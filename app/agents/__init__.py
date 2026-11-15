from .. import bus
from .booking import BookingAgent
from .notification import NotificationAgent
from .ticket import TicketAgent
from .venue import VenueAgent
from .waitlist import WaitlistAgent

for _cls in (VenueAgent, BookingAgent, TicketAgent, WaitlistAgent, NotificationAgent):
    bus.register(_cls())
