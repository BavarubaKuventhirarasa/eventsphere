import os
import tempfile
import uuid

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SECRET_KEY"] = "test-secret"

from datetime import datetime, timedelta  # noqa: E402

import pytest  # noqa: E402

import app.agents  # noqa: E402,F401  (registers the agents)
from app import bus  # noqa: E402
from app import models as M  # noqa: E402
from app.auth import hash_pw  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def make_user(db):
    def f(role="attendee"):
        u = M.User(email=f"{uuid.uuid4().hex[:8]}@t.lk", name="Test " + role,
                   password_hash=hash_pw("Passw0rd!"), role=role)
        db.add(u)
        db.commit()
        return u
    return f


@pytest.fixture
def make_venue(db):
    def f(capacity=100, hire=True, rate=1000):
        v = M.Venue(name="Hall " + uuid.uuid4().hex[:4], location="Campus", capacity=capacity,
                    hourly_rate=rate, open_for_hire=hire)
        db.add(v)
        db.commit()
        return v
    return f


@pytest.fixture
def make_event(db, make_venue):
    def f(venue=None, start=None, hours=2, qty=10, price=1000.0, status="published"):
        venue = venue or make_venue()
        start = start or datetime.now() + timedelta(days=10)
        ev = M.Event(title="Test Event", category="Music", venue_id=venue.id, start=start,
                     end=start + timedelta(hours=hours), status=status)
        ev.categories = [M.TicketCategory(name="Standard", price=price, quantity=qty)]
        db.add(ev)
        db.commit()
        return ev
    return f


@pytest.fixture
def ask(db):
    """Send a REQUEST to an agent through the bus and commit, like a web route does."""
    def f(to, **content):
        r = bus.request(db, "Test", to, **content)
        db.commit()
        return r
    return f
