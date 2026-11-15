# IT4123 Agent Based Computing Practical - ICA 02 Report: EventSphere

**Name:** Bavaruba Kuventhirarasa  **Index no.:** ________

| | |
|---|---|
| Live URL | https://________.onrender.com |
| GitHub repository | https://github.com/BavarubaKuventhirarasa/________ (lecturer added as collaborator) |

**Demo accounts**

| Role | Email | Password |
|---|---|---|
| Admin | admin@eventsphere.lk | Admin#2026 |
| Staff | staff@eventsphere.lk | Staff#2026 |
| Attendee | attendee@eventsphere.lk | Attendee#2026 |

## 1. Architecture

EventSphere is one FastAPI application. Browser pages (Jinja2 + Bootstrap 5, tested at 375 px) call thin route functions. A route never changes bookings, tickets or hire requests itself: it sends a **REQUEST message to an agent** through the message bus and shows the reply (AGREE / REFUSE / PROPOSE / INFORM / FAILURE). Data is stored with SQLAlchemy (SQLite locally, PostgreSQL on Render).

```
Browser -> FastAPI route -> bus.request() -> Agent.handle() -> (more messages to other agents) -> reply -> page
                                   |
                              messages table (every message, with conversation id)
```

* `app/bus.py` - `Message` (Pydantic: conversation_id, sender, receiver, performative, content). `send()` logs the message, delivers it, logs the reply. Receiver `*` is a broadcast to every agent's `on_inform()`.
* `app/agents/*.py` - the five agents. Agents never import or call each other; they only use `self.ask()` which goes through the bus.
* **State:** each agent stores beliefs in the `agent_state` table (counters, flagged ticket codes, re-scan counts), so they survive restarts. Domain state such as seat counters and holds is in the database tables the owning agent controls.
* **Timer:** a background task sends `REQUEST {action: sweep}` to the WaitlistAgent every 30 s (proactive behaviour).

## 2. Agents and their decision rules

### VenueAgent - goal: keep every venue calendar free of clashes
1. REFUSE an event slot if end <= start.
2. REFUSE an event slot if total tickets > venue capacity.
3. REFUSE an event slot that overlaps another non-cancelled event or a confirmed/pending hire at the same venue, **including a 30 minute turnaround buffer**; the reason names the clashing activity.
4. Private hire: REFUSE if start is not in the future, guests < 1.
5. If the venue is not open for hire, or guests > capacity: PROPOSE other open venues that fit, otherwise REFUSE.
6. If the time clashes: PROPOSE up to 2 alternative start times at the same venue (shifted +1..+6 h or -1..-3 h, 07:00 or later, same day) and free alternative venues that fit the guest count, each with its price.
7. Price = hours x hourly rate; **x1.25 on Saturday/Sunday** (advanced feature).
8. Routine requests are **confirmed automatically** (AGREE). A request is **escalated** (INFORM, status pending) if it is longer than 4 hours or has more than 80% of capacity as guests. The admin decision (approve/reject) is also a message to VenueAgent, which re-checks the calendar before approving.
9. Broadcasts INFORM `hire_confirmed / hire_pending / hire_decision`.

### BookingAgent - goal: sell tickets fairly without overselling
1. REFUSE quantity < 1 or > 6 per request.
2. REFUSE if the event is not published (draft/cancelled) or has already started.
3. REFUSE if the person would hold more than 6 tickets for the event in total.
4. REFUSE if the category does not have enough free seats (free = quantity - sold - held). The reply says how many are left and whether it is sold out.
5. Payment is simulated: a reference `SIM-XXXXXXXXXX` is generated; no card data exists anywhere.
6. Asks TicketAgent to issue tickets; INFORMs NotificationAgent.
7. **Refund policy on cancellation:** 100% if 7 or more days before the event, 50% from 2 to 7 days, 0% under 48 hours. Refused if the event has started or a ticket was already used.
8. **Event cancelled by the organiser:** every confirmed booking is refunded 100%, tickets cancelled, every buyer notified, waitlist closed.
9. After a cancellation, INFORMs WaitlistAgent that seats were freed.
10. Holds and releases seats on request of the WaitlistAgent (`hold_seats`, `release_hold`).

### TicketAgent - goal: admit each ticket exactly once
1. Issues one code per ticket with `secrets.token_urlsafe(24)` (192 random bits, unguessable).
2. Scan decision order: `UNKNOWN` (no such code) -> `WRONG_EVENT` -> `CANCELLED` (ticket or event) -> `ALREADY_USED` -> `OUTSIDE_TIME` (check-in opens 2 h before start and closes at the end) -> `ADMIT` (ticket becomes used, time recorded).
3. Every rejection is a REFUSE with a plain-English reason; ADMIT is an AGREE.
4. **Suspicious scan detection (advanced):** the agent counts re-scans per code in its state. From the 2nd re-scan the reply says SUSPICIOUS (possible shared screenshot) and the code is added to the agent's `flagged` belief.

