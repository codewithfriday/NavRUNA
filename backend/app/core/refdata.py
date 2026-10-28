"""Reference data: runway thresholds, monitored aerodromes, sectors and fixes from NavEarth, airline designators
(SRS 3, DR-004..006, DR-009, FR-CORE-013)."""
from __future__ import annotations

import csv
import io
import math
import re
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy.orm import Session

from .. import config
from ..models import Aerodrome, Airline, RefPoint, RefRunway, RefSector, RefVersion, now
from .geo import bearing_deg, haversine_nm, polygon_area_nm2

SEED = Path(__file__).resolve().parent.parent / "seed"

DEFAULT_MONITORED = {"WIII": ("Soekarno-Hatta", 7, "Jakarta APP"), "WSSS": ("Singapore Changi", 8, "Singapore APP"),
                     "WADD": ("I Gusti Ngurah Rai", 8, "Bali APP"), "WARR": ("Juanda", 7, "Surabaya APP"),
                     "WMKK": ("Kuala Lumpur Intl", 8, "Lumpur APP")}
# UTC offset per ICAO prefix: WI* west (WIB), WA* mostly central/east; refined per aerodrome on the screen
PREFIX_OFFSET = {"WI": 7, "WA": 8, "WS": 8, "WM": 8}


def next_version(db: Session, kind: str, author: str, note: str) -> int:
    last = db.query(RefVersion).filter_by(kind=kind).order_by(RefVersion.version.desc()).first()
    v = (last.version if last else 0) + 1
    db.add(RefVersion(kind=kind, version=v, author=author, note=note))
    return v


def current_version(db: Session, kind: str) -> int:
    last = db.query(RefVersion).filter_by(kind=kind).order_by(RefVersion.version.desc()).first()
    return last.version if last else 0


# ------------------------------------------------------------------ runways
def _f(v):
    try:
        return float(v) if str(v).strip() not in ("", "None", "nan") else None
    except ValueError:
        return None


def import_runways(db: Session, text: str, author: str, tolerance: float = 0.05) -> dict:
    """Import the threshold file; fill what can be derived and list what cannot (DR-004, DR-005)."""
    rows = list(csv.DictReader(io.StringIO(text)))
    need = {"icao", "runway", "thr_lat", "thr_lon"}
    if not rows or not need.issubset(rows[0].keys()):
        raise ValueError(f"The file needs at least the columns {', '.join(sorted(need))}")
    by_ad: dict[str, list[dict]] = {}
    for r in rows:
        r = {k: (v or "").strip() for k, v in r.items()}
        if not r["icao"] or not r["runway"] or _f(r["thr_lat"]) is None or _f(r["thr_lon"]) is None:
            continue
        by_ad.setdefault(r["icao"].upper(), []).append(r)
    version = next_version(db, "runways", author, f"{len(rows)} runway ends imported")
    db.query(RefRunway).delete()
    report = {"runway_ends": 0, "aerodromes": len(by_ad), "derived_bearing": [], "derived_elevation": [],
              "incomplete": [], "length_mismatch": []}
    for icao, ends in by_ad.items():
        for r in ends:
            rec = RefRunway(icao=icao, runway=r["runway"].upper(), thr_lat=_f(r["thr_lat"]), thr_lon=_f(r["thr_lon"]),
                            thr_elev_ft=_f(r.get("thr_elev_ft")), true_brg=_f(r.get("true_brg")),
                            length_m=_f(r.get("length_m")), width_m=_f(r.get("width_m")), arp_lat=_f(r.get("arp_lat")),
                            arp_lon=_f(r.get("arp_lon")), arp_elev_ft=_f(r.get("arp_elev_ft")),
                            notes=r.get("notes", ""), issues=[], derived=[], version=version)
            opp = _opposite(rec.runway, ends)
            if rec.true_brg is None:
                if opp is not None:
                    rec.true_brg = round(float(bearing_deg(rec.thr_lat, rec.thr_lon, _f(opp["thr_lat"]), _f(opp["thr_lon"]))), 2)
                    rec.derived.append("true_brg")
                    report["derived_bearing"].append(f"{icao} {rec.runway}")
                else:
                    try:
                        rec.true_brg = (int(re.match(r"\d+", rec.runway).group()) * 10) % 360 or 360
                        rec.derived.append("true_brg (from designator, check)")
                        rec.issues.append("Bearing taken from the runway number; enter the true bearing")
                    except (AttributeError, ValueError):
                        rec.issues.append("No bearing and no opposite end: runway cannot be used")
                    report["incomplete"].append(f"{icao} {rec.runway}")
            if rec.thr_elev_ft is None:
                if rec.arp_elev_ft is not None:
                    rec.thr_elev_ft = rec.arp_elev_ft
                    rec.derived.append("thr_elev_ft (aerodrome elevation)")
                    report["derived_elevation"].append(f"{icao} {rec.runway}")
                else:
                    rec.thr_elev_ft = 0.0
                    rec.issues.append("No elevation: 0 ft used")
                    report["incomplete"].append(f"{icao} {rec.runway}")
            if opp is not None and rec.length_m:
                d_m = float(haversine_nm(rec.thr_lat, rec.thr_lon, _f(opp["thr_lat"]), _f(opp["thr_lon"]))) * 1852
                if abs(d_m - rec.length_m) > tolerance * rec.length_m and "Displaced" not in (rec.notes + opp.get("notes", "")):
                    rec.issues.append(f"Threshold pair is {d_m:.0f} m apart, published length {rec.length_m:.0f} m")
                    report["length_mismatch"].append(f"{icao} {rec.runway}: {d_m:.0f} m vs {rec.length_m:.0f} m")
            if opp is None:
                rec.issues.append("Only one runway end listed")
            db.add(rec)
            report["runway_ends"] += 1
        a = db.get(Aerodrome, icao)
        if a is None:
            name, off, unit = DEFAULT_MONITORED.get(icao, ("", PREFIX_OFFSET.get(icao[:2], 7), ""))
            if icao in ("WAAA", "WADD") or icao.startswith(("WAA", "WAD", "WAF", "WAW", "WAT", "WAO", "WAL", "WAQ", "WAG")):
                off = 8
            elif icao.startswith(("WAB", "WAJ", "WAK", "WAP", "WAS", "WAU", "WAV", "WAY")):
                off = 9
            db.add(Aerodrome(icao=icao, name=name, monitored=icao in DEFAULT_MONITORED, utc_offset=off, unit=unit))
    db.commit()
    report["version"] = version
    return report


