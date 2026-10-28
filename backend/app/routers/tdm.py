"""Trajectory deviation monitor screens (SRS 7.4)."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import jobs as jobcore
from ..core import navtrace
from ..db import get_db
from ..models import TdmCluster, TdmClusterSet, TdmCriticalPoint, TdmFlight, TdmPoint, TdmRoute, User, now
from ..modules.tdm.core import compare, hazard_candidates, hotspots
from ..params import current
from ..security import audit, require_role
from .common import Masker, export, parse_day, period

router = APIRouter(prefix="/api/v1/tdm", tags=["deviation"])
viewer, analyst = require_role("viewer"), require_role("analyst")


def route_out(db, r: TdmRoute) -> dict:
    cs = db.query(TdmClusterSet).filter_by(route_id=r.id, active=True).order_by(TdmClusterSet.version.desc()).first()
    return {"id": r.id, "adep": r.adep, "ades": r.ades, "active": r.active, "unit": r.unit,
            "cluster_set": {"version": cs.version, "from": str(cs.window_from), "to": str(cs.window_to), "flights": cs.n_flights,
                            "unclustered": cs.n_unclustered, "created_at": cs.created_at.isoformat()} if cs else None}


@router.get("/routes")
def routes(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return [route_out(db, r) for r in db.query(TdmRoute).order_by(TdmRoute.adep, TdmRoute.ades)]


@router.get("/routes/proposals")
def proposals(user: User = Depends(analyst), db: Session = Depends(get_db)):
    """The 50 busiest aerodrome pairs in NavTrace over the last 90 days (FR-TDM-001)."""
    end = now().date() - timedelta(days=1)
    try:
        pairs = navtrace.source(db).top_pairs(50, end - timedelta(days=89), end)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, str(e))
    have = {(r.adep, r.ades) for r in db.query(TdmRoute)}
    return [{"adep": a, "ades": b, "flights": n, "monitored": (a, b) in have} for a, b, n in pairs]


class RouteIn(BaseModel):
    adep: str
    ades: str
    unit: str = ""
    active: bool = True


@router.post("/routes")
def add_route(body: RouteIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    a, b = body.adep.upper().strip(), body.ades.upper().strip()
    if len(a) != 4 or len(b) != 4 or a == b:
        raise HTTPException(400, "Give two different ICAO aerodrome codes")
    r = db.query(TdmRoute).filter_by(adep=a, ades=b).first() or TdmRoute(adep=a, ades=b)
    r.unit, r.active = body.unit, body.active
    db.add(r)
    db.commit()
    audit(db, user, "tdm_route_saved", "route", r.id, body.model_dump())
    return route_out(db, r)


@router.post("/routes/{rid}/rebuild")
def rebuild(rid: int, user: User = Depends(analyst), db: Session = Depends(get_db)):
    end = now().date() - timedelta(days=1)
    jid = jobcore.submit("tdm", end, end, user.username, {"rebuild": True})
    audit(db, user, "job_started", "job", jid, {"job": "tdm", "rebuild": True})
    return {"job": jid}


@router.get("/routes/{rid}")
def route_detail(rid: int, day: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    r = db.get(TdmRoute, rid)
    if not r:
        raise HTTPException(404, "Route not found")
    out = route_out(db, r)
    cs = db.query(TdmClusterSet).filter_by(route_id=rid, active=True).first()
    out["clusters"] = [{"id": c.id, "label": c.label, "name": c.name, "n": c.n, "share": c.share, "medoid": c.medoid,
                        "corridor": c.corridor, "profile": c.profile, "retired": c.retired}
                       for c in (db.query(TdmCluster).filter_by(set_id=cs.id).order_by(TdmCluster.label) if cs else [])]
    out["critical_points"] = [{"id": c.id, "name": c.name, "kind": c.kind, "lat": c.lat, "lon": c.lon, "in_tma": c.in_tma,
                               "active": c.active} for c in db.query(TdmCriticalPoint).filter_by(route_id=rid).order_by(TdmCriticalPoint.name)]
    last = db.query(TdmFlight.day).filter_by(route_id=rid).order_by(TdmFlight.day.desc()).first()
    d = parse_day(day, last[0] if last else now().date() - timedelta(days=1))
    m = Masker(user)
    out["day"] = str(d)
    out["flights"] = [{"id": f.id, "callsign": m.ident(f.callsign), "operator": m.op(f.operator), "cluster_id": f.cluster_id,
                       "off_pattern": f.off_pattern, "mean_dist_nm": f.mean_dist_nm, "max_dev_nm": f.max_dev_nm,
                       "flag_lateral": f.flag_lateral, "flag_vertical": f.flag_vertical, "track": f.track}
                      for f in db.query(TdmFlight).filter_by(route_id=rid, day=d)]
    return out


class ClusterIn(BaseModel):
    name: str
    retired: bool = False


@router.put("/clusters/{cid}")
def edit_cluster(cid: int, body: ClusterIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    c = db.get(TdmCluster, cid)
    if not c:
        raise HTTPException(404, "Path not found")
    before = {"name": c.name, "retired": c.retired}
    c.name, c.retired = body.name[:64], body.retired
    db.commit()
    audit(db, user, "tdm_path_edited", "path", cid, {"before": before, "after": body.model_dump()})
    return {"ok": True}


class CpIn(BaseModel):
    name: str
    lat: float
    lon: float
    in_tma: bool = False


@router.post("/routes/{rid}/points")
def add_point(rid: int, body: CpIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    cp = TdmCriticalPoint(route_id=rid, name=body.name.upper()[:32], kind="manual", lat=body.lat, lon=body.lon, in_tma=body.in_tma)
    db.add(cp)
    db.commit()
    audit(db, user, "tdm_point_added", "critical_point", cp.id, body.model_dump())
    return {"id": cp.id}


@router.put("/points/{pid}")
def toggle_point(pid: int, active: bool, user: User = Depends(analyst), db: Session = Depends(get_db)):
    cp = db.get(TdmCriticalPoint, pid)
    if not cp:
        raise HTTPException(404, "Point not found")
    cp.active = active
    db.commit()
    audit(db, user, "tdm_point_toggled", "critical_point", pid, {"active": active})
    return {"ok": True}


@router.get("/points/{pid}/histogram")
def point_histogram(pid: int, date_from: str | None = None, date_to: str | None = None, user: User = Depends(viewer),
                    db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 30)
    rows = (db.query(TdmPoint).join(TdmFlight, TdmFlight.id == TdmPoint.flight_id)
            .filter(TdmPoint.cp_id == pid, TdmFlight.day >= d0, TdmFlight.day <= d1).all())
    return {"lateral_nm": [p.lateral_nm for p in rows], "alt_diff_ft": [p.alt_diff_ft for p in rows if p.alt_diff_ft is not None],
            "flagged_lateral": sum(p.flag_lateral for p in rows), "flagged_vertical": sum(p.flag_vertical for p in rows), "n": len(rows)}


@router.get("/hotspots")
def get_hotspots(route_id: int | None = None, date_from: str | None = None, date_to: str | None = None,
                 user: User = Depends(viewer), db: Session = Depends(get_db)):
    d0, d1 = period(date_from, date_to, 30)
    P, _ = current(db)
    return hotspots(db, route_id, d0, d1, P)


@router.get("/compare")
def get_compare(route_id: int, change_date: str, days: int = 30, user: User = Depends(viewer), db: Session = Depends(get_db)):
    return compare(db, route_id, parse_day(change_date), days)


@router.get("/hazards")
def hazards(date_from: str | None = None, date_to: str | None = None, fmt: str | None = None, user: User = Depends(viewer),
            db: Session = Depends(get_db)):
    """Hazard-candidate list for the SRM unit (FR-TDM-023); example flights only for reviewers and up."""
    d0, d1 = period(date_from, date_to, 30)
    P, _ = current(db)
    rows = hazard_candidates(db, d0, d1, P)
    m = Masker(user)
    if not m.full:
        for r in rows:
            r["examples"] = []
    if fmt in ("csv", "xlsx"):
        audit(db, user, "export", "tdm_hazards", "", {"rows": len(rows)})
        return export([r | {"examples": ";".join(r["examples"])} for r in rows], fmt, f"navruna_hazard_candidates_{d0}_{d1}")
    return rows
