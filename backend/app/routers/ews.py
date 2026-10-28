"""Early-warning screens (SRS 9.3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EwsCell, EwsCoverage, GaApproach, User
from ..security import audit, require_role, scope_filter
from .common import Masker, export, parse_day, period

router = APIRouter(prefix="/api/v1/ews", tags=["early warning"])
viewer, analyst = require_role("viewer"), require_role("analyst")


def cell_out(c: EwsCell, m: Masker) -> dict:
    return {"id": c.id, "aerodrome": c.icao, "day": str(c.day), "block": c.block,
            "block_label": "whole day" if c.block < 0 else f"{c.block * 4:02d}–{c.block * 4 + 4:02d} UTC",
            "operator": "all" if c.operator == "*" else ("others" if c.operator == "OTHERS" else m.op(c.operator)),
            "movements": c.movements, "score": c.score, "alert": c.alert, "explanation": c.explanation, "features": c.features,
            "label": c.label, "label_cause": c.label_cause, "label_by": c.label_by if m.full else "", "label_note": c.label_note,
            "model_version": c.model_version}


@router.get("/anomalies")
def anomalies(date_from: str | None = None, date_to: str | None = None, aerodrome: str | None = None, only_alerts: bool = True,
              label: str | None = None, fmt: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 90)
    q = db.query(EwsCell).filter(EwsCell.day >= d0, EwsCell.day <= d1)
    sf = scope_filter(user)
    if sf:
        q = q.filter(EwsCell.icao.in_(sf))
    if aerodrome:
        q = q.filter(EwsCell.icao == aerodrome.upper())
    if only_alerts:
        q = q.filter(EwsCell.alert.is_(True))
    if label is not None:
        q = q.filter(EwsCell.label == ("" if label == "none" else label))
    m = Masker(user)
    rows = [cell_out(c, m) for c in q.order_by(EwsCell.day.desc(), EwsCell.score.desc()).limit(3000)]
    if fmt in ("csv", "xlsx"):
        return export([{k: v for k, v in r.items() if k not in ("features", "explanation")} for r in rows], fmt, "navruna_ews")
    return rows


@router.get("/calendar")
def calendar(aerodrome: str, year: int, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Daily score (whole-day cell) and coverage anomalies for a year (FR-EWS-023)."""
    from datetime import date
    d0, d1 = date(year, 1, 1), date(year, 12, 31)
    cells = {c.day: c for c in db.query(EwsCell).filter(EwsCell.icao == aerodrome.upper(), EwsCell.block == -1,
                                                         EwsCell.operator == "*", EwsCell.day >= d0, EwsCell.day <= d1)}
    alerts = {c.day for c in db.query(EwsCell).filter(EwsCell.icao == aerodrome.upper(), EwsCell.alert.is_(True),
                                                      EwsCell.day >= d0, EwsCell.day <= d1)}
    cov = {c.day: c for c in db.query(EwsCoverage).filter(EwsCoverage.icao == aerodrome.upper(), EwsCoverage.day >= d0, EwsCoverage.day <= d1)}
    days = sorted(set(cells) | set(cov))
    return [{"day": str(d), "score": cells[d].score if d in cells else None, "alert": d in alerts,
             "coverage_anomaly": bool(cov.get(d) and cov[d].anomaly), "coverage_reasons": cov[d].reasons if d in cov else []} for d in days]


@router.get("/cells/{cid}")
def cell(cid: int, user: User = Depends(viewer), db: Session = Depends(get_db)):
    c = db.get(EwsCell, cid)
    if not c:
        raise HTTPException(404, "Cell not found")
    m = Masker(user)
    out = cell_out(c, m)
    q = db.query(GaApproach).filter(GaApproach.icao == c.icao, GaApproach.day == c.day, GaApproach.outcome == "go_around")
    gas = [a for a in q if c.block < 0 or (a.t_thr and a.t_thr.hour // 4 == c.block)]
    if c.operator not in ("*", "OTHERS"):
        gas = [a for a in gas if a.operator == c.operator]
    out["go_arounds"] = [{"id": a.id, "runway": a.runway, "time": a.t_thr.isoformat(timespec="minutes") if a.t_thr else None,
                          "callsign": m.ident(a.callsign), "band": a.ga_band} for a in gas]
    cov = db.query(EwsCoverage).filter_by(icao=c.icao, day=c.day).first()
    out["coverage"] = {"flights": cov.flights, "expected": cov.expected, "gap_share": cov.gap_share, "low_share": cov.low_share,
                       "anomaly": cov.anomaly, "reasons": cov.reasons} if cov else None
    return out


class LabelIn(BaseModel):
    label: str        # real | known_cause | false_alarm
    cause: str = ""   # weather | runway_closure | notam | data_issue | other
    note: str = ""


@router.put("/cells/{cid}/label")
def label_cell(cid: int, body: LabelIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    """FR-EWS-013: label each alert; labels feed precision and threshold tuning."""
    c = db.get(EwsCell, cid)
    if not c:
        raise HTTPException(404, "Cell not found")
    if body.label not in ("real", "known_cause", "false_alarm"):
        raise HTTPException(400, "Label must be real, known_cause or false_alarm")
    before = {"label": c.label, "cause": c.label_cause, "note": c.label_note}
    c.label, c.label_cause, c.label_note, c.label_by = body.label, body.cause, body.note, user.username
    db.commit()
    audit(db, user, "ews_labelled", "ews_cell", cid, {"before": before, "after": body.model_dump()})
    return {"ok": True}


@router.get("/precision")
def precision(date_from: str | None = None, date_to: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 180)
    out = {}
    for c in db.query(EwsCell).filter(EwsCell.day >= d0, EwsCell.day <= d1, EwsCell.alert.is_(True)):
        e = out.setdefault(c.icao, {"aerodrome": c.icao, "alerts": 0, "labelled": 0, "real": 0, "known_cause": 0, "false_alarm": 0})
        e["alerts"] += 1
        if c.label:
            e["labelled"] += 1
            e[c.label] += 1
    for e in out.values():
        e["precision"] = round(e["real"] / e["labelled"], 3) if e["labelled"] else None
    return list(out.values())
