"""Alerts (SRS 6.3 GET /alerts; FR-CORE-023 acknowledgement)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Alert, User, now
from ..security import audit, can_see_identifiers, require_role
from .common import export, parse_day

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
viewer = require_role("viewer")


def alert_out(a: Alert, full: bool) -> dict:
    return {"id": a.id, "source": a.source, "ref": a.ref, "breakdown": a.breakdown_key if full else "", "level": a.level,
            "title": a.title if full else (a.public_message or a.title), "message": a.message if full else "",
            "link": a.link, "first_seen": a.first_seen.isoformat(timespec="seconds"),
            "last_seen": a.last_seen.isoformat(timespec="seconds"), "status": a.status, "ack_by": a.ack_by,
            "ack_at": a.ack_at.isoformat(timespec="seconds") if a.ack_at else None, "ack_comment": a.ack_comment,
            "delivery": a.delivery if full else []}


@router.get("")
def list_alerts(status: str | None = None, source: str | None = None, spi: str | None = None, date_from: str | None = None,
                date_to: str | None = None, fmt: str | None = None, limit: int = 500, user: User = Depends(viewer),
                db: Session = Depends(get_db)):
    q = db.query(Alert)
    if status:
        q = q.filter(Alert.status.in_(status.split(",")))
    if source:
        q = q.filter(Alert.source == source.upper())
    if spi:
        q = q.filter(Alert.source == "SPI", Alert.ref == spi.upper())
    if date_from:
        q = q.filter(Alert.last_seen >= parse_day(date_from))
    if date_to:
        from datetime import timedelta
        q = q.filter(Alert.first_seen < parse_day(date_to) + timedelta(days=1))
    full = can_see_identifiers(user)
    rows = [alert_out(a, full) for a in q.order_by(Alert.last_seen.desc()).limit(limit)]
    if fmt in ("csv", "xlsx"):
        return export([{k: v for k, v in r.items() if k != "delivery"} for r in rows], fmt, "navruna_alerts")
    return rows


@router.get("/counts")
def counts(user: User = Depends(viewer), db: Session = Depends(get_db)):
    open_ = db.query(Alert).filter_by(status="open").all()
    return {"open": len(open_), "by_level": {lvl: sum(1 for a in open_ if a.level == lvl) for lvl in (1, 2, 3)}}


class AckIn(BaseModel):
    comment: str
    close: bool = False


@router.post("/{aid}/acknowledge")
def acknowledge(aid: int, body: AckIn, user: User = Depends(viewer), db: Session = Depends(get_db)):
    a = db.get(Alert, aid)
    if not a:
        raise HTTPException(404, "Alert not found")
    if not body.comment.strip():
        raise HTTPException(400, "Add a comment: what was done or why no action is needed")
    a.status = "closed" if body.close else "acknowledged"
    a.ack_by, a.ack_at, a.ack_comment = user.username, now(), body.comment
    db.commit()
    audit(db, user, "alert_acknowledged", "alert", aid, body.model_dump())
    return alert_out(a, can_see_identifiers(user))