def _opposite(rw: str, ends: list[dict]) -> dict | None:
    m = re.match(r"(\d+)([LCR]?)", rw)
    if not m:
        return None
    n = (int(m.group(1)) + 18 - 1) % 36 + 1
    side = {"L": "R", "R": "L", "C": "C", "": ""}[m.group(2)]
    target = f"{n:02d}{side}"
    for e in ends:
        if e["runway"].upper() == target:
            return e
    others = [e for e in ends if e["runway"].upper() != rw]
    return others[0] if len(others) == 1 else None


def seed_runways(db: Session):
    if db.query(RefRunway).count():
        return
    f = SEED / "runway_thresholds.csv"
    if f.exists():
        import_runways(db, f.read_text(), "seed")


def runways_as_dicts(db=None) -> list[dict]:
    from ..db import SessionLocal
    own = db is None
    db = db or SessionLocal()
    try:
        return [{"icao": r.icao, "runway": r.runway, "thr_lat": r.thr_lat, "thr_lon": r.thr_lon,
                 "thr_elev_ft": r.thr_elev_ft, "true_brg": r.true_brg, "length_m": r.length_m,
                 "arp_lat": r.arp_lat, "arp_lon": r.arp_lon, "arp_elev_ft": r.arp_elev_ft}
                for r in db.query(RefRunway).all()]
    finally:
        if own:
            db.close()


def aerodrome_ref(db: Session, icao: str) -> dict | None:
    """ARP and elevation of an aerodrome (from its runway rows)."""
    rws = db.query(RefRunway).filter_by(icao=icao).all()
    if not rws:
        return None
    lat = next((r.arp_lat for r in rws if r.arp_lat is not None), float(np.mean([r.thr_lat for r in rws])))
    lon = next((r.arp_lon for r in rws if r.arp_lon is not None), float(np.mean([r.thr_lon for r in rws])))
    elev = next((r.arp_elev_ft for r in rws if r.arp_elev_ft is not None),
                float(np.mean([r.thr_elev_ft or 0 for r in rws])))
    return {"icao": icao, "lat": lat, "lon": lon, "elev": elev}


