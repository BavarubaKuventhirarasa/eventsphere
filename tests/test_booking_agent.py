import json
from datetime import datetime, timedelta

from app import models as M


def book(ask, user, cat, q=1):
    return ask("BookingAgent", action="book", user_id=user.id, category_id=cat.id, quantity=q)


def test_more_than_six_tickets_refused(db, make_user, make_event, ask):
    ev = make_event(qty=50)
    r = book(ask, make_user(), ev.categories[0], 7)
    assert r.performative == "REFUSE" and "6" in r.content["reason"]


def test_never_oversells_last_seat(db, make_user, make_event, ask):
    ev = make_event(qty=1)
    cat = ev.categories[0]
    a, b = make_user(), make_user()
    assert book(ask, a, cat).performative == "AGREE"
    r = book(ask, b, cat)
    assert r.performative == "REFUSE" and r.content["sold_out"] is True
    db.refresh(cat)
    assert cat.sold == 1 and db.query(M.Ticket).count() == 1


def test_booking_issues_unique_unguessable_codes(db, make_user, make_event, ask):
    ev = make_event(qty=10)
    r = book(ask, make_user(), ev.categories[0], 4)
    codes = r.content["codes"]
    assert len(set(codes)) == 4 and all(len(c) >= 30 for c in codes) and r.content["payment_ref"].startswith("SIM-")


def _cancel_refund(db, make_user, make_event, ask, days):
    ev = make_event(start=datetime.now() + timedelta(days=days), price=1000)
    u = make_user()
    b = book(ask, u, ev.categories[0], 2)
    r = ask("BookingAgent", action="cancel", booking_id=b.content["booking_id"], user_id=u.id)
    assert r.performative == "AGREE"
    return r.content["refund"]


def test_refund_policy(db, make_user, make_event, ask):
    assert _cancel_refund(db, make_user, make_event, ask, 10) == 2000   # 7+ days: 100%
    assert _cancel_refund(db, make_user, make_event, ask, 3) == 1000    # 2-7 days: 50%
    assert _cancel_refund(db, make_user, make_event, ask, 1) == 0       # under 48h: 0%


def test_event_cancellation_refunds_everyone_in_full(db, make_user, make_event, ask):
    ev = make_event(qty=10, price=500)
    users = [make_user(), make_user()]
    for u in users:
        book(ask, u, ev.categories[0], 2)
    r = ask("BookingAgent", action="cancel_event", event_id=ev.id)
    assert r.performative == "AGREE" and r.content["refunded"] == 2000
    for b in db.query(M.Booking).all():
        assert b.status == "cancelled" and b.refund == b.amount
    assert db.query(M.Ticket).filter_by(status="valid").count() == 0
    assert db.query(M.Notification).filter(M.Notification.text.like("%cancelled by the organiser%")).count() == 2


def test_cancelled_event_cannot_be_booked(db, make_user, make_event, ask):
    ev = make_event(status="cancelled")
    assert book(ask, make_user(), ev.categories[0]).performative == "REFUSE"
