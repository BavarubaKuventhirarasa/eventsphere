import asyncio
import io
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import qrcode
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, or_
from starlette.middleware.sessions import SessionMiddleware

from . import agents  # noqa: F401  (importing registers the five agents on the bus)
from . import bus
from .auth import check_pw, csrf_token, get_db, get_user, hash_pw, require, verify_csrf
from .bus import Message
from .db import Base, SessionLocal, engine
from .models import (AgentState, Booking, Event, HireRequest, MessageLog, Notification, Ticket,
                     TicketCategory, User, Venue, WaitlistEntry)

SWEEP_SECONDS = int(os.getenv("SWEEP_SECONDS", "30"))


def run_sweep():
    with SessionLocal() as db:
        bus.request(db, "Scheduler", "WaitlistAgent", action="sweep")
        db.commit()


async def sweeper():
    """Proactive behaviour: the WaitlistAgent expires stale holds on a timer, no user needed."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await asyncio.to_thread(run_sweep)
        except Exception as exc:  # keep the timer alive
            print("sweep failed:", exc)


def seed():
    with SessionLocal() as db:
        if db.query(User).count():
            return
        db.add_all([
            User(name="Admin", email="admin@eventsphere.lk", password_hash=hash_pw("Admin#2026"), role="admin"),
            User(name="Gate Staff", email="staff@eventsphere.lk", password_hash=hash_pw("Staff#2026"), role="staff"),
            User(name="Demo Attendee", email="attendee@eventsphere.lk", password_hash=hash_pw("Attendee#2026"), role="attendee"),
        ])
        v1 = Venue(name="Main Auditorium", location="Campus, Block A", capacity=200, hourly_rate=5000, open_for_hire=True,
                   description="Large hall with stage and sound system.")
        v2 = Venue(name="Seminar Room B", location="Campus, Block B", capacity=40, hourly_rate=1500, open_for_hire=True,
                   description="Small room for talks and club meetings.")
        db.add_all([v1, v2])
        db.flush()
        start = (datetime.now() + timedelta(days=10)).replace(hour=18, minute=0, second=0, microsecond=0)
        ev = Event(title="Welcome Concert", category="Concert", venue_id=v1.id, start=start, end=start + timedelta(hours=3),
                   description="Live music night for new students.", status="published")
        ev.categories = [TicketCategory(name="Standard", price=1500, quantity=100), TicketCategory(name="VIP", price=4000, quantity=20)]
        db.add(ev)
        db.commit()


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    seed()
    task = asyncio.create_task(sweeper())
    yield
    task.cancel()


app = FastAPI(title="EventSphere", lifespan=lifespan, dependencies=[Depends(verify_csrf)])
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
                   https_only=os.getenv("HTTPS_ONLY") == "1", same_site="lax", max_age=60 * 60 * 8)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["dt"] = lambda d: d.strftime("%a %d %b %Y, %H:%M") if d else ""
templates.env.filters["dtl"] = lambda d: d.strftime("%Y-%m-%dT%H:%M") if d else ""
templates.env.filters["pretty"] = lambda s: s


def render(request, name, user=None, **ctx):
    ctx.update(user=user, csrf=csrf_token(request), flash=request.session.pop("flash", None), now=datetime.now())
    return templates.TemplateResponse(request, name, ctx)


def flash(request, msg):
    request.session["flash"] = msg


def go(url):
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ accounts
@app.get("/login")
def login_page(request: Request, user=Depends(get_user)):
    return render(request, "auth.html", user, mode="login")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    u = db.query(User).filter_by(email=email.strip().lower()).first()
    if not u or not check_pw(password, u.password_hash):
        flash(request, "Wrong email or password.")
        return go("/login")
    request.session.clear()
    request.session["uid"] = u.id
    return go("/admin" if u.role == "admin" else "/scanner" if u.role == "staff" else "/")


@app.get("/register")
def register_page(request: Request, user=Depends(get_user)):
    return render(request, "auth.html", user, mode="register")


@app.post("/register")
def register(request: Request, name: str = Form(...), email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.")
        return go("/register")
    if db.query(User).filter_by(email=email).first():
        flash(request, "That email is already registered.")
        return go("/register")
    u = User(name=name.strip(), email=email, password_hash=hash_pw(password), role="attendee")  # self-service is always attendee
    db.add(u)
    db.commit()
    request.session.clear()
    request.session["uid"] = u.id
    return go("/")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return go("/login")


# ------------------------------------------------------------------ browsing and booking
@app.get("/")
def home(request: Request, q: str = "", category: str = "", user=Depends(get_user), db=Depends(get_db)):
    qs = db.query(Event).filter(Event.status == "published", Event.end >= datetime.now())
    if q:
        like = f"%{q}%"
        qs = qs.filter(or_(Event.title.ilike(like), Event.description.ilike(like)))
    if category:
        qs = qs.filter(Event.category == category)
    cats = [c for (c,) in db.query(Event.category).filter(Event.status == "published").distinct()]
    return render(request, "events.html", user, events=qs.order_by(Event.start).all(), cats=cats, q=q, category=category)


@app.get("/events/{eid}")
def event_page(eid: int, request: Request, user=Depends(get_user), db=Depends(get_db)):
    ev = db.get(Event, eid)
    if not ev or (ev.status == "draft" and not (user and user.role == "admin")):
        raise HTTPException(404, "Event not found")
    return render(request, "event.html", user, ev=ev)


@app.post("/events/{eid}/book")
def book(eid: int, request: Request, category_id: int = Form(...), quantity: int = Form(...),
         user=Depends(require()), db=Depends(get_db)):
    r = bus.request(db, f"User:{user.id}", "BookingAgent", action="book", user_id=user.id, category_id=category_id, quantity=quantity)
    db.commit()
    if r.performative == "AGREE":
        flash(request, f"Booked! Payment reference {r.content['payment_ref']}. Your QR tickets are below.")
        return go("/my/bookings")
    flash(request, "Could not book: " + r.content.get("reason", "unknown reason"))
    return go(f"/events/{eid}")


@app.post("/events/{eid}/waitlist")
def join_waitlist(eid: int, request: Request, category_id: int = Form(...), quantity: int = Form(1),
                  user=Depends(require()), db=Depends(get_db)):
    r = bus.request(db, f"User:{user.id}", "WaitlistAgent", action="join", user_id=user.id, event_id=eid,
                    category_id=category_id, quantity=quantity)
    db.commit()
    flash(request, ("You are on the waitlist. " if r.performative == "AGREE" else "Could not join: ") + r.content.get("reason", ""))
    return go(f"/events/{eid}")


@app.get("/my/bookings")
def my_bookings(request: Request, user=Depends(require()), db=Depends(get_db)):
    return render(
        request, "bookings.html", user,
        bookings=db.query(Booking).filter_by(user_id=user.id).order_by(Booking.id.desc()).all(),
        waits=db.query(WaitlistEntry).filter(WaitlistEntry.user_id == user.id, WaitlistEntry.status.in_(["waiting", "held"])).all(),
        notes=db.query(Notification).filter_by(user_id=user.id).order_by(Notification.id.desc()).limit(15).all(),
    )


@app.post("/bookings/{bid}/cancel")
def cancel_booking(bid: int, request: Request, user=Depends(require()), db=Depends(get_db)):
    r = bus.request(db, f"User:{user.id}", "BookingAgent", action="cancel", booking_id=bid, user_id=user.id)
    db.commit()
    if r.performative == "AGREE":
        flash(request, f"Booking cancelled. Refund: LKR {r.content['refund']} ({r.content['percent']}%).")
    else:
        flash(request, "Could not cancel: " + r.content.get("reason", ""))
    return go("/my/bookings")


@app.post("/waitlist/{wid}/claim")
def claim_hold(wid: int, request: Request, user=Depends(require()), db=Depends(get_db)):
    r = bus.request(db, f"User:{user.id}", "WaitlistAgent", action="claim", entry_id=wid, user_id=user.id)
    db.commit()
    flash(request, "Seats booked!" if r.performative == "AGREE" else "Could not claim: " + r.content.get("reason", ""))
    return go("/my/bookings")


@app.get("/tickets/{code}/qr.png")
def ticket_qr(code: str, user=Depends(require()), db=Depends(get_db)):
    t = db.query(Ticket).filter_by(code=code).first()
    if not t or (t.booking.user_id != user.id and user.role not in ("admin", "staff")):
        raise HTTPException(404)
    buf = io.BytesIO()
    qrcode.make(code).save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


# ------------------------------------------------------------------ private venue hire
def _hire_ctx(db, user):
    return dict(venues=db.query(Venue).filter_by(open_for_hire=True).all(),
                mine=db.query(HireRequest).filter_by(user_id=user.id).order_by(HireRequest.id.desc()).all())


@app.get("/hire")
def hire_form(request: Request, user=Depends(require()), db=Depends(get_db)):
    return render(request, "hire.html", user, result=None, form={}, **_hire_ctx(db, user))


@app.post("/hire")
def hire_submit(request: Request, venue_id: int = Form(...), start: str = Form(...), end: str = Form(...),
                guests: int = Form(...), purpose: str = Form(""), user=Depends(require()), db=Depends(get_db)):
    try:
        datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        flash(request, "Please enter valid start and end times.")
        return go("/hire")
    r = bus.request(db, f"User:{user.id}", "VenueAgent", action="hire", user_id=user.id, venue_id=venue_id,
                    start=start, end=end, guests=guests, purpose=purpose)
    db.commit()
    form = dict(venue_id=venue_id, start=start, end=end, guests=guests, purpose=purpose)
    return render(request, "hire.html", user, result=r, form=form, **_hire_ctx(db, user))


# ------------------------------------------------------------------ staff scanner
class ScanIn(BaseModel):
    code: str
    event_id: int


@app.get("/scanner")
def scanner_page(request: Request, user=Depends(require("staff", "admin")), db=Depends(get_db)):
    events = db.query(Event).filter(Event.status == "published").order_by(Event.start.desc()).limit(50).all()
    return render(request, "scanner.html", user, events=events)


@app.post("/scanner/scan")
def scan(body: ScanIn, user=Depends(require("staff", "admin")), db=Depends(get_db)):
    r = bus.request(db, f"Staff:{user.id}", "TicketAgent", action="scan", code=body.code, event_id=body.event_id)
    db.commit()
    return {"performative": r.performative, **r.content}


# ------------------------------------------------------------------ admin
@app.get("/admin")
def dashboard(request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    rows = []
    for ev in db.query(Event).order_by(Event.start.desc()).all():
        revenue = db.query(func.coalesce(func.sum(Booking.amount - Booking.refund), 0)).filter(Booking.event_id == ev.id).scalar()
        rows.append(dict(ev=ev, sold=sum(c.sold for c in ev.categories), total=sum(c.quantity for c in ev.categories),
                         revenue=revenue, checkins=db.query(Ticket).filter_by(event_id=ev.id, status="used").count()))
    state = db.query(AgentState).filter(~AgentState.key.like("rescans:%")).order_by(AgentState.agent, AgentState.key).all()
    return render(request, "admin_dashboard.html", user, rows=rows, state=state,
                  labels=[r["ev"].title for r in rows], sold=[r["sold"] for r in rows], checkins=[r["checkins"] for r in rows])


@app.get("/admin/venues")
def venues_page(request: Request, edit: int = 0, user=Depends(require("admin")), db=Depends(get_db)):
    return render(request, "admin_venues.html", user, venues=db.query(Venue).order_by(Venue.id).all(),
                  v=db.get(Venue, edit) if edit else None)


@app.post("/admin/venues")
def save_venue(request: Request, venue_id: str = Form(""), name: str = Form(...), location: str = Form(""),
               capacity: int = Form(...), description: str = Form(""), image_url: str = Form(""),
               hourly_rate: float = Form(...), open_for_hire: str = Form(""),
               user=Depends(require("admin")), db=Depends(get_db)):
    if capacity < 1 or hourly_rate < 0:
        flash(request, "Capacity must be at least 1 and the rate cannot be negative.")
        return go("/admin/venues")
    v = db.get(Venue, int(venue_id)) if venue_id else Venue()
    v.name, v.location, v.capacity, v.description = name.strip(), location, capacity, description
    v.image_url, v.hourly_rate, v.open_for_hire = image_url, hourly_rate, bool(open_for_hire)
    db.add(v)
    db.commit()
    flash(request, "Venue saved.")
    return go("/admin/venues")


@app.get("/admin/events")
def admin_events(request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    return render(request, "admin_events.html", user, events=db.query(Event).order_by(Event.start.desc()).all(),
                  venues=db.query(Venue).order_by(Venue.name).all())


@app.post("/admin/events")
def create_event(request: Request, title: str = Form(...), category: str = Form("General"), venue_id: int = Form(...),
                 start: str = Form(...), end: str = Form(...), description: str = Form(""), status: str = Form("draft"),
                 cat_name: list[str] = Form(default=[]), cat_price: list[str] = Form(default=[]),
                 cat_qty: list[str] = Form(default=[]), user=Depends(require("admin")), db=Depends(get_db)):
    try:
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
        cats = [(n.strip(), float(p), int(q)) for n, p, q in zip(cat_name, cat_price, cat_qty) if n.strip()]
        if not cats or any(p < 0 or q < 1 for _, p, q in cats):
            raise ValueError
    except ValueError:
        flash(request, "Enter valid times and at least one ticket category (name, price >= 0, quantity >= 1).")
        return go("/admin/events")
    r = bus.request(db, f"Admin:{user.id}", "VenueAgent", action="check_slot", venue_id=venue_id,
                    start=start, end=end, tickets=sum(q for _, _, q in cats))
    db.commit()
    if r.performative != "AGREE":
        flash(request, "VenueAgent refused: " + r.content["reason"])
        return go("/admin/events")
    ev = Event(title=title.strip(), category=category.strip() or "General", venue_id=venue_id, start=s, end=e,
               description=description, status="published" if status == "published" else "draft")
    ev.categories = [TicketCategory(name=n, price=p, quantity=q) for n, p, q in cats]
    db.add(ev)
    db.commit()
    flash(request, f"Event saved as {ev.status}.")
    return go("/admin/events")


@app.post("/admin/events/{eid}/publish")
def publish_event(eid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    ev = db.get(Event, eid)
    if not ev or ev.status != "draft":
        raise HTTPException(404)
    r = bus.request(db, f"Admin:{user.id}", "VenueAgent", action="check_slot", venue_id=ev.venue_id, start=ev.start,
                    end=ev.end, tickets=sum(c.quantity for c in ev.categories), exclude_event_id=ev.id)
    if r.performative == "AGREE":
        ev.status = "published"
    db.commit()
    flash(request, "Published." if r.performative == "AGREE" else "VenueAgent refused: " + r.content["reason"])
    return go("/admin/events")


@app.post("/admin/events/{eid}/cancel")
def cancel_event(eid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    r = bus.request(db, f"Admin:{user.id}", "BookingAgent", action="cancel_event", event_id=eid)
    db.commit()
    flash(request, f"Event cancelled, LKR {r.content['refunded']} refunded in full." if r.performative == "AGREE" else r.content["reason"])
    return go("/admin/events")


@app.get("/admin/hire")
def admin_hire(request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    return render(request, "admin_hire.html", user, hires=db.query(HireRequest).order_by(HireRequest.id.desc()).all())


@app.post("/admin/hire/{hid}/decide")
def decide_hire(hid: int, request: Request, approve: str = Form(...), reason: str = Form(""),
                user=Depends(require("admin")), db=Depends(get_db)):
    r = bus.request(db, f"Admin:{user.id}", "VenueAgent", action="decide_hire", hire_id=hid, approve=(approve == "1"), reason=reason)
    db.commit()
    flash(request, f"Request {r.content.get('status')}." if r.performative == "AGREE" else r.content["reason"])
    return go("/admin/hire")


@app.get("/admin/messages")
def messages_page(request: Request, cid: str = "", all: int = 0, user=Depends(require("admin")), db=Depends(get_db)):
    q = db.query(MessageLog)
    if cid:
        q = q.filter(MessageLog.conversation_id == cid).order_by(MessageLog.id)
    else:
        if not all:  # hide the scheduler's empty heartbeat sweeps
            noop = db.query(MessageLog.conversation_id).filter(MessageLog.receiver == "Scheduler", MessageLog.content.like('%"expired": 0%'))
            q = q.filter(MessageLog.conversation_id.notin_(noop))
        q = q.order_by(MessageLog.id.desc()).limit(300)
    return render(request, "admin_messages.html", user, msgs=q.all(), cid=cid, all=all)


# Advanced feature: every agent is reachable over HTTP with a JSON message (admin only).
@app.post("/agents/{name}/inbox")
def agent_inbox(name: str, msg: Message, user=Depends(require("admin")), db=Depends(get_db)):
    if name not in bus.AGENTS:
        raise HTTPException(404, "unknown agent")
    msg.receiver = name
    reply = bus.send(db, msg)
    db.commit()
    return reply.model_dump() if reply else {"status": "delivered"}
