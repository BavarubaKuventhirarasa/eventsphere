from datetime import datetime, timedelta

from ..models import Event, HireRequest, Venue
from .base import Agent

BUFFER = timedelta(minutes=30)   # turnaround time between two activities
MAX_AUTO_HOURS = 4               # longer hires are escalated to the admin
AUTO_CAPACITY_SHARE = 0.8        # hires above 80% of capacity are escalated
WEEKEND_MULTIPLIER = 1.25        # Sat/Sun pricing


def iso(d):
    return datetime.fromisoformat(d) if isinstance(d, str) else d


class VenueAgent(Agent):
    name = "VenueAgent"

    def handle(self, db, m):
        if m.performative != "REQUEST":
            return super().handle(db, m)
        fn = {"check_slot": self.check_slot, "hire": self.hire, "decide_hire": self.decide}.get(m.content.get("action"))
        return fn(db, m) if fn else self.reply(m, "FAILURE", reason="unknown action")

    # ---- the calendar belief ----
    def clash(self, db, venue_id, start, end, exclude_event=None, exclude_hire=None):
        for e in db.query(Event).filter(Event.venue_id == venue_id, Event.status != "cancelled"):
            if e.id != exclude_event and e.start < end + BUFFER and e.end + BUFFER > start:
                return f"overlaps event '{e.title}' ({e.start:%d %b %H:%M}-{e.end:%H:%M}) including a 30 minute turnaround buffer"
        for h in db.query(HireRequest).filter(HireRequest.venue_id == venue_id, HireRequest.status.in_(["confirmed", "pending"])):
            if h.id != exclude_hire and h.start < end + BUFFER and h.end + BUFFER > start:
                return f"overlaps a private hire ({h.start:%d %b %H:%M}-{h.end:%H:%M}) including a 30 minute turnaround buffer"
        return None

    def price(self, venue, start, end):
        hours = (end - start).total_seconds() / 3600
        return round(hours * venue.hourly_rate * (WEEKEND_MULTIPLIER if start.weekday() >= 5 else 1), 2)

    def _alt(self, v, s, e):
        return {"venue_id": v.id, "venue": v.name, "start": s.isoformat(timespec="minutes"),
                "end": e.isoformat(timespec="minutes"), "price": self.price(v, s, e)}

    def alternatives(self, db, v, s, e, guests, same_venue=True):
        out, dur = [], e - s
        if same_venue:
            for h in (1, 2, 3, 4, -1, -2, -3, 5, 6):
                ns = s + timedelta(hours=h)
                ne = ns + dur
                if ns <= datetime.now() or ns.hour < 7 or ne.date() != ns.date():
                    continue
                if not self.clash(db, v.id, ns, ne):
                    out.append(self._alt(v, ns, ne))
                if len(out) >= 2:
                    break
        for o in db.query(Venue).filter(Venue.open_for_hire.is_(True), Venue.id != v.id, Venue.capacity >= guests):
            if not self.clash(db, o.id, s, e):
                out.append(self._alt(o, s, e))
            if len(out) >= 4:
                break
        return out

    # ---- decisions ----
    def check_slot(self, db, m):
        c = m.content
        v = db.get(Venue, int(c["venue_id"]))
        s, e = iso(c["start"]), iso(c["end"])
        if not v:
            return self.reply(m, "REFUSE", reason="venue not found")
        if e <= s:
            return self.reply(m, "REFUSE", reason="end time must be after start time")
        tickets = int(c.get("tickets", 0))
        if tickets > v.capacity:
            self.incr(db, "slots_refused")
            return self.reply(m, "REFUSE", reason=f"total tickets ({tickets}) exceed venue capacity ({v.capacity})")
        why = self.clash(db, v.id, s, e, exclude_event=c.get("exclude_event_id"))
        if why:
            self.incr(db, "slots_refused")
            return self.reply(m, "REFUSE", reason=why)
        self.incr(db, "slots_accepted")
        return self.reply(m, "AGREE", reason="slot is free")

    def hire(self, db, m):
        c = m.content
        v = db.get(Venue, int(c["venue_id"]))
        s, e, guests = iso(c["start"]), iso(c["end"]), int(c["guests"])

        def no(reason, alts):
            self.incr(db, "hires_refused")
            return self.reply(m, "PROPOSE" if alts else "REFUSE", reason=reason, alternatives=alts)

        if not v:
            return self.reply(m, "REFUSE", reason="venue not found", alternatives=[])
        if e <= s:
            return self.reply(m, "REFUSE", reason="end time must be after start time", alternatives=[])
        if s <= datetime.now():
            return self.reply(m, "REFUSE", reason="start time must be in the future", alternatives=[])
        if guests < 1:
            return self.reply(m, "REFUSE", reason="guest count must be at least 1", alternatives=[])
        if not v.open_for_hire:
            return no(f"{v.name} is not open for private hire", self.alternatives(db, v, s, e, guests, same_venue=False))
        if guests > v.capacity:
            return no(f"{guests} guests exceed the capacity of {v.name} ({v.capacity})",
                      self.alternatives(db, v, s, e, guests, same_venue=False))
        why = self.clash(db, v.id, s, e)
        if why:
            return no(f"{v.name} is not free: {why}", self.alternatives(db, v, s, e, guests))

        hours = (e - s).total_seconds() / 3600
        price = self.price(v, s, e)
        weekend = " (weekend rate x1.25)" if s.weekday() >= 5 else ""
        reasons = []
        if hours > MAX_AUTO_HOURS:
            reasons.append(f"duration {hours:g} h is over the {MAX_AUTO_HOURS} h automatic limit")
        if guests > AUTO_CAPACITY_SHARE * v.capacity:
            reasons.append(f"{guests} guests is over {int(AUTO_CAPACITY_SHARE * 100)}% of capacity")
        status = "pending" if reasons else "confirmed"
        h = HireRequest(user_id=c["user_id"], venue_id=v.id, start=s, end=e, purpose=c.get("purpose", ""),
                        guests=guests, price=price, status=status, note="; ".join(reasons))
        db.add(h)
        db.flush()
        self.incr(db, "hires_" + status)
        self.ask(db, "*", m.conversation_id, "INFORM", type="hire_" + status, user_id=c["user_id"], hire_id=h.id,
                 venue=v.name, start=s.strftime("%d %b %H:%M"), price=price)
        if status == "pending":
            return self.reply(m, "INFORM", decision="escalated", hire_id=h.id, price=price,
                              reason="sent to the admin for approval: " + "; ".join(reasons))
        return self.reply(m, "AGREE", decision="confirmed", hire_id=h.id, price=price,
                          reason=f"{hours:g} h x LKR {v.hourly_rate:,.0f}{weekend} = LKR {price:,.2f}")

    def decide(self, db, m):
        c = m.content
        h = db.get(HireRequest, int(c["hire_id"]))
        if not h or h.status != "pending":
            return self.reply(m, "REFUSE", reason="request is not pending")
        if c.get("approve"):
            why = self.clash(db, h.venue_id, h.start, h.end, exclude_hire=h.id)
            if why:
                return self.reply(m, "REFUSE", reason="cannot approve, slot now taken: " + why)
            h.status = "confirmed"
        else:
            h.status = "rejected"
            h.note = c.get("reason") or "rejected by the organiser"
        self.ask(db, "*", m.conversation_id, "INFORM", type="hire_decision", user_id=h.user_id, hire_id=h.id,
                 status=h.status, venue=h.venue.name, start=h.start.strftime("%d %b %H:%M"), price=h.price)
        return self.reply(m, "AGREE", status=h.status)
