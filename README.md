# EventSphere - multi-agent venue booking and digital ticketing

IT4123 Agent Based Computing Practical, ICA 02. FastAPI + SQLAlchemy + Jinja/Bootstrap.
Five cooperating agents (VenueAgent, BookingAgent, TicketAgent, WaitlistAgent, NotificationAgent) talk
only through JSON messages on a bus (`app/bus.py`); every message is stored in the `messages` table.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 (SQLite file `eventsphere.db` is created and seeded automatically).

## Environment variables
| Name | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL URL in production | `sqlite:///./eventsphere.db` |
| `SECRET_KEY` | signs the session cookie (never commit it) | dev-only value |
| `HTTPS_ONLY` | `1` marks the cookie Secure (set on Render) | off |
| `TZ` | local time zone for events | `Asia/Colombo` |
| `SWEEP_SECONDS` | WaitlistAgent expiry sweep interval | `30` |

## Demo accounts (seeded on first start)
| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.lk | Admin#2026 |
| Staff | staff@eventsphere.lk | Staff#2026 |
| Attendee | attendee@eventsphere.lk | Attendee#2026 |

## Tests
```bash
pytest -v
```
(25 tests: overbooking, double check-in, refunds, hire clashes, waitlist expiry, message tracing, CSRF and roles.)

## Demo tip: showing check-in
Check-in opens 2 hours before an event starts. In Admin > Events create an event starting in the next
hour, book a ticket as the attendee, then scan its QR (or paste the code) on /scanner as staff.

## Deploy on Render
Push to GitHub, then Render > New > Blueprint > pick the repo (uses `render.yaml`: web service + free PostgreSQL).