def export_runways_csv(db: Session) -> str:
    cols = ["icao", "runway", "thr_lat", "thr_lon", "thr_elev_ft", "true_brg", "length_m", "width_m", "arp_lat",
            "arp_lon", "arp_elev_ft", "notes"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    for r in db.query(RefRunway).order_by(RefRunway.icao, RefRunway.runway):
        w.writerow([getattr(r, c) if getattr(r, c) is not None else "" for c in cols])
    return out.getvalue()


# ------------------------------------------------------------------ vertical limits
def parse_level(text: str, default: float) -> tuple[float, bool]:
    """'FL245' -> 24500, '10000 ft' -> 10000, 'SFC'/'GND' -> 0. Several values ('FL245/FL370') -> the first,
    flagged so the analyst can set an override (DR-006)."""
    t = (text or "").upper().replace(" ", "")
    if not t:
        return default, False
    parts = [p for p in re.split(r"[/;,]", t) if p]
    multi = len(parts) > 1 or "*" in t
    p = parts[0].rstrip("*")
    if p in ("SFC", "GND", "SURFACE"):
        return 0.0, multi
    if p.startswith("FL"):
        m = re.search(r"\d+", p)
        return (float(m.group()) * 100 if m else default), multi
    m = re.search(r"\d+", p)
    if m:
        return float(m.group()), multi
    return default, multi


# ------------------------------------------------------------------ NavEarth
LAYERS = {"UTA": "enroute_chart:uta_polygon", "TMA": "enroute_chart:tma_polygon", "CTA": "enroute_chart:cta_polygon",
          "fix": "enroute_chart:rep_point", "navaid": "enroute_chart:nav_aid_enr"}


def wfs_get(layer: str, url: str | None = None) -> list[dict]:
    base = (url or config.NAVEARTH_WFS).split("?")[0]
    params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature", "typeName": layer,
              "outputFormat": "application/json"}
    r = httpx.get(base, params=params, timeout=90)
    r.raise_for_status()
    try:
        return r.json().get("features", [])
    except ValueError as e:
        raise ValueError(f"NavEarth did not return GeoJSON for {layer}: {r.text[:200]}") from e


def store_sectors(db: Session, feats: list[dict], author: str, note: str) -> dict:
    """Replace the sector set, keeping capacities and overrides entered for sectors of the same name."""
    keep = {s.name: s for s in db.query(RefSector).all()}
    version = next_version(db, "sectors", author, note)
    db.query(RefSector).delete()
    n_override = 0
    for f in feats:
        geom = f.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        lo, m1 = parse_level(f["lower_raw"], 0)
        hi, m2 = parse_level(f["upper_raw"], 60000)
        old = keep.get(f["name"])
        s = RefSector(name=f["name"], kind=f["kind"], unit=f.get("unit", ""), lower_raw=f["lower_raw"],
                      upper_raw=f["upper_raw"], lower_ft=lo, upper_ft=hi, needs_override=m1 or m2,
                      airspace_class=f.get("class", ""), geometry=geom, area_nm2=round(polygon_area_nm2(geom), 1),
                      version=version)
        if old:
            s.override_lower_ft, s.override_upper_ft, s.declared_capacity = old.override_lower_ft, old.override_upper_ft, old.declared_capacity
        n_override += int(s.needs_override)
        db.add(s)
    db.commit()
    return {"sectors": len(feats), "need_override": n_override, "version": version}


