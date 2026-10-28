"""Sign-in, own account and personal access tokens (FR-CORE-030, FR-CORE-042)."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import ApiToken, User, now
from ..security import (audit, check_password_policy, current_user, hash_password, login, make_session_token,
                        new_api_token, verify_password)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "email": u.email, "role": u.role,
            "scope": u.scope or [], "active": u.active, "must_change_password": u.must_change_password,
            "language": u.language, "last_login": u.last_login.isoformat() if u.last_login else None,
            "locked_until": u.locked_until.isoformat() if u.locked_until else None}


@router.post("/login")
def do_login(body: LoginIn, db: Session = Depends(get_db)):
    u = login(db, body.username.strip(), body.password)
    return {"token": make_session_token(u), "user": user_out(u)}


@router.post("/refresh")
def refresh(user: User = Depends(current_user)):
    """Sliding session: the interface refreshes while the user is active (30 min idle timeout)."""
    return {"token": make_session_token(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class PwIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
def change_password(body: PwIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "Current password is wrong")
    check_password_policy(body.new_password)
    if body.new_password == body.old_password:
        raise HTTPException(400, "Choose a password different from the current one")
    u = db.get(User, user.id)
    u.password_hash, u.must_change_password = hash_password(body.new_password), False
    db.commit()
    audit(db, u, "password_changed", "user", u.id)
    return {"ok": True}


class PrefIn(BaseModel):
    language: str


@router.put("/preferences")
def prefs(body: PrefIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.language not in ("en", "id"):
        raise HTTPException(400, "Language must be en or id")
    u = db.get(User, user.id)
    u.language = body.language
    db.commit()
    return user_out(u)


class TokenIn(BaseModel):
    name: str
    days: int = 90


@router.get("/tokens")
def tokens(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name, "prefix": t.prefix, "expires_at": t.expires_at.isoformat(), "revoked": t.revoked,
             "last_used": t.last_used.isoformat() if t.last_used else None, "created_at": t.created_at.isoformat()}
            for t in db.query(ApiToken).filter_by(user_id=user.id).order_by(ApiToken.id.desc())]


@router.post("/tokens")
def create_token(body: TokenIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not 1 <= body.days <= 365:
        raise HTTPException(400, "A token lasts 1 to 365 days")
    raw, h = new_api_token()
    t = ApiToken(user_id=user.id, name=body.name[:64] or "token", token_hash=h, prefix=raw[:10],
                 expires_at=now() + timedelta(days=body.days))
    db.add(t)
    db.commit()
    audit(db, user, "token_created", "token", t.id, {"name": t.name, "days": body.days})
    return {"id": t.id, "token": raw, "expires_at": t.expires_at.isoformat(),
            "note": "Copy the token now; it is not shown again."}


@router.delete("/tokens/{tid}")
def revoke_token(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(ApiToken, tid)
    if not t or (t.user_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Token not found")
    t.revoked = True
    db.commit()
    audit(db, user, "token_revoked", "token", t.id)
    return {"ok": True}
