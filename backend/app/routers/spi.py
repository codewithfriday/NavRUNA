"""SPI register and observations (SRS 6.3): GET /spis, /spis/{id}, /spis/{id}/observations; drafts and approval."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import jobs as jobcore
from ..db import get_db
from ..models import GaApproach, SpiDefinition, SpiObservation, TdmFlight, User, now
from ..modules.spi.engine import approved, period_end, period_start, target_status
from ..modules.spi.formulas import REGISTRY
from ..security import ROLE_RANK, audit, require_role, scope_filter
from .common import Masker, export, parse_day, period

router = APIRouter(prefix="/api/v1", tags=["SPI"])
viewer, analyst, reviewer = require_role("viewer"), require_role("analyst"), require_role("reviewer")


def spi_out(s: SpiDefinition) -> dict:
    f = REGISTRY.get(s.formula)
    return {"id": s.id, "code": s.code, "version": s.version, "name": s.name, "description": s.description,
            "owner_unit": s.owner_unit, "owner_person": s.owner_person, "module": s.module, "formula": s.formula,
            "formula_title": f.title if f else "", "formula_params": s.formula_params, "unit": s.unit, "scale": s.scale,
            "breakdowns": s.breakdowns, "available_breakdowns": list(f.dims) if f else [], "periods": s.periods,
            "direction": s.direction, "baseline_method": s.baseline_method, "baseline_window": s.baseline_window,
            "alert_method": s.alert_method, "alert_params": s.alert_params, "target": s.target,
            "target_date": str(s.target_date) if s.target_date else None, "min_denominator": s.min_denominator,
            "classification": s.classification, "nasp_goal": s.nasp_goal, "status": s.status,
            "effective_from": str(s.effective_from) if s.effective_from else None, "backfill_months": s.backfill_months,
            "created_by": s.created_by, "created_at": s.created_at.isoformat(), "approved_by": s.approved_by,
            "approved_at": s.approved_at.isoformat() if s.approved_at else None, "change_note": s.change_note}


def obs_out(o: SpiObservation, m: Masker | None = None) -> dict:
    bd = dict(o.breakdown or {})
    if m is not None and "operator" in bd:
        bd["operator"] = m.op(bd["operator"])
    return {"period": o.period, "start": str(o.period_start), "end": str(o.period_end), "breakdown": bd,
            "breakdown_key": o.breakdown_key if (m is None or m.full or "operator=" not in o.breakdown_key) else "",
            "numerator": o.numerator, "denominator": o.denominator, "value": o.value, "baseline": o.baseline,
            "levels": o.levels, "alert_state": o.alert_state, "reliable": o.reliable, "incomplete": o.incomplete,
            "confirmed_share": o.confirmed_share, "source": o.source, "note": o.note, "version": o.spi_version}


def _visible(user: User, s: SpiDefinition) -> bool:
    return s.classification != "restricted" or ROLE_RANK.get(user.role, 0) >= ROLE_RANK["reviewer"]


@router.get("/spis")
def list_spis(module: str | None = None, owner: str | None = None, status: str = "approved",
              user: User = Depends(viewer), db: Session = Depends(get_db)):
    q = db.query(SpiDefinition)
    if status != "all":
        q = q.filter(SpiDefinition.status == status)
    out = {}
    for s in q.order_by(SpiDefinition.code, SpiDefinition.version):
        if module and s.module != module:
            continue
        if owner and owner.lower() not in s.owner_unit.lower():
            continue
        if _visible(user, s):
            out[(s.code, s.status)] = s
    res = []
    for s in out.values():
        d = spi_out(s)
        if s.status == "approved":
            per = "M" if "M" in s.periods else s.periods[0]
            obs = (db.query(SpiObservation).filter_by(spi_code=s.code, period=per, breakdown_key="")
                   .order_by(SpiObservation.period_start.desc()).limit(12).all())
            d["latest"] = obs_out(obs[0]) if obs else None
            d["spark"] = [{"start": str(o.period_start), "value": o.value, "alert_state": o.alert_state} for o in reversed(obs)]
            d["target_status"] = target_status(db, s, per)
            d["open_alerts"] = None
        res.append(d)
    return sorted(res, key=lambda d: d["code"])


def _get(db: Session, code: str, version: int | None = None) -> SpiDefinition:
    q = db.query(SpiDefinition).filter_by(code=code.upper())
    s = q.filter_by(version=version).first() if version else (q.filter_by(status="approved").order_by(SpiDefinition.version.desc()).first()
                                                             or q.order_by(SpiDefinition.version.desc()).first())
    if not s:
        raise HTTPException(404, "SPI not found")
    return s


@router.get("/spis/{code}")
def get_spi(code: str, user: User = Depends(viewer), db: Session = Depends(get_db)):
    s = _get(db, code)
    if not _visible(user, s):
        raise HTTPException(403, "Restricted SPI")
    d = spi_out(s)
    d["versions"] = [spi_out(v) for v in db.query(SpiDefinition).filter_by(code=s.code).order_by(SpiDefinition.version.desc())]
    d["target_status"] = target_status(db, s)
    return d


@router.get("/spis/{code}/observations")
def observations(code: str, period: str = "M", date_from: str | None = None, date_to: str | None = None,
                 breakdown: str | None = None, dims: str | None = None, fmt: str | None = None,
                 user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Observations of an SPI. `breakdown` = exact key ("aerodrome=WIII|runway=25R", "" = total); `dims` = all keys of
    one breakdown kind ("aerodrome+runway")."""
    s = _get(db, code)
    if not _visible(user, s):
        raise HTTPException(403, "Restricted SPI")
    end = parse_day(date_to, now().date())
    start = parse_day(date_from, end - timedelta(days=400))
    q = db.query(SpiObservation).filter(SpiObservation.spi_code == s.code, SpiObservation.spi_version == s.version,
                                        SpiObservation.period == period.upper(), SpiObservation.period_start >= start,
                                        SpiObservation.period_start <= end)
    m = Masker(user)
    if breakdown is not None:
        q = q.filter(SpiObservation.breakdown_key == breakdown)
    rows = q.order_by(SpiObservation.period_start).all()
    if dims is not None:
        want = [d for d in dims.split("+") if d]
        rows = [o for o in rows if sorted((o.breakdown or {}).keys()) == sorted(want)]
        if "operator" in want and not m.full:
            raise HTTPException(403, "Breakdowns by operator need the reviewer role or higher")
    sf = scope_filter(user)
    if sf:
        rows = [o for o in rows if not (o.breakdown or {}).get("aerodrome") or o.breakdown["aerodrome"] in sf]
    out = [obs_out(o, m) for o in rows]
    if fmt in ("csv", "xlsx"):
        flat = [{k: v for k, v in r.items() if k not in ("levels", "breakdown")} | {f"bd_{k}": v for k, v in r["breakdown"].items()} for r in out]
        audit(db, user, "export", "spi_observations", s.code, {"rows": len(flat)})
        return export(flat, fmt, f"navruna_{s.code}_{period}")
    return out


