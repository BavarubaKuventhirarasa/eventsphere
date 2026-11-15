import re

from fastapi.testclient import TestClient

from app.main import app


def token(client, path="/login"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def login(client, email, password="Passw0rd!"):
    r = client.post("/login", data={"email": email, "password": password, "csrf": token(client)}, follow_redirects=False)
    assert r.status_code == 303
    return r


def test_post_without_csrf_token_is_rejected(db):
    assert TestClient(app).post("/login", data={"email": "a@b.c", "password": "x"}).status_code == 403


def test_password_is_hashed(db, make_user):
    u = make_user()
    assert u.password_hash != "Passw0rd!" and u.password_hash.startswith("$2")


def test_attendee_blocked_from_admin_and_scanner(db, make_user):
    c = TestClient(app)
    login(c, make_user("attendee").email)
    assert c.get("/admin").status_code == 403
    assert c.get("/scanner").status_code == 403
    assert c.get("/admin/messages").status_code == 403


def test_staff_can_scan_but_not_admin(db, make_user):
    c = TestClient(app)
    login(c, make_user("staff").email)
    assert c.get("/scanner").status_code == 200
    assert c.get("/admin").status_code == 403


def test_admin_sees_dashboard_and_message_log(db, make_user):
    c = TestClient(app)
    login(c, make_user("admin").email)
    assert c.get("/admin").status_code == 200
    assert c.get("/admin/messages").status_code == 200
