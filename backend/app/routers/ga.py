"""Go-around screens and API (SRS 5.2–5.5)."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from scipy.stats import poisson
from sqlalchemy.orm import Session

from ..core import geo, navtrace
from ..core import jobs as jobcore
from ..core.refdata import aerodrome_ref
from ..db import get_db
from ..models import Aerodrome, GaApproach, GaCoverage, MlModel, RefRunway, User, WxReport, now
from ..modules.spi.engine import period_start
from ..params import current
from ..security import audit, can_see_identifiers, require_role, scope_filter
from .common import Masker, export, parse_day, period

router = APIRouter(prefix="/api/v1/ga", tags=["go-arounds"])
viewer, analyst, reviewer = require_role("viewer"), require_role("analyst"), require_role("reviewer")


def _q(db: Session, user: User, d0: date, d1: date, aerodrome=None, runway=None, operator=None, outcome=None):
    q = db.query(GaApproach).filter(GaApproach.day >= d0, GaApproach.day <= d1)
    sf = scope_filter(user)
    if sf:
        q = q.filter(GaApproach.icao.in_(sf))
    if aerodrome:
        q = q.filter(GaApproach.icao == aerodrome.upper())
    if runway:
        q = q.filter(GaApproach.runway == runway.upper())
    if operator:
        if not can_see_identifiers(user):
            raise HTTPException(403, "Filtering by operator needs the reviewer role or higher")
        q = q.filter(GaApproach.operator == operator.upper())
    if outcome:
        q = q.filter(GaApproach.outcome.in_(outcome.split(",")))
    return q


def _offsets(db):
    return {a.icao: a.utc_offset for a in db.query(Aerodrome)}


def event_out(a: GaApproach, m: Masker, offs: dict) -> dict:
    off = offs.get(a.icao, 7)
    return {"id": a.id, "aerodrome": a.icao, "runway": a.runway, "day": str(a.day),
            "time_utc": a.t_thr.isoformat(timespec="seconds") if a.t_thr else None,
            "time_local": (a.t_thr + timedelta(hours=off)).strftime("%H:%M") if a.t_thr else None,
            "callsign": m.ident(a.callsign), "registration": m.ident(a.registration), "operator": m.op(a.operator),
            "aircraft_type": a.aircraft_type, "outcome": a.outcome, "approach_no": a.approach_no, "n_approaches": a.n_approaches,
            "ga_height_ft": a.ga_height_ft, "ga_dist_nm": a.ga_dist_nm, "ga_band": a.ga_band, "ga_outcome": a.ga_outcome,
            "ga_delay_min": a.ga_delay_min, "ga_extra_nm": a.ga_extra_nm, "repeated": a.repeated,
            "min_height_ft": a.min_height_ft, "ttl_s": a.ttl_s, "head_kt": a.head_kt, "cross_kt": a.cross_kt,
            "gust_kt": a.gust_kt, "ts": a.ts, "cb": a.cb, "vis_m": a.vis_m, "unstable": a.unstable,
            "validation": a.validation, "validated_by": a.validated_by if m.full else "", "validation_note": a.validation_note,
            "added_manually": a.added_manually, "incomplete": a.incomplete}


@router.get("/summary")
def summary(date_from: str | None = None, date_to: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 30)
    out = {}
    for a in _q(db, user, d0, d1, outcome="landed,go_around"):
        e = out.setdefault(a.icao, {"aerodrome": a.icao, "approaches": 0, "go_arounds": 0, "low": 0, "repeated": 0,
                                    "pending": 0, "confirmed": 0, "rejected": 0, "ts": 0, "incomplete": 0})
        if a.incomplete:
            e["incomplete"] += 1
            continue
        e["approaches"] += 1
        if a.outcome == "go_around":
            e["go_arounds"] += 1
            e["low"] += int(a.ga_band == "low")
            e["repeated"] += int(a.repeated)
            e["ts"] += int(a.ts)
            e["pending"] += int(a.validation == "pending")
            e["confirmed"] += int(a.validation == "confirmed")
            e["rejected"] += int(a.validation == "rejected")
    for e in out.values():
        e["rate"] = round(1000 * e["go_arounds"] / e["approaches"], 2) if e["approaches"] else None
        rev = e["confirmed"] + e["rejected"]
        e["precision"] = round(e["confirmed"] / rev, 3) if rev else None
    mon = [a.icao for a in db.query(Aerodrome).filter_by(monitored=True)]
    return {"from": str(d0), "to": str(d1), "aerodromes": sorted(out.values(), key=lambda e: e["aerodrome"]), "monitored": mon}


@router.get("/events")
def events(date_from: str | None = None, date_to: str | None = None, aerodrome: str | None = None, runway: str | None = None,
           operator: str | None = None, hour_from: int | None = None, hour_to: int | None = None, band: str | None = None,
           validation: str | None = None, outcome: str = "go_around", fmt: str | None = None, limit: int = 2000,
           user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 30)
    q = _q(db, user, d0, d1, aerodrome, runway, operator, outcome)
    if band:
        q = q.filter(GaApproach.ga_band == band)
    if validation:
        q = q.filter(GaApproach.validation == ("" if validation == "none" else validation))
    m, offs = Masker(user), _offsets(db)
    rows = [event_out(a, m, offs) for a in q.order_by(GaApproach.t_thr.desc()).limit(min(limit, 20000))]
    if hour_from is not None and hour_to is not None:
        rows = [r for r in rows if r["time_local"] and hour_from <= int(r["time_local"][:2]) <= hour_to]
    if fmt in ("csv", "xlsx"):
        audit(db, user, "export", "ga_events", "", {"rows": len(rows), "from": str(d0), "to": str(d1)})
        return export(rows, fmt, f"navruna_goarounds_{d0}_{d1}")
    return rows


def _ga_point(r: RefRunway, dist_nm: float):
    back = math.radians((r.true_brg + 180) % 360)
    lat = r.thr_lat + dist_nm * math.cos(back) / 60
    lon = r.thr_lon + dist_nm * math.sin(back) / (60 * math.cos(math.radians(r.thr_lat)))
    return lat, lon


@router.get("/map")
def ga_map(hours: int | None = 24, date_from: str | None = None, date_to: str | None = None, aerodrome: str | None = None,
           user: User = Depends(viewer), db: Session = Depends(get_db)):
    if date_from or date_to:
        d0, d1 = period(date_from, date_to, 30)
        q = _q(db, user, d0, d1, aerodrome, outcome="go_around")
    else:
        t0 = now() - timedelta(hours=hours or 24)
        q = _q(db, user, t0.date(), now().date(), aerodrome, outcome="go_around").filter(GaApproach.t_thr >= t0)
    rws = {(r.icao, r.runway): r for r in db.query(RefRunway)}
    m, offs = Masker(user), _offsets(db)
    out = []
    for a in q.order_by(GaApproach.t_thr.desc()).limit(5000):
        r = rws.get((a.icao, a.runway))
        if r is None or r.true_brg is None:
            continue
        lat, lon = _ga_point(r, a.ga_dist_nm or 0)
        out.append(event_out(a, m, offs) | {"lat": lat, "lon": lon})
    return out


@router.get("/corridors")
def corridors(aerodrome: str, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Final approach corridors as polygons for the map (FR-GA-001)."""
    P, _ = current(db)
    L, hw, gr = P["ga.corridor_length_nm"], P["ga.corridor_halfwidth_nm"], P["ga.corridor_growth"]
    feats = []
    for r in db.query(RefRunway).filter_by(icao=aerodrome.upper()):
        if r.true_brg is None:
            continue
        b = math.radians(r.true_brg)
        ux, uy = math.sin(b), math.cos(b)
        kx = 60 * math.cos(math.radians(r.thr_lat))
        pts = []
        for d, side in ((0, 1), (L, 1), (L, -1), (0, -1)):
            w = (hw + gr * d) * side
            x, y = -d * ux + w * uy, -d * uy - w * ux
            pts.append([r.thr_lon + x / kx, r.thr_lat + y / 60])
        pts.append(pts[0])
        gates = []
        for g in (6, 4, 2):
            gates.append({"nm": g, "lat": r.thr_lat - g * uy / 60, "lon": r.thr_lon - g * ux / kx})
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [pts]},
                      "properties": {"runway": r.runway, "threshold": [r.thr_lat, r.thr_lon], "bearing": r.true_brg, "gates": gates}})
    return {"type": "FeatureCollection", "features": feats}


