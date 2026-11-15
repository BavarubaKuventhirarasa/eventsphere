import secrets
from datetime import datetime, timedelta

from ..models import Ticket
from .base import Agent

CHECKIN_OPENS_BEFORE = timedelta(hours=2)
SUSPICIOUS_AFTER = 2  # this many re-scans of one used ticket raises a flag (shared screenshot?)


class TicketAgent(Agent):
    name = "TicketAgent"

    def handle(self, db, m):
        if m.performative != "REQUEST":
            return super().handle(db, m)
        fn = {"issue": self.issue, "cancel_tickets": self.cancel_tickets, "scan": self.scan}.get(m.content.get("action"))
        return fn(db, m) if fn else self.reply(m, "FAILURE", reason="unknown action")

    def issue(self, db, m):
        c = m.content
        codes = []
        for _ in range(int(c["quantity"])):
            code = secrets.token_urlsafe(24)  # 192 random bits: unguessable
            db.add(Ticket(booking_id=c["booking_id"], event_id=c["event_id"], code=code))
            codes.append(code)
        db.flush()
        self.incr(db, "issued", len(codes))
        return self.reply(m, "AGREE", codes=codes)

    def cancel_tickets(self, db, m):
        n = 0
        for t in db.query(Ticket).filter_by(booking_id=m.content["booking_id"], status="valid"):
            t.status = "cancelled"
            n += 1
        return self.reply(m, "AGREE", cancelled=n)

    def scan(self, db, m):
        code, event_id = str(m.content["code"]).strip(), int(m.content["event_id"])
        t = db.query(Ticket).filter_by(code=code).first()

        def refuse(result, reason, **extra):
            self.incr(db, "rejected_" + result)
            return self.reply(m, "REFUSE", result=result, reason=reason, **extra)

        if not t:
            return refuse("UNKNOWN", "no ticket with this code")
        if t.event_id != event_id:
            return refuse("WRONG_EVENT", f"this ticket is for '{t.event.title}', not the selected event")
        if t.status == "cancelled" or t.event.status == "cancelled":
            return refuse("CANCELLED", "this ticket was cancelled")
        if t.status == "used":
            n = self.incr(db, f"rescans:{code}")
            flagged = n >= SUSPICIOUS_AFTER
            if flagged and code not in self.get(db, "flagged", []):
                self.put(db, "flagged", self.get(db, "flagged", []) + [code])
            return refuse("ALREADY_USED", f"already admitted at {t.used_at:%H:%M}" +
                          (f" - SUSPICIOUS: scanned {n} more times, possible shared screenshot" if flagged else ""),
                          suspicious=flagged)
        now = datetime.now()
        if not (t.event.start - CHECKIN_OPENS_BEFORE <= now <= t.event.end):
            return refuse("OUTSIDE_TIME", f"check-in is open from {(t.event.start - CHECKIN_OPENS_BEFORE):%d %b %H:%M} until the event ends")
        t.status, t.used_at = "used", now
        self.incr(db, "admitted")
        return self.reply(m, "AGREE", result="ADMIT", reason="welcome", holder=t.booking.user.name, category=t.booking.category.name)
