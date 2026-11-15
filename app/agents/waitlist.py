from datetime import datetime, timedelta

from ..models import WaitlistEntry
from .base import Agent

HOLD_MINUTES = 15
MAX_PARTY = 6


class WaitlistAgent(Agent):
    name = "WaitlistAgent"

    def handle(self, db, m):
        if m.performative == "INFORM":
            self.on_inform(db, m)
            return None
        fn = {"join": self.join, "claim": self.claim, "sweep": self.sweep}.get(m.content.get("action"))
        return fn(db, m) if fn else self.reply(m, "FAILURE", reason="unknown action")

    # Reactive: BookingAgent tells us seats were freed (or an event was cancelled).
    def on_inform(self, db, m):
        c = m.content
        if c.get("type") == "seats_freed":
            self.process(db, m.conversation_id, c["event_id"], c["category_id"], int(c["quantity"]))
        elif c.get("type") == "event_cancelled":
            for w in db.query(WaitlistEntry).filter(WaitlistEntry.event_id == c["event_id"],
                                                    WaitlistEntry.status.in_(["waiting", "held"])):
                w.status = "expired"

    def join(self, db, m):
        c = m.content
        q = int(c["quantity"])
        if not 1 <= q <= MAX_PARTY:
            return self.reply(m, "REFUSE", reason=f"you can wait for 1 to {MAX_PARTY} seats")
        r = self.ask(db, "BookingAgent", m.conversation_id, action="availability", category_id=c["category_id"])
        if r.content.get("available", 0) >= q:
            return self.reply(m, "REFUSE", reason="seats are available now, please book directly")
        dup = db.query(WaitlistEntry).filter(WaitlistEntry.user_id == c["user_id"], WaitlistEntry.category_id == c["category_id"],
                                             WaitlistEntry.status.in_(["waiting", "held"])).first()
        if dup:
            return self.reply(m, "REFUSE", reason="you are already on this waitlist")
        w = WaitlistEntry(user_id=c["user_id"], event_id=c["event_id"], category_id=c["category_id"], quantity=q)
        db.add(w)
        db.flush()
        self.incr(db, "joined")
        return self.reply(m, "AGREE", entry_id=w.id, reason="you will be offered seats in order of joining")

    def process(self, db, cid, event_id, category_id, freed):
        """Rule: earliest waiter whose party fits the freed seats gets them, held for 15 minutes."""
        left = freed
        waiting = db.query(WaitlistEntry).filter_by(event_id=event_id, category_id=category_id, status="waiting") \
            .order_by(WaitlistEntry.created_at, WaitlistEntry.id).all()
        for w in waiting:
            if left <= 0:
                break
            if w.quantity > left:
                continue
            r = self.ask(db, "BookingAgent", cid, action="hold_seats", category_id=category_id, quantity=w.quantity)
            if r and r.performative == "AGREE":
                w.status, w.hold_expires = "held", datetime.now() + timedelta(minutes=HOLD_MINUTES)
                left -= w.quantity
                self.incr(db, "offers_made")
                self.ask(db, "NotificationAgent", cid, "INFORM", type="waitlist_offer", user_id=w.user_id,
                         event=w.event.title, minutes=HOLD_MINUTES, entry_id=w.id)

    def _expire(self, db, cid, w, notify):
        self.ask(db, "BookingAgent", cid, action="release_hold", category_id=w.category_id, quantity=w.quantity)
        w.status = "expired"
        if notify:
            self.ask(db, "NotificationAgent", cid, "INFORM", type="hold_expired", user_id=w.user_id, event=w.event.title)
        self.process(db, cid, w.event_id, w.category_id, w.quantity)  # re-offer the seats

    def claim(self, db, m):
        c = m.content
        w = db.get(WaitlistEntry, int(c["entry_id"]))
        if not w or w.user_id != c["user_id"] or w.status != "held":
            return self.reply(m, "REFUSE", reason="you have no active seat hold")
        if w.hold_expires <= datetime.now():
            self._expire(db, m.conversation_id, w, notify=True)
            return self.reply(m, "REFUSE", reason="your hold has expired and the seats were re-offered")
        r = self.ask(db, "BookingAgent", m.conversation_id, action="book", user_id=w.user_id,
                     category_id=w.category_id, quantity=w.quantity, from_hold=True)
        if r.performative == "AGREE":
            w.status = "claimed"
            self.incr(db, "claimed")
            return self.reply(m, "AGREE", booking_id=r.content["booking_id"])
        self._expire(db, m.conversation_id, w, notify=False)
        return self.reply(m, "REFUSE", reason=r.content.get("reason", "booking failed"))

    # Proactive: called by the scheduler every 30 s, with no user action.
    def sweep(self, db, m):
        n = 0
        for w in db.query(WaitlistEntry).filter(WaitlistEntry.status == "held", WaitlistEntry.hold_expires <= datetime.now()).all():
            self._expire(db, m.conversation_id, w, notify=True)
            n += 1
        if n:
            self.incr(db, "holds_expired", n)
        return self.reply(m, "INFORM", expired=n)