@router.get("/spis/{code}/drilldown")
def drilldown(code: str, period: str = "M", start: str = "", breakdown: str = "", user: User = Depends(viewer),
              db: Session = Depends(get_db)):
    """Flights or events behind one observation (FR-SPI-009)."""
    s = _get(db, code)
    p0 = parse_day(start)
    p1 = period_end(p0, period.upper())
    bd = dict(kv.split("=", 1) for kv in breakdown.split("|") if "=" in kv)
    m = Masker(user)
    if s.module == "GA":
        q = db.query(GaApproach).filter(GaApproach.day >= p0, GaApproach.day <= p1, GaApproach.outcome == "go_around")
        if "aerodrome" in bd:
            q = q.filter(GaApproach.icao == bd["aerodrome"])
        if "runway" in bd:
            q = q.filter(GaApproach.runway == bd["runway"])
        if "operator" in bd:
            if not m.full:
                raise HTTPException(403, "Operator breakdown needs the reviewer role")
            q = q.filter(GaApproach.operator == bd["operator"])
        if s.formula == "ga_low_rate":
            q = q.filter(GaApproach.ga_band == "low")
        return {"kind": "go_arounds", "rows": [{"id": a.id, "aerodrome": a.icao, "runway": a.runway, "time": a.t_thr.isoformat(timespec="minutes") if a.t_thr else None,
                                                "callsign": m.ident(a.callsign), "operator": m.op(a.operator), "height_ft": a.ga_height_ft,
                                                "band": a.ga_band, "validation": a.validation} for a in q.order_by(GaApproach.t_thr).limit(1000)]}
    if s.module == "TDM":
        from ..models import TdmRoute
        q = db.query(TdmFlight).filter(TdmFlight.day >= p0, TdmFlight.day <= p1)
        if "route" in bd and "-" in bd["route"]:
            a_, b_ = bd["route"].split("-")
            r = db.query(TdmRoute).filter_by(adep=a_, ades=b_).first()
            q = q.filter(TdmFlight.route_id == (r.id if r else -1))
        flag = {"tdm_lateral_share": TdmFlight.flag_lateral, "tdm_vertical_share": TdmFlight.flag_vertical,
                "tdm_offpattern_share": TdmFlight.off_pattern}.get(s.formula)
        if flag is not None:
            q = q.filter(flag.is_(True))
        return {"kind": "flights", "rows": [{"id": f.id, "route_id": f.route_id, "day": str(f.day), "callsign": m.ident(f.callsign),
                                             "operator": m.op(f.operator), "max_dev_nm": f.max_dev_nm, "off_pattern": f.off_pattern}
                                            for f in q.limit(1000)]}
    return {"kind": "none", "rows": [], "note": "Open the module screen for the details behind this SPI."}


