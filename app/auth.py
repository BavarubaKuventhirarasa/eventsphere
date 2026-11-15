import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request

from .db import SessionLocal
from .models import User


def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_pw(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_user(request: Request, db=Depends(get_db)):
    uid = request.session.get("uid")
    return db.get(User, uid) if uid else None


def require(*roles):
    """Route guard: login required, and one of `roles` if any are given."""
    def dep(user=Depends(get_user)):
        if not user:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        if roles and user.role not in roles:
            raise HTTPException(status_code=403, detail="You do not have permission to use this page.")
        return user
    return dep


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request):
    """Applied to the whole app: every POST must carry the session's CSRF token."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("x-csrf-token")
        if not sent:
            form = await request.form()
            sent = form.get("csrf")
        expected = request.session.get("csrf", "")
        if not sent or not expected or not secrets.compare_digest(str(sent), expected):
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid")
