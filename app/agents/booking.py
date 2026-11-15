import secrets
from datetime import datetime

from sqlalchemy import update

from ..models import Booking, Event, TicketCategory as T
from .base import Agent

MAX_PER_REQUEST = 6
MAX_PER_USER_PER_EVENT = 6


def refund_rate(days_before):
    """Refund policy: >=7 days before: 100%; 2 to 7 days: 50%; under 48 h: 0%."""
    return 1.0 if days_before >= 7 else 0.5 if days_before >= 2 else 0.0


class BookingAgent(Agent):
    name = "BookingAgent"

    def handle(self, db, m):
        if m.performative != "REQUEST":
            return super().handle(db, m)
        fn = {"book": self.book, "cancel": self.cancel, "cancel_event": self.cancel_event,
              "hold_seats": self.hold_seats, "release_hold": self.release_hold,
              "availability": self.availability}.get(m.content.get("action"))
        return fn(db, m) if fn else self.reply(m, "FAILURE", reason="unknown action")

    # Seat counters are changed ONLY by one atomic conditional UPDATE. The WHERE clause is the
    # capacity check, so two simultaneous requests for the last seat cannot both succeed.
    def _run(self, db, stmt):
        return db.execute(stmt.execution_options(synchronize_session=False)).rowcount

    def _take(self, db, cat_id, q, from_hold):
        if from_hold:
            return self._run(db, update(T).where(T.id == cat_id, T.held >= q).values(sold=T.sold + q, held=T.held - q)) == 1
        return self._run(db, update(T).where(T.id == cat_id, T.sold + T.held + q <= T.quantity).values(sold=T.sold + q)) == 1

    def availability(self, db, m):
        cat = db.get(T, int(m.content["category_id"]))
        db.refresh(cat)
        return self.reply(m, "AGREE", available=cat.available)

    def hold_seats(self, db, m):
        cat_id, q = int(m.content["category_id"]), int(m.content["quantity"])
        ok = self._run(db, update(T).where(T.id == cat_id, T.sold + T.held + q <= T.quantity).values(held=T.held + q)) == 1
        db.refresh(db.get(T, cat_id))
        return self.reply(m, "AGREE" if ok else "REFUSE", reason="seats held" if ok else "seats no longer free")

    def release_hold(self, db, m):
        cat_id, q = int(m.content["category_id"]), int(m.content["quantity"])
        self._run(db, update(T).where(T.id == cat_id, T.held >= q).values(held=T.held - q))
        db.refresh(db.get(T, cat_id))
        return self.reply(m, "AGREE", reason="hold released")

    def book(self, db, m):
        c = m.content
        q, uid, from_hold = int(c["quantity"]), c["user_id"], c.get("from_hold", False)
        cat = db.get(T, int(c["category_id"]))

        def no(reason, **extra):
            self.incr(db, "bookings_refused")
            return self.reply(m, "REFUSE", reason=reason, **extra)

        if not cat:
            return no("ticket category not found")
        ev = cat.event
        if q < 1 or q > MAX_PER_REQUEST:
            return no(f"you can book 1 to {MAX_PER_REQUEST} tickets per request")
        if ev.status != "published":
            return no("this event is not open for booking")
        if ev.start <= datetime.now():
            return no("this event has already started")
        have = sum(b.quantity for b in db.query(Booking).filter_by(user_id=uid, event_id=ev.id, status="confirmed"))
        if have + q > MAX_PER_USER_PER_EVENT:
            return no(f"limit is {MAX_PER_USER_PER_EVENT} tickets per person per event (you already hold {have})")
        if not self._take(db, cat.id, q, from_hold):
            db.refresh(cat)
            return no(f"only {cat.available} ticket(s) left in '{cat.name}'", sold_out=cat.available == 0, available=cat.available)
        db.refresh(cat)

        # Simulated payment: no real card or bank is involved, only a generated reference.
        ref = "SIM-" + secrets.token_hex(5).upper()
        b = Booking(user_id=uid, event_id=ev.id, category_id=cat.id, quantity=q, amount=round(cat.price * q, 2),
                    payment_ref=ref, status="confirmed")
        db.add(b)
        db.flush()
        r = self.ask(db, "TicketAgent", m.conversation_id, action="issue", booking_id=b.id, event_id=ev.id, quantity=q)
        if r is None or r.performative != "AGREE":
            raise RuntimeError("TicketAgent could not issue tickets")  # request is rolled back by the caller
        self.incr(db, "bookings_confirmed")
        self.incr(db, "tickets_sold", q)
        self.ask(db, "NotificationAgent", m.conversation_id, "INFORM", type="booking_confirmed", user_id=uid,
                 booking_id=b.id, event=ev.title, quantity=q, amount=b.amount, ref=ref)
        return self.reply(m, "AGREE", booking_id=b.id, payment_ref=ref, amount=b.amount, codes=r.content["codes"])

    def cancel(self, db, m):
        c = m.content
        b = db.get(Booking, int(c["booking_id"]))
        if not b or b.user_id != c["user_id"]:
            return self.reply(m, "REFUSE", reason="booking not found")
        if b.status != "confirmed":
            return self.reply(m, "REFUSE", reason="booking is already cancelled")
        now = datetime.now()
        if b.event.start <= now:
            return self.reply(m, "REFUSE", reason="the event has already started")
        if any(t.status == "used" for t in b.tickets):
            return self.reply(m, "REFUSE", reason="tickets have already been used for check-in")
        days = (b.event.start - now).total_seconds() / 86400
        rate = refund_rate(days)
        refund = round(b.amount * rate, 2)
        b.status, b.refund = "cancelled", refund
        self._run(db, update(T).where(T.id == b.category_id, T.sold >= b.quantity).values(sold=T.sold - b.quantity))
        db.refresh(db.get(T, b.category_id))
        self.ask(db, "TicketAgent", m.conversation_id, action="cancel_tickets", booking_id=b.id)
        self.incr(db, "refunds_total", refund)
        self.ask(db, "NotificationAgent", m.conversation_id, "INFORM", type="booking_cancelled", user_id=b.user_id,
                 booking_id=b.id, refund=refund, percent=int(rate * 100))
        self.ask(db, "WaitlistAgent", m.conversation_id, "INFORM", type="seats_freed", event_id=b.event_id,
                 category_id=b.category_id, quantity=b.quantity)
        return self.reply(m, "AGREE", refund=refund, percent=int(rate * 100))

    def cancel_event(self, db, m):
        ev = db.get(Event, int(m.content["event_id"]))
        if not ev or ev.status == "cancelled":
            return self.reply(m, "REFUSE", reason="event not found or already cancelled")
        ev.status = "cancelled"
        total = 0.0
        for b in db.query(Booking).filter_by(event_id=ev.id, status="confirmed").all():
            b.status, b.refund = "cancelled", b.amount  # full refund when the organiser cancels
            total += b.amount
            self.ask(db, "TicketAgent", m.conversation_id, action="cancel_tickets", booking_id=b.id)
            self.ask(db, "NotificationAgent", m.conversation_id, "INFORM", type="event_cancelled", user_id=b.user_id,
                     booking_id=b.id, event=ev.title, refund=b.amount)
        self._run(db, update(T).where(T.event_id == ev.id).values(sold=0, held=0))
        self.ask(db, "WaitlistAgent", m.conversation_id, "INFORM", type="event_cancelled", event_id=ev.id)
        self.incr(db, "refunds_total", total)
        return self.reply(m, "AGREE", refunded=total)