class SpiIn(BaseModel):
    code: str
    name: str
    description: str = ""
    owner_unit: str = ""
    owner_person: str = ""
    formula: str
    formula_params: dict = {}
    unit: str = ""
    scale: float = 1.0
    breakdowns: list[str] = []
    periods: list[str] = ["M"]
    direction: str = "up_bad"
    baseline_method: str = "rolling"
    baseline_window: int = 90
    alert_method: str = "poisson"
    alert_params: dict = {}
    target: float | None = None
    target_date: str | None = None
    min_denominator: float = 0
    classification: str = "public"
    nasp_goal: str = ""
    backfill_months: int = 24
    change_note: str = ""


def _check(body: SpiIn):
    f = REGISTRY.get(body.formula)
    if f is None:
        raise HTTPException(400, f"Formula '{body.formula}' is not registered (FR-SPI-002). Available: {', '.join(REGISTRY)}")
    for b in body.breakdowns:
        bad = [d for d in b.split("+") if d and d not in f.dims]
        if bad:
            raise HTTPException(400, f"Breakdown {b}: {', '.join(bad)} not available for {body.formula} ({', '.join(f.dims)})")
    if not body.periods or any(p not in ("D", "W", "M") for p in body.periods):
        raise HTTPException(400, "Periods must be D, W and/or M")
    if body.alert_method not in ("doc9859", "poisson", "ewma", "fixed", "none"):
        raise HTTPException(400, "Unknown alert method")
    if body.alert_method == "fixed" and not body.alert_params.get("levels"):
        raise HTTPException(400, "A fixed alert method needs alert_params.levels, e.g. [2, 3, 4]")
    if body.direction not in ("up_bad", "down_bad") or body.classification not in ("public", "anonymised", "restricted"):
        raise HTTPException(400, "Unknown direction or classification")


