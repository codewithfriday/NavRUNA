"""Reference data screens: runways, aerodromes, sectors, fixes, airlines (FR-CORE-013, DR-004..006, DR-009)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import refdata
from ..db import get_db
from ..models import Aerodrome, Airline, RefPoint, RefRunway, RefSector, RefVersion, User
from ..params import current
from ..security import audit, require_role

router = APIRouter(prefix="/api/v1/ref", tags=["reference data"])
viewer, analyst, admin = require_role("viewer"), require_role("analyst"), require_role("admin")


def rw_out(r: RefRunway) -> dict:
    return {"id": r.id, "icao": r.icao, "runway": r.runway, "thr_lat": r.thr_lat, "thr_lon": r.thr_lon,
            "thr_elev_ft": r.thr_elev_ft, "true_brg": r.true_brg, "length_m": r.length_m, "width_m": r.width_m,
            "arp_lat": r.arp_lat, "arp_lon": r.arp_lon, "arp_elev_ft": r.arp_elev_ft, "notes": r.notes,
            "issues": r.issues or [], "derived": r.derived or [], "version": r.version}


@router.get("/runways")
def runways(icao: str | None = None, issues_only: bool = False, user: User = Depends(viewer), db: Session = Depends(get_db)):
    q = db.query(RefRunway)
    if icao:
        q = q.filter(RefRunway.icao == icao.upper())
    rows = [rw_out(r) for r in q.order_by(RefRunway.icao, RefRunway.runway)]
    if issues_only:
        rows = [r for r in rows if r["issues"]]
    return rows


@router.post("/runways/import")
async def import_runways(file: UploadFile = File(...), user: User = Depends(analyst), db: Session = Depends(get_db)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    P, _ = current(db)
    try:
        rep = refdata.import_runways(db, text, user.username, P["data.threshold_length_tolerance"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, user, "runways_imported", "ref", rep["version"], {k: (len(v) if isinstance(v, list) else v) for k, v in rep.items()})
    return rep


class RunwayIn(BaseModel):
    thr_lat: float
    thr_lon: float
    thr_elev_ft: float | None = None
    true_brg: float | None = None
    length_m: float | None = None
    notes: str = ""


@router.put("/runways/{rid}")
def edit_runway(rid: int, body: RunwayIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    r = db.get(RefRunway, rid)
    if not r:
        raise HTTPException(404, "Runway not found")
    before = rw_out(r)
    if not (-90 <= body.thr_lat <= 90 and -180 <= body.thr_lon <= 180):
        raise HTTPException(400, "Coordinates out of range")
    if body.true_brg is not None and not 0 <= body.true_brg <= 360:
        raise HTTPException(400, "Bearing must be 0–360")
    r.thr_lat, r.thr_lon, r.thr_elev_ft, r.true_brg, r.length_m, r.notes = (body.thr_lat, body.thr_lon, body.thr_elev_ft,
                                                                            body.true_brg, body.length_m, body.notes)
    r.issues = [i for i in (r.issues or []) if "bearing" not in i.lower() or body.true_brg is None]
    r.derived = [d for d in (r.derived or []) if not d.startswith("true_brg") or body.true_brg is None]
    r.version = refdata.next_version(db, "runways", user.username, f"Edited {r.icao} {r.runway}")
    db.commit()
    audit(db, user, "runway_edited", "runway", rid, {"before": before, "after": rw_out(r)})
    return rw_out(r)


@router.get("/runways/export")
def export_runways(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return Response(refdata.export_runways_csv(db), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="runway_thresholds.csv"'})


@router.get("/aerodromes")
def aerodromes(monitored: bool | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    q = db.query(Aerodrome)
    if monitored is not None:
        q = q.filter_by(monitored=monitored)
    rws = {}
    for r in db.query(RefRunway):
        rws.setdefault(r.icao, []).append(r)
    out = []
    for a in q.order_by(Aerodrome.icao):
        rr = rws.get(a.icao, [])
        lat = next((r.arp_lat for r in rr if r.arp_lat is not None), rr[0].thr_lat if rr else None)
        lon = next((r.arp_lon for r in rr if r.arp_lon is not None), rr[0].thr_lon if rr else None)
        out.append({"icao": a.icao, "name": a.name, "monitored": a.monitored, "utc_offset": a.utc_offset, "unit": a.unit,
                    "lat": lat, "lon": lon, "runways": sorted(r.runway for r in rr),
                    "issues": sum(1 for r in rr if r.issues)})
    return out


class AerodromeIn(BaseModel):
    name: str = ""
    monitored: bool = False
    utc_offset: int = 7
    unit: str = ""


@router.put("/aerodromes/{icao}")
def edit_aerodrome(icao: str, body: AerodromeIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    a = db.get(Aerodrome, icao.upper())
    if not a:
        raise HTTPException(404, "Aerodrome not in the runway data; import its thresholds first")
    if not -12 <= body.utc_offset <= 14:
        raise HTTPException(400, "UTC offset out of range")
    before = {"monitored": a.monitored, "utc_offset": a.utc_offset, "unit": a.unit, "name": a.name}
    a.name, a.monitored, a.utc_offset, a.unit = body.name, body.monitored, body.utc_offset, body.unit
    db.commit()
    audit(db, user, "aerodrome_edited", "aerodrome", icao, {"before": before, "after": body.model_dump()})
    return {"ok": True}


def sector_out(s: RefSector, geometry: bool = False) -> dict:
    d = {"id": s.id, "name": s.name, "kind": s.kind, "unit": s.unit, "lower_raw": s.lower_raw, "upper_raw": s.upper_raw,
         "lower_ft": s.lower_ft, "upper_ft": s.upper_ft, "override_lower_ft": s.override_lower_ft,
         "override_upper_ft": s.override_upper_ft, "needs_override": s.needs_override, "class": s.airspace_class,
         "area_nm2": s.area_nm2, "declared_capacity": s.declared_capacity, "active": s.active, "version": s.version}
    if geometry:
        d["geometry"] = s.geometry
    return d


@router.get("/sectors")
def sectors(geometry: bool = False, user: User = Depends(viewer), db: Session = Depends(get_db)):
    return [sector_out(s, geometry) for s in db.query(RefSector).order_by(RefSector.kind.desc(), RefSector.name)]


@router.get("/sectors.geojson")
def sectors_geojson(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": s.geometry, "properties": sector_out(s)} for s in db.query(RefSector).filter_by(active=True)]}


class SectorIn(BaseModel):
    override_lower_ft: float | None = None
    override_upper_ft: float | None = None
    declared_capacity: int | None = None
    active: bool = True


@router.put("/sectors/{sid}")
def edit_sector(sid: int, body: SectorIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    s = db.get(RefSector, sid)
    if not s:
        raise HTTPException(404, "Sector not found")
    before = sector_out(s)
    s.override_lower_ft, s.override_upper_ft, s.declared_capacity, s.active = (body.override_lower_ft, body.override_upper_ft,
                                                                               body.declared_capacity, body.active)
    db.commit()
    audit(db, user, "sector_edited", "sector", sid, {"before": before, "after": sector_out(s)})
    return sector_out(s)


@router.post("/navearth/refresh")
def navearth_refresh(user: User = Depends(admin), db: Session = Depends(get_db)):
    res = refdata.refresh_navearth(db, user.username)
    audit(db, user, "navearth_refresh", "ref", "", res)
    if res.get("errors") and not res.get("sectors"):
        raise HTTPException(502, "; ".join(res["errors"]))
    return res


@router.get("/points")
def points(q: str | None = None, user: User = Depends(viewer), db: Session = Depends(get_db)):
    qq = db.query(RefPoint)
    if q:
        qq = qq.filter(RefPoint.name.contains(q.upper()))
    return [{"id": p.id, "name": p.name, "kind": p.kind, "lat": p.lat, "lon": p.lon} for p in qq.order_by(RefPoint.name).limit(500)]


@router.get("/airlines")
def airlines(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return [{"designator": a.designator, "name": a.name, "country": a.country} for a in db.query(Airline).order_by(Airline.designator)]


class AirlineIn(BaseModel):
    designator: str
    name: str
    country: str = ""


@router.post("/airlines")
def add_airline(body: AirlineIn, user: User = Depends(analyst), db: Session = Depends(get_db)):
    d = body.designator.strip().upper()
    if len(d) != 3 or not d.isalpha():
        raise HTTPException(400, "An ICAO airline designator has three letters")
    a = db.get(Airline, d) or Airline(designator=d)
    a.name, a.country = body.name, body.country
    db.add(a)
    db.commit()
    audit(db, user, "airline_saved", "airline", d, body.model_dump())
    return {"ok": True}


@router.delete("/airlines/{d}")
def del_airline(d: str, user: User = Depends(analyst), db: Session = Depends(get_db)):
    a = db.get(Airline, d.upper())
    if a:
        db.delete(a)
        db.commit()
        audit(db, user, "airline_deleted", "airline", d)
    return {"ok": True}


@router.get("/versions")
def versions(user: User = Depends(viewer), db: Session = Depends(get_db)):
    return [{"kind": v.kind, "version": v.version, "note": v.note, "author": v.author, "at": v.created_at.isoformat()}
            for v in db.query(RefVersion).order_by(RefVersion.id.desc()).limit(200)]