### WaitlistAgent - goal: pass freed seats to waiting customers
1. A person may join only if the seats are really sold out (it asks BookingAgent for availability), 1 to 6 seats, no duplicate entry.
2. When seats are freed, the **earliest waiter whose party fits** gets them. The seats are **held for 15 minutes** (BookingAgent counts them as unavailable to everyone else).
3. The attendee sees a live countdown and claims the seats (a normal booking made from the hold).
4. **Timer sweep every 30 s:** expired holds are released, the person is notified, and the seats are **re-offered** to the next waiter.
5. When an event is cancelled, all waiting/held entries are closed.

### NotificationAgent - goal: tell the right person what affects them
Reactive listener (direct INFORMs and all broadcasts). Turns events into notifications for the right user: booking confirmed, booking cancelled with refund, event cancelled, waitlist offer, hold expired, hire confirmed / pending / decided. For `hire_pending` it also notifies every admin.

## 3. Message example (one user action, one conversation id)

Attendee books 2 tickets (shown on Admin > Agent log by clicking the conversation id):

| # | From -> To | Performative | Content |
|---|---|---|---|
| 1 | User:3 -> BookingAgent | REQUEST | action=book, category_id, quantity=2 |
| 2 | BookingAgent -> TicketAgent | REQUEST | action=issue, booking_id, quantity=2 |
| 3 | TicketAgent -> BookingAgent | AGREE | codes=[...] |
| 4 | BookingAgent -> NotificationAgent | INFORM | type=booking_confirmed |
| 5 | BookingAgent -> User:3 | AGREE | booking_id, payment_ref, amount |

(Replace this table with a screenshot of your own log for the viva.)

## 4. Concurrency (no overselling)

Seat counters are changed only with **one atomic conditional UPDATE**:

```sql
UPDATE ticket_categories SET sold = sold + :n
WHERE id = :id AND sold + held + :n <= quantity
```

The capacity check is the WHERE clause, so the check and the change are a single statement. If two people book the last seat at the same moment, the database lets one UPDATE succeed (1 row changed) and the other changes 0 rows; the agent then answers REFUSE "sold out". On PostgreSQL the first UPDATE locks the row and the second waits, then re-evaluates the condition on the new value. No read-then-write gap exists, so no overselling. Waitlist holds use the same pattern on the `held` column. Test: `test_never_oversells_last_seat`.

## 5. Security

* Passwords hashed with bcrypt (salted); never stored in plain text.
* Roles admin / staff / attendee are enforced by a `require()` dependency on every admin and staff route (403 for wrong role, redirect to login if anonymous). Self-registration always creates an attendee.
* CSRF: a per-session token is embedded in every form and checked on every POST by a global dependency (header `X-CSRF-Token` for the scanner's fetch calls).
* Session cookie is signed (SECRET_KEY from the environment), `SameSite=Lax`, `Secure` on Render. No secrets are committed to Git.
* Payments are simulated; no card or bank details are collected.
* QR images are only served to the ticket owner, staff or admin.

## 6. Database

users, venues, events, ticket_categories (quantity, sold, held), bookings, tickets (unique code, status), hire_requests, waitlist, messages (agent log), agent_state (agent beliefs), notifications.

## 7. Advanced features implemented

1. Waitlist with timed holds, visible countdown and automatic re-offer (scheduler sweep).
2. Weekend pricing for venue hire, decided by VenueAgent.
3. Suspicious repeated-scan detection.
4. Agents reachable over HTTP: `POST /agents/{AgentName}/inbox` accepts a JSON Message (admin session + CSRF header); documented at `/docs`.
5. Dashboard chart (tickets sold and check-ins per event, Chart.js).

## 8. Testing

`pytest -v` runs 25 tests: venue overlap/buffer, capacity, routine hire and weekend price, clash proposals, escalation and approval, ticket limit, overbooking of the last seat, unique codes, refund policy (3 cases), event cancellation refunds, double check-in and suspicious flag, wrong event, cancelled ticket, outside check-in time, waitlist hold/expiry/re-offer/claim, notification reaction, message traceability, agent state persistence, CSRF, password hashing, role checks.

## 9. Deployment

Render Blueprint (`render.yaml`): one web service (gunicorn + uvicorn worker, 1 worker so the timer runs once) and a free PostgreSQL database. HTTPS is provided by Render. Free instances sleep when idle, so the site is opened before the viva.

## 10. Limitations

Agents run in one process (in-process bus) rather than as separate services; emails are not sent; hire alternatives search a fixed set of time shifts.
