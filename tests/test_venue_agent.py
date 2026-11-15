from datetime import datetime, timedelta

from app import models as M


def day_at(weekday, hour=10, days_ahead=8):
    d = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def test_event_overlap_refused_with_buffer_and_reason(db, make_event, ask):
    ev = make_event()
    r = ask("VenueAgent", action="check_slot", venue_id=ev.venue_id,
            start=(ev.end + timedelta(minutes=10)).isoformat(), end=(ev.end + timedelta(hours=2)).isoformat())
    assert r.performative == "REFUSE" and "buffer" in r.content["reason"]
    ok = ask("VenueAgent", action="check_slot", venue_id=ev.venue_id,
             start=(ev.end + timedelta(minutes=30)).isoformat(), end=(ev.end + timedelta(hours=2)).isoformat())
    assert ok.performative == "AGREE"


def test_tickets_over_capacity_refused(db, make_venue, ask):
    v = make_venue(capacity=50)
    s = datetime.now() + timedelta(days=5)
    r = ask("VenueAgent", action="check_slot", venue_id=v.id, start=s.isoformat(),
            end=(s + timedelta(hours=2)).isoformat(), tickets=51)
    assert r.performative == "REFUSE" and "capacity" in r.content["reason"]


def test_routine_hire_confirmed_with_weekday_and_weekend_price(db, make_venue, make_user, ask):
    v, u = make_venue(capacity=100, rate=1000), make_user()
    wd, we = day_at(1), day_at(5)
    r1 = ask("VenueAgent", action="hire", user_id=u.id, venue_id=v.id, start=wd.isoformat(),
             end=(wd + timedelta(hours=2)).isoformat(), guests=10)
    r2 = ask("VenueAgent", action="hire", user_id=u.id, venue_id=v.id, start=we.isoformat(),
             end=(we + timedelta(hours=2)).isoformat(), guests=10)
    assert r1.performative == "AGREE" and r1.content["price"] == 2000
    assert r2.performative == "AGREE" and r2.content["price"] == 2500  # weekend x1.25
    assert db.get(M.HireRequest, r1.content["hire_id"]).status == "confirmed"


def test_clash_returns_proposal_with_alternatives(db, make_venue, make_user, ask):
    v, other, u = make_venue(), make_venue(), make_user()
    s = day_at(2)
    ask("VenueAgent", action="hire", user_id=u.id, venue_id=v.id, start=s.isoformat(),
        end=(s + timedelta(hours=2)).isoformat(), guests=10)
    r = ask("VenueAgent", action="hire", user_id=u.id, venue_id=v.id, start=(s + timedelta(hours=1)).isoformat(),
            end=(s + timedelta(hours=3)).isoformat(), guests=10)
    assert r.performative == "PROPOSE" and r.content["reason"]
    alts = r.content["alternatives"]
    assert alts and all({"venue", "start", "end", "price"} <= set(a) for a in alts)
    assert any(a["venue_id"] == other.id for a in alts)  # a different venue is offered too


def test_long_hire_escalated_then_admin_approves(db, make_venue, make_user, ask):
    v, u = make_venue(), make_user()
    s = day_at(3)
    r = ask("VenueAgent", action="hire", user_id=u.id, venue_id=v.id, start=s.isoformat(),
            end=(s + timedelta(hours=6)).isoformat(), guests=10)
    assert r.performative == "INFORM" and r.content["decision"] == "escalated"
    d = ask("VenueAgent", action="decide_hire", hire_id=r.content["hire_id"], approve=True)
    assert d.performative == "AGREE" and db.get(M.HireRequest, r.content["hire_id"]).status == "confirmed"
