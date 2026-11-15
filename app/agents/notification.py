from ..models import Notification, User
from .base import Agent


def compose(t, c):
    return {
        "booking_confirmed": f"Booking #{c.get('booking_id')} confirmed: {c.get('quantity')} x {c.get('event')}. Payment ref {c.get('ref')}, LKR {c.get('amount')}.",
        "booking_cancelled": f"Booking #{c.get('booking_id')} cancelled. Refund LKR {c.get('refund')} ({c.get('percent')}% under the refund policy).",
        "event_cancelled": f"'{c.get('event')}' was cancelled by the organiser. Full refund of LKR {c.get('refund')} issued for booking #{c.get('booking_id')}.",
        "waitlist_offer": f"Seats are now available for '{c.get('event')}'. Claim them within {c.get('minutes')} minutes from My Bookings.",
        "hold_expired": f"Your seat hold for '{c.get('event')}' expired and the seats were offered to the next person.",
        "hire_confirmed": f"Venue hire #{c.get('hire_id')} confirmed: {c.get('venue')} on {c.get('start')}. Price LKR {c.get('price')}.",
        "hire_pending": f"Venue hire #{c.get('hire_id')} ({c.get('venue')}, {c.get('start')}) is waiting for organiser approval.",
        "hire_decision": f"Venue hire #{c.get('hire_id')} ({c.get('venue')}, {c.get('start')}) was {c.get('status')}.",
    }.get(t)


class NotificationAgent(Agent):
    name = "NotificationAgent"

    # Reactive: listens to direct INFORMs and to every broadcast.
    def on_inform(self, db, m):
        c, t = m.content, m.content.get("type")
        text = compose(t, c)
        if not text:
            return
        if c.get("user_id"):
            db.add(Notification(user_id=c["user_id"], text=text))
            self.incr(db, "sent")
        if t == "hire_pending":  # the admin needs to know too
            for a in db.query(User).filter_by(role="admin"):
                db.add(Notification(user_id=a.id, text=f"ACTION: hire request #{c.get('hire_id')} for {c.get('venue')} needs approval."))
                self.incr(db, "sent")
        db.flush()
