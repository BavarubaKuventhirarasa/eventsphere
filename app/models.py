from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .db import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="attendee")  # admin | staff | attendee


class Venue(Base):
    __tablename__ = "venues"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    location = Column(String, default="")
    capacity = Column(Integer, nullable=False)
    description = Column(Text, default="")
    image_url = Column(String, default="")
    hourly_rate = Column(Float, default=0)
    open_for_hire = Column(Boolean, default=False)


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    category = Column(String, default="General")
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    description = Column(Text, default="")
    status = Column(String, default="draft")  # draft | published | cancelled
    venue = relationship("Venue")
    categories = relationship("TicketCategory", back_populates="event", order_by="TicketCategory.id")


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    sold = Column(Integer, nullable=False, default=0)
    held = Column(Integer, nullable=False, default=0)  # seats reserved for waitlist holds
    event = relationship("Event", back_populates="categories")

    @property
    def available(self):
        return max(self.quantity - (self.sold or 0) - (self.held or 0), 0)


class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("ticket_categories.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    refund = Column(Float, nullable=False, default=0)
    payment_ref = Column(String)
    status = Column(String, default="confirmed")  # confirmed | cancelled
    created_at = Column(DateTime, default=datetime.now)
    user = relationship("User")
    event = relationship("Event")
    category = relationship("TicketCategory")
    tickets = relationship("Ticket", back_populates="booking")


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    code = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="valid")  # valid | used | cancelled
    used_at = Column(DateTime)
    booking = relationship("Booking", back_populates="tickets")
    event = relationship("Event")


class HireRequest(Base):
    __tablename__ = "hire_requests"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    venue_id = Column(Integer, ForeignKey("venues.id"), nullable=False)
    start = Column(DateTime, nullable=False)
    end = Column(DateTime, nullable=False)
    purpose = Column(String, default="")
    guests = Column(Integer, nullable=False)
    price = Column(Float, default=0)
    status = Column(String, default="pending")  # confirmed | pending | rejected
    note = Column(String, default="")
    created_at = Column(DateTime, default=datetime.now)
    user = relationship("User")
    venue = relationship("Venue")


class WaitlistEntry(Base):
    __tablename__ = "waitlist"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("ticket_categories.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, default="waiting")  # waiting | held | claimed | expired
    hold_expires = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    user = relationship("User")
    event = relationship("Event")
    category = relationship("TicketCategory")


class MessageLog(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(String, index=True)
    sender = Column(String)
    receiver = Column(String)
    performative = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.now)


class AgentState(Base):  # persistent beliefs of every agent
    __tablename__ = "agent_state"
    agent = Column(String, primary_key=True)
    key = Column(String, primary_key=True)
    value = Column(Text)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    text = Column(String)
    created_at = Column(DateTime, default=datetime.now)
