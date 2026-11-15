from datetime import datetime, timedelta

from app import models as M


def test_waitlist_hold_expiry_sweep_and_reoffer(db, make_user, make_event, ask):
    ev = make_event(qty=1)
    cat = ev.categories[0]
    a, b, c = make_user(), make_user(), make_user()
    bk = ask("BookingAgent", action="book", user_id=a.id, category_id=cat.id, quantity=1)
    wb = ask("WaitlistAgent", action="join", user_id=b.id, event_id=ev.id, category_id=cat.id, quantity=1)
    wc = ask("WaitlistAgent", action="join", user_id=c.id, event_id=ev.id, category_id=cat.id, quantity=1)
    assert wb.performative == wc.performative == "AGREE"

    ask("BookingAgent", action="cancel", booking_id=bk.content["booking_id"], user_id=a.id)  # frees the seat
    eb, ec = db.get(M.WaitlistEntry, wb.content["entry_id"]), db.get(M.WaitlistEntry, wc.content["entry_id"])
    db.refresh(eb), db.refresh(ec), db.refresh(cat)
    assert eb.status == "held" and ec.status == "waiting" and cat.held == 1  # earliest waiter gets it
    outsider = ask("BookingAgent", action="book", user_id=make_user().id, category_id=cat.id, quantity=1)
    assert outsider.performative == "REFUSE"  # held seats are not for sale

    eb.hold_expires = datetime.now() - timedelta(minutes=1)  # the timer fires with no user action
    db.commit()
    r = ask("WaitlistAgent", action="sweep")
    db.refresh(eb), db.refresh(ec)
    assert r.content["expired"] == 1 and eb.status == "expired" and ec.status == "held"

    claim = ask("WaitlistAgent", action="claim", entry_id=ec.id, user_id=c.id)
    assert claim.performative == "AGREE"
    db.refresh(cat)
    assert cat.sold == 1 and cat.held == 0


def test_cannot_join_waitlist_when_seats_available(db, make_user, make_event, ask):
    ev = make_event(qty=5)
    r = ask("WaitlistAgent", action="join", user_id=make_user().id, event_id=ev.id, category_id=ev.categories[0].id, quantity=1)
    assert r.performative == "REFUSE"


def test_notification_agent_reacts_to_booking(db, make_user, make_event, ask):
    ev = make_event()
    u = make_user()
    ask("BookingAgent", action="book", user_id=u.id, category_id=ev.categories[0].id, quantity=2)
    notes = db.query(M.Notification).filter_by(user_id=u.id).all()
    assert len(notes) == 1 and "confirmed" in notes[0].text


def test_one_user_action_is_traceable_across_agents(db, make_user, make_event, ask):
    ev = make_event()
    r = ask("BookingAgent", action="book", user_id=make_user().id, category_id=ev.categories[0].id, quantity=1)
    rows = db.query(M.MessageLog).filter_by(conversation_id=r.conversation_id).all()
    assert {"BookingAgent", "TicketAgent", "NotificationAgent"} <= {m.sender for m in rows} | {m.receiver for m in rows}
    assert {"REQUEST", "AGREE", "INFORM"} <= {m.performative for m in rows}


def test_agent_beliefs_persist_in_database(db, make_user, make_event, ask):
    ev = make_event()
    ask("BookingAgent", action="book", user_id=make_user().id, category_id=ev.categories[0].id, quantity=3)
    row = db.get(M.AgentState, ("BookingAgent", "tickets_sold"))
    assert row is not None and row.value == "3"
