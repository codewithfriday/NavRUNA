"""Sign-in, roles, personal access tokens and the audit log (FR-CORE-030..032, FR-CORE-042, SEC-012)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import ApiToken, AuditLog, User, now

bearer = HTTPBearer(auto_error=False)
ROLE_RANK = {"viewer": 0, "reviewer": 1, "analyst": 2, "admin": 3}
TOKEN_PREFIX = "nvr_"


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except (ValueError, TypeError):
        return False


def check_password_policy(p: str):
    p = p or ""
    if len(p) < config.MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must have at least {config.MIN_PASSWORD_LENGTH} characters")
    classes = sum([any(c.islower() for c in p), any(c.isupper() for c in p), any(c.isdigit() for c in p),
                   any(not c.isalnum() for c in p)])
    if classes < 3:
        raise HTTPException(400, "Password must mix at least three of: lower case, upper case, digits, symbols")


def make_session_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=config.SESSION_IDLE_MINUTES)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, config.SECRET_KEY,
                      algorithm=config.JWT_ALGORITHM)


def new_api_token() -> tuple[str, str]:
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def audit(db: Session, user: User | None, action: str, obj_type: str = "", obj_id="", detail: dict | None = None,
          commit: bool = True):
    db.add(AuditLog(user_id=user.id if user else None, username=user.username if user else "system",
                    action=action, obj_type=obj_type, obj_id=str(obj_id), detail=detail or {}))
    if commit:
        db.commit()


def _user_from_token(token: str, db: Session) -> User:
    if token.startswith(TOKEN_PREFIX):
        h = hashlib.sha256(token.encode()).hexdigest()
        t = db.query(ApiToken).filter_by(token_hash=h).first()
        if not t or t.revoked or t.expires_at < now():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token invalid, revoked or expired")
        t.last_used = now()
        db.commit()
        user = db.get(User, t.user_id)
    else:
        try:
            payload = jwt.decode(token, config.SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
        except jwt.PyJWTError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired, please sign in again")
        user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account inactive")
    return user


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    token = creds.credentials if creds else request.query_params.get("token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return _user_from_token(token, db)


def require_role(min_role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires the {min_role} role")
        return user
    return dep


def can_see_identifiers(user: User) -> bool:
    """SEC-001/003: restricted data (callsign, registration, ICAO address, operator names) for analysts and up."""
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK["reviewer"]


def in_scope(user: User, aerodrome: str | None = None, unit: str | None = None) -> bool:
    """FR-CORE-031: viewers may be limited to aerodromes or units; an empty scope means everything."""
    if user.role != "viewer" or not user.scope:
        return True
    return (aerodrome is not None and aerodrome in user.scope) or (unit is not None and unit in user.scope)


def scope_filter(user: User) -> list[str] | None:
    return list(user.scope) if user.role == "viewer" and user.scope else None


def login(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    t = now()
    if user and user.locked_until and user.locked_until > t:
        audit(db, user, "login_blocked", "user", user.id)
        raise HTTPException(423, f"Account locked until {user.locked_until:%H:%M} UTC")
    if not user or not user.active or not verify_password(password, user.password_hash):
        if user:
            user.failed_attempts += 1
            if user.failed_attempts >= config.MAX_FAILED_LOGINS:
                user.locked_until = t + timedelta(minutes=config.LOCKOUT_MINUTES)
                user.failed_attempts = 0
            db.commit()
        audit(db, user, "login_failed", "user", user.id if user else "", {"username": username})
        raise HTTPException(401, "Invalid username or password")
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = t
    db.commit()
    audit(db, user, "login", "user", user.id)
    return user