@router.get("/trend")
def trend(aerodrome: str | None = None, runway: str | None = None, operator: str | None = None, per: str = "W",
          date_from: str | None = None, date_to: str | None = None, hour_from: int | None = None, hour_to: int | None = None,
          aircraft_type: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Weekly or monthly GA rate with its 90-day baseline and Poisson alert marks (FR-GA-041)."""
    if per not in ("W", "M"):
        raise HTTPException(400, "per must be W or M")
    d0, d1 = period(date_from, date_to, 365)
    P, _ = current(db)
    offs = _offsets(db)
    q = _q(db, user, d0 - timedelta(days=95), d1, aerodrome, runway, operator, "landed,go_around").filter(GaApproach.incomplete.is_(False))
    if aircraft_type:
        q = q.filter(GaApproach.aircraft_type == aircraft_type.upper())
    daily = defaultdict(lambda: [0, 0])
    for a in q.with_entities(GaApproach.day, GaApproach.outcome, GaApproach.t_thr, GaApproach.icao, GaApproach.validation):
        if hour_from is not None and hour_to is not None and a.t_thr is not None:
            h = (a.t_thr.hour + offs.get(a.icao, 7)) % 24
            if not hour_from <= h <= hour_to:
                continue
        daily[a.day][0] += 1
        daily[a.day][1] += int(a.outcome == "go_around" and a.validation != "rejected")
    out, s = [], period_start(d0, per)
    while s <= d1:
        e = s + timedelta(days=6) if per == "W" else (s.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        n = sum(daily[d][0] for d in daily if s <= d <= e)
        g = sum(daily[d][1] for d in daily if s <= d <= e)
        bn = sum(daily[d][0] for d in daily if s - timedelta(days=90) <= d < s)
        bg = sum(daily[d][1] for d in daily if s - timedelta(days=90) <= d < s)
        rate = 1000 * g / n if n else None
        base = 1000 * bg / bn if bn else None
        alert, p = 0, None
        if bn >= P["alert.min_baseline_den"] and n >= P["alert.min_period_den"] and g > 0:
            exp = bg / bn * n if bg else 0.5 / bn * n
            p = float(poisson.sf(g - 1, exp))
            a_ = P["alert.poisson_p"]
            alert = 3 if p < a_ / 100 else 2 if p < a_ / 10 else 1 if p < a_ else 0
        out.append({"start": str(s), "end": str(e), "approaches": n, "go_arounds": g,
                    "rate": round(rate, 3) if rate is not None else None, "baseline": round(base, 3) if base is not None else None,
                    "p": round(p, 5) if p is not None else None, "alert": alert,
                    "too_few": n < P["alert.min_period_den"] or bn < P["alert.min_baseline_den"]})
        s = e + timedelta(days=1)
    return out


@router.get("/heat")
def heat(kind: str = "hour_runway", aerodrome: str | None = None, date_from: str | None = None, date_to: str | None = None,
         user: User = Depends(viewer), db: Session = Depends(get_db)):
    """GA rate by hour of day x runway end, or by operator x month (FR-GA-043)."""
    d0, d1 = period(date_from, date_to, 180)
    offs = _offsets(db)
    m = Masker(user)
    cells = defaultdict(lambda: [0, 0])
    for a in _q(db, user, d0, d1, aerodrome, outcome="landed,go_around").filter(GaApproach.incomplete.is_(False)):
        if kind == "hour_runway":
            key = (f"{a.icao} {a.runway}" if not aerodrome else a.runway, (a.t_thr.hour + offs.get(a.icao, 7)) % 24 if a.t_thr else -1)
        else:
            key = (m.op(a.operator) or "?", a.day.strftime("%Y-%m"))
        cells[key][0] += 1
        cells[key][1] += int(a.outcome == "go_around" and a.validation != "rejected")
    rows = sorted({k[0] for k in cells})
    cols = sorted({k[1] for k in cells})
    return {"kind": kind, "rows": rows, "cols": cols,
            "cells": [{"row": k[0], "col": k[1], "approaches": v[0], "go_arounds": v[1],
                       "rate": round(1000 * v[1] / v[0], 2) if v[0] else None} for k, v in cells.items()]}


@router.get("/approaches/{aid}")
def approach(aid: int, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Detail and replay data: track around the aerodrome with height, distance and lateral offset (FR-GA-042)."""
    a = db.get(GaApproach, aid)
    if not a or (scope_filter(user) and a.icao not in scope_filter(user)):
        raise HTTPException(404, "Approach not found")
    m, offs = Masker(user), _offsets(db)
    out = event_out(a, m, offs) | {"gates": a.gates, "dtl_nm": a.dtl_nm, "since_dep_s": a.since_dep_s,
                                   "extra_40nm": a.extra_40nm, "holds": a.holds, "max_gap_s": a.max_gap_s,
                                   "param_version": a.param_version, "ref_version": a.ref_version}
    wx = db.get(WxReport, a.wx_id) if a.wx_id else None
    out["weather"] = {"raw": wx.raw, "observed": wx.observed.isoformat(), "kind": wx.kind} if wx else None
    others = (db.query(GaApproach).filter(GaApproach.gufi == a.gufi, GaApproach.icao == a.icao)
              .order_by(GaApproach.approach_no).all())
    out["flight_approaches"] = [{"id": o.id, "approach_no": o.approach_no, "runway": o.runway, "outcome": o.outcome,
                                 "time_utc": o.t_thr.isoformat(timespec="seconds") if o.t_thr else None} for o in others]
    leader = None
    if a.ttl_s:
        leader = (db.query(GaApproach).filter(GaApproach.icao == a.icao, GaApproach.runway == a.runway,
                                              GaApproach.t_thr < a.t_thr, GaApproach.outcome.in_(["landed", "go_around"]))
                  .order_by(GaApproach.t_thr.desc()).first())
    out["leader"] = {"id": leader.id, "callsign": m.ident(leader.callsign), "outcome": leader.outcome, "ttl_s": a.ttl_s} if leader else None
    out["track"] = []
    ref = aerodrome_ref(db, a.icao)
    rw = db.query(RefRunway).filter_by(icao=a.icao, runway=a.runway).first()
    try:
        src = navtrace.source(db)
        meta, pts = src.flight_track(a.gufi, ref["lon"], ref["lat"], 40)
    except Exception as e:  # noqa: BLE001 - NFR-011
        meta, pts = None, None
        out["track_error"] = str(e)
    if pts is not None and len(pts) and rw is not None:
        qnh = (a.qnh_hpa or 1013.25)
        P, _ = current(db)
        h = pts.alt.values + P["data.qnh_ft_per_hpa"] * (qnh - 1013.25) - (rw.thr_elev_ft or 0)
        h = np.where(np.isnan(h), 0, h)
        x, y = geo.to_xy(pts.lon.values, pts.lat.values, rw.thr_lon, rw.thr_lat)
        b = math.radians(rw.true_brg)
        dist = -(x * math.sin(b) + y * math.cos(b))
        lat_off = x * math.cos(b) - y * math.sin(b)
        out["track"] = [{"t": int(t), "lat": round(float(la), 5), "lon": round(float(lo), 5), "h": round(float(hh)),
                         "gs": round(float(g)) if g == g else None, "d": round(float(dd), 2), "lat_nm": round(float(lo_), 3)}
                        for t, la, lo, hh, g, dd, lo_ in zip(pts.t, pts.lat, pts.lon, h, pts.gs, dist, lat_off)]
        out["runway_ref"] = {"lat": rw.thr_lat, "lon": rw.thr_lon, "bearing": rw.true_brg, "elev": rw.thr_elev_ft}
    return out


class ValidationIn(BaseModel):
    status: str        # confirmed | rejected | pending
    note: str = ""


@router.put("/approaches/{aid}/validation")
def validate(aid: int, body: ValidationIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    a = db.get(GaApproach, aid)
    if not a:
        raise HTTPException(404, "Approach not found")
    if body.status not in ("confirmed", "rejected", "pending"):
        raise HTTPException(400, "Status must be confirmed, rejected or pending")
    if body.status == "rejected" and not body.note.strip():
        raise HTTPException(400, "Give a reason for rejecting the event (FR-GA-015)")
    before = {"validation": a.validation, "note": a.validation_note}
    a.validation, a.validation_note, a.validated_by = body.status, body.note, user.username
    db.commit()
    audit(db, user, "ga_validated", "ga_approach", aid, {"before": before, "after": body.model_dump()})
    return {"ok": True}


class ManualIn(BaseModel):
    aerodrome: str
    runway: str
    time_utc: str
    callsign: str = ""
    gufi: str = ""
    height_ft: float | None = None
    dist_nm: float | None = None
    note: str


@router.post("/manual")
def manual(body: ManualIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    """Add a go-around the detector missed; counts toward recall (FR-GA-017)."""
    try:
        t = datetime.fromisoformat(body.time_utc.replace("Z", ""))
    except ValueError:
        raise HTTPException(400, "time_utc must be YYYY-MM-DDTHH:MM")
    if not db.query(RefRunway).filter_by(icao=body.aerodrome.upper(), runway=body.runway.upper()).first():
        raise HTTPException(400, "Unknown aerodrome or runway")
    a = GaApproach(icao=body.aerodrome.upper(), runway=body.runway.upper(), day=t.date(), gufi=body.gufi or f"MANUAL-{t:%Y%m%d%H%M}",
                   callsign=body.callsign.upper(), operator=body.callsign[:3].upper() if body.callsign else "", t_start=t,
                   t_end=t, t_thr=t, outcome="go_around", min_height_ft=body.height_ft or 0, min_dist_nm=body.dist_nm or 0,
                   ga_time=t, ga_height_ft=body.height_ft, ga_dist_nm=body.dist_nm,
                   ga_band=("" if body.height_ft is None else "high" if body.height_ft > 1000 else "low" if body.height_ft < 500 else "mid"),
                   validation="confirmed", validated_by=user.username, validation_note=body.note, added_manually=True)
    db.add(a)
    db.commit()
    audit(db, user, "ga_added_manually", "ga_approach", a.id, body.model_dump())
    return {"id": a.id}


@router.get("/validation-stats")
def validation_stats(date_from: str | None = None, date_to: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Precision (confirmed / reviewed) and recall (detected confirmed / all confirmed incl. manual) per aerodrome and month."""
    d0, d1 = period(date_from, date_to, 365)
    out = {}
    for a in _q(db, user, d0, d1, outcome="go_around"):
        k = (a.icao, a.day.strftime("%Y-%m"))
        e = out.setdefault(k, {"aerodrome": k[0], "month": k[1], "detected": 0, "reviewed": 0, "confirmed": 0, "rejected": 0, "manual": 0})
        if a.added_manually:
            e["manual"] += 1
            continue
        e["detected"] += 1
        e["reviewed"] += int(a.validation in ("confirmed", "rejected"))
        e["confirmed"] += int(a.validation == "confirmed")
        e["rejected"] += int(a.validation == "rejected")
    for e in out.values():
        e["precision"] = round(e["confirmed"] / e["reviewed"], 3) if e["reviewed"] else None
        e["recall"] = round(e["confirmed"] / (e["confirmed"] + e["manual"]), 3) if (e["confirmed"] + e["manual"]) else None
    return sorted(out.values(), key=lambda e: (e["aerodrome"], e["month"]))


@router.get("/coverage")
def coverage(aerodrome: str | None = None, date_from: str | None = None, date_to: str | None = None,
             user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 30)
    q = db.query(GaCoverage).filter(GaCoverage.day >= d0, GaCoverage.day <= d1)
    if aerodrome:
        q = q.filter(GaCoverage.icao == aerodrome.upper())
    return [{"aerodrome": c.icao, "runway": c.runway, "day": str(c.day), "approaches": c.approaches, "reached_1000": c.reached_1000,
             "share": c.share, "flights_seen": c.flights_seen, "gap_share": c.gap_share, "incomplete": c.incomplete}
            for c in q.order_by(GaCoverage.day.desc(), GaCoverage.icao, GaCoverage.runway)]


@router.get("/factors")
def factors(aerodrome: str | None = None, date_from: str | None = None, date_to: str | None = None,
            user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Go-around rate by condition with 95 % intervals (FR-GA-033)."""
    from ..modules.ga.model import dataset, factor_effects
    d0, d1 = period(date_from, date_to, 365)
    df = dataset(db, [aerodrome.upper()] if aerodrome else None)
    if df.empty:
        return {"approaches": 0, "effects": {}}
    df = df[(df.day >= d0) & (df.day <= d1)]
    return {"approaches": int(len(df)), "go_arounds": int(df.ga.sum()), "effects": factor_effects(df)}


@router.get("/models")
def models(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return [{"id": m.id, "scope": m.scope, "version": m.version, "status": m.status, "approved_by": m.approved_by,
             "created_at": m.created_at.isoformat(), "metrics": m.metrics, "card": m.card}
            for m in db.query(MlModel).filter_by(kind="ga").order_by(MlModel.id.desc())]


@router.post("/models/train")
def train(user: User = Depends(analyst), db: Session = Depends(get_db)):
    today = now().date()
    rid = jobcore.submit("ga_model", today, today, user.username)
    audit(db, user, "job_started", "job", rid, {"job": "ga_model"})
    return {"job": rid}


class ApproveIn(BaseModel):
    status: str   # approved | retired


@router.post("/models/{mid}/status")
def approve_model(mid: int, body: ApproveIn, user: User = Depends(reviewer), db: Session = Depends(get_db)):
    """A model is used only after its card is approved (GOV-003)."""
    m = db.get(MlModel, mid)
    if not m or m.kind != "ga":
        raise HTTPException(404, "Model not found")
    if body.status not in ("approved", "retired"):
        raise HTTPException(400, "Status must be approved or retired")
    if body.status == "approved":
        for o in db.query(MlModel).filter_by(kind="ga", scope=m.scope, status="approved"):
            o.status = "retired"
    m.status, m.approved_by = body.status, user.username
    db.commit()
    audit(db, user, f"model_{body.status}", "model", mid)
    return {"ok": True}