@router.post("/spis")
def create_draft(body: SpiIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    """New SPI, or a new version of an existing one, as a draft (FR-SPI-001)."""
    _check(body)
    code = body.code.strip().upper()
    if db.query(SpiDefinition).filter_by(code=code, status="draft").first():
        raise HTTPException(409, f"{code} already has a draft; edit it or reject it first")
    last = db.query(SpiDefinition).filter_by(code=code).order_by(SpiDefinition.version.desc()).first()
    s = SpiDefinition(code=code, version=(last.version if last else 0) + 1, module=REGISTRY[body.formula].module,
                      created_by=user.username, status="draft",
                      **{k: v for k, v in body.model_dump().items() if k not in ("code", "target_date")})
    s.target_date = parse_day(body.target_date) if body.target_date else None
    db.add(s)
    db.commit()
    audit(db, user, "spi_draft_created", "spi", f"{code} v{s.version}", body.model_dump())
    return spi_out(s)


@router.put("/spis/{code}/draft")
def edit_draft(code: str, body: SpiIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    _check(body)
    s = db.query(SpiDefinition).filter_by(code=code.upper(), status="draft").first()
    if not s:
        raise HTTPException(404, "No draft for this SPI")
    before = spi_out(s)
    for k, v in body.model_dump().items():
        if k not in ("code", "target_date"):
            setattr(s, k, v)
    s.module = REGISTRY[body.formula].module
    s.target_date = parse_day(body.target_date) if body.target_date else None
    db.commit()
    audit(db, user, "spi_draft_edited", "spi", f"{s.code} v{s.version}", {"before": before, "after": spi_out(s)})
    return spi_out(s)


class DecisionIn(BaseModel):
    decision: str      # approve | reject
    comment: str = ""


@router.post("/spis/{code}/approve")
def approve(code: str, body: DecisionIn, user: User = Depends(reviewer), db: Session = Depends(get_db)):
    """Four-eyes approval (FR-CORE-012): the reviewer cannot be the author of the draft."""
    s = db.query(SpiDefinition).filter_by(code=code.upper(), status="draft").first()
    if not s:
        raise HTTPException(404, "No draft to approve")
    if s.created_by == user.username:
        raise HTTPException(403, "The author of a draft cannot approve it")
    if body.decision == "reject":
        s.status = "rejected"
        db.commit()
        audit(db, user, "spi_rejected", "spi", f"{s.code} v{s.version}", {"comment": body.comment})
        return spi_out(s)
    for old in db.query(SpiDefinition).filter_by(code=s.code, status="approved"):
        old.status = "retired"
    s.status, s.approved_by, s.approved_at = "approved", user.username, now()
    s.effective_from = s.effective_from or now().date()
    db.commit()
    audit(db, user, "spi_approved", "spi", f"{s.code} v{s.version}", {"comment": body.comment})
    # FR-SPI-004: compute the new version back over the chosen history
    end = now().date() - timedelta(days=1)
    start = (end - timedelta(days=30 * s.backfill_months)).replace(day=1)
    jobcore.submit("spi", start, end, user.username, {"codes": [s.code], "no_alerts": True})
    return spi_out(s)


@router.post("/spis/{code}/retire")
def retire(code: str, user: User = Depends(reviewer), db: Session = Depends(get_db)):
    s = _get(db, code)
    s.status = "retired"
    db.commit()
    audit(db, user, "spi_retired", "spi", f"{s.code} v{s.version}")
    return {"ok": True}


class ManualObsIn(BaseModel):
    period: str = "M"
    start: str
    value: float
    numerator: float | None = None
    denominator: float | None = None
    breakdown: dict = {}
    note: str


@router.post("/spis/{code}/manual")
def manual_value(code: str, body: ManualObsIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    """Values from outside the platform, with their source noted (FR-SPI-008)."""
    s = _get(db, code)
    if not body.note.strip():
        raise HTTPException(400, "Note the source of the value")
    p0 = period_start(parse_day(body.start), body.period)
    key = "|".join(f"{k}={v}" for k, v in body.breakdown.items())
    o = db.query(SpiObservation).filter_by(spi_code=s.code, spi_version=s.version, period=body.period, period_start=p0,
                                           breakdown_key=key).first() or SpiObservation(spi_code=s.code, spi_version=s.version,
                                                                                      period=body.period, period_start=p0, breakdown_key=key)
    o.period_end, o.breakdown, o.value, o.numerator, o.denominator = period_end(p0, body.period), body.breakdown, body.value, body.numerator, body.denominator
    o.source, o.note, o.computed_at = "manual", f"{body.note} (entered by {user.username})", now()
    db.add(o)
    db.commit()
    audit(db, user, "spi_manual_value", "spi", s.code, body.model_dump())
    return obs_out(o)


class RecomputeIn(BaseModel):
    date_from: str
    date_to: str | None = None
    codes: list[str] = []


@router.post("/spis/recompute")
def recompute(body: RecomputeIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    d0, d1 = parse_day(body.date_from), parse_day(body.date_to, now().date())
    rid = jobcore.submit("spi", d0, d1, user.username, {"codes": body.codes} if body.codes else None)
    audit(db, user, "job_started", "job", rid, {"job": "spi", "codes": body.codes})
    return {"job": rid}


@router.get("/formulas")
def formulas(user: User = Depends(viewer)):
    return [{"name": f.name, "title": f.title, "module": f.module, "dims": list(f.dims), "agg": f.agg} for f in REGISTRY.values()]
