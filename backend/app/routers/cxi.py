"""Complexity screens (SRS 8.4)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import CxiSectorHour, RefSector, User
from ..security import require_role
from .common import export, parse_day, period

router = APIRouter(prefix="/api/v1/cxi", tags=["complexity"])
viewer = require_role("viewer")
FIELDS = ["entries", "peak", "occupancy", "flight_hours", "interaction_h", "vertical_h", "horizontal_h", "speed_h", "crossings",
          "proximity", "level_changes", "heading_changes", "adjusted_density", "structural_index", "pru_score", "index", "top5"]


def _sectors(db):
    s = {x.id: x for x in db.query(RefSector)}
    return s


@router.get("/heat")
def heat(date_from: str | None = None, date_to: str | None = None, measure: str = "index", kind: str | None = None,
         user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Mean measure by sector x hour of day (UTC), grouped by unit (FR-CXI-030)."""
    if measure not in FIELDS:
        raise HTTPException(400, "Unknown measure")
    d0, d1 = period(date_from, date_to, 30)
    secs = _sectors(db)
    acc = defaultdict(list)
    for c in db.query(CxiSectorHour).filter(CxiSectorHour.hour >= datetime(d0.year, d0.month, d0.day),
                                            CxiSectorHour.hour < datetime(d1.year, d1.month, d1.day) + timedelta(days=1)):
        s = secs.get(c.sector_id)
        if s is None or (kind and s.kind != kind):
            continue
        v = getattr(c, measure)
        if v is not None:
            acc[(s.id, c.hour.hour)].append(float(v))
    rows = sorted({k[0] for k in acc}, key=lambda i: (secs[i].unit, secs[i].kind, secs[i].name))
    return {"measure": measure, "rows": [{"id": i, "name": secs[i].name, "unit": secs[i].unit, "kind": secs[i].kind} for i in rows],
            "cells": [{"sector": k[0], "hour": k[1], "value": round(float(np.mean(v)), 3)} for k, v in acc.items()]}


@router.get("/hour")
def hour(at: str, user: User = Depends(viewer), db: Session = Depends(get_db)):
    """All sectors for one hour, for the map (FR-CXI-031)."""
    try:
        t = datetime.fromisoformat(at[:13] + ":00")
    except ValueError:
        raise HTTPException(400, "at must be YYYY-MM-DDTHH")
    secs = _sectors(db)
    return [{"sector": c.sector_id, "name": secs[c.sector_id].name if c.sector_id in secs else "Outside controlled sectors",
             **{f: getattr(c, f) for f in FIELDS}} for c in db.query(CxiSectorHour).filter(CxiSectorHour.hour == t)]


@router.get("/sectors/{sid}")
def sector(sid: int, date_from: str | None = None, date_to: str | None = None, fmt: str | None = None,
           user: User = Depends(viewer), db: Session = Depends(get_db)):
    """Trend of each measure and the busiest hours (FR-CXI-032)."""
    d0, d1 = period(date_from, date_to, 30)
    rows = (db.query(CxiSectorHour).filter(CxiSectorHour.sector_id == sid, CxiSectorHour.hour >= datetime(d0.year, d0.month, d0.day),
                                           CxiSectorHour.hour < datetime(d1.year, d1.month, d1.day) + timedelta(days=1))
            .order_by(CxiSectorHour.hour).all())
    data = [{"hour": r.hour.isoformat(timespec="minutes"), **{f: getattr(r, f) for f in FIELDS}} for r in rows]
    if fmt in ("csv", "xlsx"):
        return export(data, fmt, f"navruna_cxi_sector{sid}_{d0}_{d1}")
    s = db.get(RefSector, sid)
    daily = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for f in ("entries", "interaction_h", "crossings", "level_changes", "heading_changes", "index"):
            v = getattr(r, f)
            if v is not None:
                daily[str(r.hour.date())][f].append(v)
    trend = [{"day": d, **{f: round(float(np.sum(v) if f != "index" else np.mean(v)), 3) for f, v in m.items()}} for d, m in sorted(daily.items())]
    busiest = sorted([x for x in data if x["index"] is not None], key=lambda x: -x["index"])[:20]
    return {"sector": {"id": sid, "name": s.name if s else "Outside controlled sectors", "unit": s.unit if s else "",
                       "kind": s.kind if s else "OUT", "capacity": s.declared_capacity if s else None},
            "trend": trend, "busiest": busiest, "hours": len(data)}


@router.get("/sectors/{sid}/flights")
def sector_flights(sid: int, at: str, user: User = Depends(require_role("reviewer")), db: Session = Depends(get_db)):
    """Flights in a sector during one hour (recomputed on demand from NavTrace)."""
    from ..core import navtrace
    from ..modules.cxi.core import SectorModel
    from ..params import current
    t0 = datetime.fromisoformat(at[:13] + ":00")
    P, _ = current(db)
    sm = SectorModel(db, P)
    idx = next((i for i, s in enumerate(sm.sectors) if s.id == sid), None)
    if idx is None:
        raise HTTPException(404, "Sector not found")
    out = []
    for meta, pts in navtrace.source(db).flights_in_window(t0, t0 + timedelta(hours=1), sm.bbox, step_s=30):
        pts = pts[pts.alt.notna()]
        if pts.empty:
            continue
        a = sm.assign(pts.lon.values, pts.lat.values, pts.alt.values)
        inside = pts[a == idx]
        if len(inside):
            out.append({"gufi": meta["gufi"], "callsign": meta.get("callsign"), "adep": meta.get("adep"), "ades": meta.get("ades"),
                        "minutes": round(len(inside) * 0.5, 1), "min_fl": int(inside.alt.min() // 100), "max_fl": int(inside.alt.max() // 100)})
    return sorted(out, key=lambda r: -r["minutes"])
