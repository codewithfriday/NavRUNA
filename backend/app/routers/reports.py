"""Reports: SRB monthly PDF with draft -> in review -> released workflow (FR-CORE-040, FR-SPI-022/023)."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..core import jobs as jobcore
from ..db import get_db
from ..models import AppSetting, Report, User, now
from ..security import audit, require_role

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
viewer, analyst, reviewer = require_role("viewer"), require_role("analyst"), require_role("reviewer")


def rep_out(r: Report) -> dict:
    return {"id": r.id, "kind": r.kind, "period": r.period, "title": r.title, "status": r.status, "summary": r.summary,
            "created_by": r.created_by, "reviewed_by": r.reviewed_by, "released_by": r.released_by,
            "released_at": r.released_at.isoformat() if r.released_at else None, "created_at": r.created_at.isoformat(),
            "data_version": r.data_version}


@router.get("")
def reports(user: User = Depends(viewer), db: Session = Depends(get_db)):
    q = db.query(Report).order_by(Report.period.desc(), Report.id.desc())
    if user.role == "viewer":
        q = q.filter(Report.status == "released")
    return [rep_out(r) for r in q]


class SrbIn(BaseModel):
    month: str


@router.post("/srb")
def make_srb(body: SrbIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    if not re.fullmatch(r"\d{4}-\d{2}", body.month):
        raise HTTPException(400, "month must be YYYY-MM")
    if db.query(Report).filter(Report.kind == "srb", Report.period == body.month, Report.status != "draft").first():
        raise HTTPException(409, "This month's report is already in review or released")
    today = now().date()
    rid = jobcore.submit("monthly", today, today, user.username, {"force": True, "month": body.month})
    audit(db, user, "job_started", "job", rid, {"job": "monthly", "month": body.month})
    return {"job": rid}


@router.get("/{rid}/file")
def download(rid: int, user: User = Depends(viewer), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or (user.role == "viewer" and r.status != "released"):
        raise HTTPException(404, "Report not found")
    p = Path(r.path)
    if not p.exists():
        raise HTTPException(410, "The report file is missing")
    audit(db, user, "report_downloaded", "report", rid)
    return FileResponse(p, media_type="application/pdf", filename=f"NavRUNA_SRB_{r.period}_{r.status}.pdf")


class StatusIn(BaseModel):
    status: str      # review | released | draft
    comment: str = ""


@router.post("/{rid}/status")
def set_status(rid: int, body: StatusIn, user: User = Depends(viewer), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r:
        raise HTTPException(404, "Report not found")
    from ..security import ROLE_RANK
    rank = ROLE_RANK.get(user.role, 0)
    if body.status == "review":
        if r.status != "draft" or rank < ROLE_RANK["analyst"]:
            raise HTTPException(403, "Only analysts send a draft for review")
        r.status = "review"
    elif body.status == "released":
        if r.status != "review" or rank < ROLE_RANK["reviewer"]:
            raise HTTPException(403, "Only reviewers release a report in review")
        if r.created_by == user.username:
            raise HTTPException(403, "The author cannot release their own report")
        r.status, r.released_by, r.released_at, r.reviewed_by = "released", user.username, now(), user.username
        _distribute(db, r)
    elif body.status == "draft":
        if r.status != "review" or rank < ROLE_RANK["reviewer"]:
            raise HTTPException(403, "Only reviewers return a report to draft")
        r.status, r.reviewed_by = "draft", user.username
    else:
        raise HTTPException(400, "Unknown status")
    db.commit()
    audit(db, user, f"report_{body.status}", "report", rid, {"comment": body.comment})
    return rep_out(r)


def _distribute(db: Session, r: Report):
    """E-mail the released report to the distribution list (FR-SPI-023)."""
    s = db.get(AppSetting, "srb_distribution")
    emails = (s.value or {}).get("emails", []) if s else []
    if not emails or not config.SMTP_HOST:
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = f"NavRUNA {r.title} (released)"
    msg["From"], msg["To"] = config.SMTP_FROM, ", ".join(emails)
    msg.set_content(f"The {r.title} has been released by the Safety Review Board.\n\n{config.DISCLAIMER}")
    msg.add_attachment(Path(r.path).read_bytes(), maintype="application", subtype="pdf", filename=f"NavRUNA_SRB_{r.period}.pdf")
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            if config.SMTP_TLS:
                smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception:  # noqa: BLE001 - release stands even if mail fails
        pass
