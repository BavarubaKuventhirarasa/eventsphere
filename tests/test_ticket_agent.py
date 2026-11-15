from datetime import datetime, timedelta


def booked(ask, make_user, make_event, **kw):
    ev = make_event(**kw)
    u = make_user()
    r = ask("BookingAgent", action="book", user_id=u.id, category_id=ev.categories[0].id, quantity=1)
    return ev, u, r.content["codes"][0], r.content["booking_id"]


def scan(ask, code, event_id):
    return ask("TicketAgent", action="scan", code=code, event_id=event_id)


def test_double_checkin_rejected_and_flagged(db, make_user, make_event, ask):
    ev, _, code, _ = booked(ask, make_user, make_event, start=datetime.now() + timedelta(minutes=30))
    assert scan(ask, code, ev.id).content["result"] == "ADMIT"
    second = scan(ask, code, ev.id)
    assert second.performative == "REFUSE" and second.content["result"] == "ALREADY_USED"
    third = scan(ask, code, ev.id)
    assert third.content["suspicious"] is True  # repeated scans of one used ticket


def test_ticket_for_another_event_rejected(db, make_user, make_event, ask):
    ev, _, code, _ = booked(ask, make_user, make_event, start=datetime.now() + timedelta(minutes=30))
    other = make_event(start=datetime.now() + timedelta(minutes=30))
    r = scan(ask, code, other.id)
    assert r.content["result"] == "WRONG_EVENT"


def test_cancelled_ticket_rejected(db, make_user, make_event, ask):
    ev, u, code, bid = booked(ask, make_user, make_event, start=datetime.now() + timedelta(minutes=30))
    ask("BookingAgent", action="cancel", booking_id=bid, user_id=u.id)
    assert scan(ask, code, ev.id).content["result"] == "CANCELLED"


def test_checkin_outside_time_window_and_unknown_code(db, make_user, make_event, ask):
    ev, _, code, _ = booked(ask, make_user, make_event)  # event is 10 days away
    assert scan(ask, code, ev.id).content["result"] == "OUTSIDE_TIME"
    assert scan(ask, "not-a-real-code", ev.id).content["result"] == "UNKNOWN"