def refresh_navearth(db: Session, author: str, url: str | None = None) -> dict:
    feats, errors = [], []
    for kind in ("UTA", "TMA", "CTA"):
        try:
            for f in wfs_get(LAYERS[kind], url):
                p = f.get("properties", {})
                feats.append({"name": (p.get("layer") or p.get("name") or "").strip(), "kind": kind,
                              "unit": (p.get("unt_srvc") or "").strip(), "lower_raw": str(p.get("lower") or ""),
                              "upper_raw": str(p.get("upper") or ""), "class": p.get("class") or "",
                              "geometry": f.get("geometry")})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kind}: {e}")
    out = {"errors": errors}
    if feats:
        out.update(store_sectors(db, feats, author, "NavEarth refresh"))
    pts = []
    for kind in ("fix", "navaid"):
        try:
            for f in wfs_get(LAYERS[kind], url):
                g, p = f.get("geometry") or {}, f.get("properties", {})
                if g.get("type") != "Point":
                    continue
                name = p.get("name_rep") or p.get("designator_nav") or p.get("name") or p.get("designator")
                if name:
                    pts.append(RefPoint(name=str(name).strip()[:16], kind=kind, lon=g["coordinates"][0], lat=g["coordinates"][1]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{kind}: {e}")
    if pts:
        db.query(RefPoint).delete()
        seen = set()
        for p in pts:
            if (p.name, p.kind) not in seen:
                seen.add((p.name, p.kind))
                db.add(p)
        next_version(db, "points", author, "NavEarth refresh")
        db.commit()
        out["points"] = len(seen)
    return out


def seed_demo_reference(db: Session):
    from . import demo
    if not db.query(RefSector).count():
        store_sectors(db, demo.demo_sectors(), "demo", "Demo sectors")
    if not db.query(RefPoint).count():
        for p in demo.demo_points():
            db.add(RefPoint(**p))
        db.commit()
    for a in db.query(Aerodrome):
        a.monitored = a.icao in demo.DEMO_AERODROMES
    from ..models import TdmRoute
    if not db.query(TdmRoute).count():
        for a, b, _ in demo.PAIRS:
            for x, y in ((a, b), (b, a)):
                db.add(TdmRoute(adep=x, ades=y, unit="Jakarta ACC" if "WIII" in (x, y) else "Ujung Pandang ACC"))
    db.commit()


# ------------------------------------------------------------------ airlines (DR-009)
AIRLINES = [("GIA", "Garuda Indonesia", "ID"), ("CTV", "Citilink", "ID"), ("LNI", "Lion Air", "ID"),
            ("BTK", "Batik Air", "ID"), ("WON", "Wings Air", "ID"), ("SJY", "Sriwijaya Air", "ID"),
            ("NIH", "NAM Air", "ID"), ("AWQ", "Indonesia AirAsia", "ID"), ("IDX", "Indonesia AirAsia (old)", "ID"),
            ("TNU", "TransNusa", "ID"), ("PAS", "Pelita Air", "ID"), ("SJV", "Super Air Jet", "ID"),
            ("TGN", "Trigana Air", "ID"), ("XAR", "Xpress Air", "ID"), ("SQS", "Susi Air", "ID"),
            ("MNA", "Merpati", "ID"), ("TMG", "Tri-MG", "ID"), ("CJN", "Cardig Air", "ID"), ("JTA", "My Indo Airlines", "ID"),
            ("SIA", "Singapore Airlines", "SG"), ("SLK", "Scoot (old)", "SG"), ("TGW", "Scoot", "SG"), ("JSA", "Jetstar Asia", "SG"),
            ("MAS", "Malaysia Airlines", "MY"), ("AXM", "AirAsia", "MY"), ("MXD", "Batik Air Malaysia", "MY"),
            ("FFM", "Firefly", "MY"), ("THA", "Thai Airways", "TH"), ("AIQ", "Thai AirAsia", "TH"), ("TLM", "Thai Lion Air", "TH"),
            ("QFA", "Qantas", "AU"), ("JST", "Jetstar", "AU"), ("VOZ", "Virgin Australia", "AU"), ("ANZ", "Air New Zealand", "NZ"),
            ("CPA", "Cathay Pacific", "HK"), ("UAE", "Emirates", "AE"), ("QTR", "Qatar Airways", "QA"), ("ETD", "Etihad", "AE"),
            ("SVA", "Saudia", "SA"), ("KAL", "Korean Air", "KR"), ("AAR", "Asiana", "KR"), ("JAL", "Japan Airlines", "JP"),
            ("ANA", "All Nippon Airways", "JP"), ("CES", "China Eastern", "CN"), ("CSN", "China Southern", "CN"),
            ("CCA", "Air China", "CN"), ("EVA", "EVA Air", "TW"), ("CAL", "China Airlines", "TW"), ("PAL", "Philippine Airlines", "PH"),
            ("CEB", "Cebu Pacific", "PH"), ("VJC", "VietJet", "VN"), ("HVN", "Vietnam Airlines", "VN"), ("THY", "Turkish Airlines", "TR"),
            ("KLM", "KLM", "NL"), ("DLH", "Lufthansa", "DE"), ("AFR", "Air France", "FR"), ("BAW", "British Airways", "GB"),
            ("FDX", "FedEx", "US"), ("UPS", "UPS", "US"), ("CKS", "Kalitta Air", "US"), ("RBA", "Royal Brunei", "BN")]


def seed_airlines(db: Session):
    if db.query(Airline).count():
        return
    for d, n, c in AIRLINES:
        db.add(Airline(designator=d, name=n, country=c))
    db.commit()


def operator_of(callsign: str, known: set[str]) -> str:
    cs = (callsign or "").strip().upper()
    m = re.match(r"^([A-Z]{3})\d", cs)
    if m and m.group(1) in known:
        return m.group(1)
    if m:
        return "OTH"
    return "GA"   # registration-style callsign: general aviation / private


def known_airlines(db: Session) -> set[str]:
    return {a.designator for a in db.query(Airline).all()}
