"""BMKG aviation weather: client, METAR/SPECI parser and lookup (SRS 3, DR-007, DR-008).

The BMKG website sits behind bot protection that blocks some HTTP clients; requests therefore identify
themselves like curl and encode spaces as %20, which BMKG accepts (verified 28 Oct 2026).
"""
from __future__ import annotations

import math
import re
import urllib.parse
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from .. import config
from ..models import TafReport, WxReport, now

WIND = re.compile(r"\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)\b")
CLOUD = re.compile(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3}|///)(CB|TCU)?\b")
TEMP = re.compile(r"\b(M?\d{2})/(M?\d{2})?\b")
QNH = re.compile(r"\bQ(\d{4})\b")
WX = re.compile(r"(?<=\s)([+-]|VC)?(TS|SH|FZ|MI|BC|DR|BL)?(RA|DZ|SN|GR|GS|PL|SG|UP)?(BR|FG|HZ|FU|DU|SA|VA)?(?=\s)")
WS = re.compile(r"\bWS\s+(?:R(?:WY)?\d{2}[LCR]?|ALL\s+RWY)\b")


def _t(v: str | None) -> float | None:
    if not v:
        return None
    return -float(v[1:]) if v.startswith("M") else float(v)


def parse_metar(raw: str) -> dict:
    """Fields used by NavRUNA. Returns parse_ok False when wind or QNH cannot be read."""
    text = " " + (raw or "").replace("=", " ").strip() + " "
    body = text.split(" NOSIG")[0].split(" TEMPO")[0].split(" BECMG")[0]   # ignore trend groups
    out = {"wind_dir": None, "wind_kt": None, "gust_kt": None, "vis_m": None, "weather": "", "ts": False,
           "vcts": False, "precip": 0, "cb": False, "tcu": False, "ceiling_ft": None, "temp_c": None, "dew_c": None,
           "qnh_hpa": None, "wind_shear": "", "parse_ok": True}
    w = WIND.search(body)
    if w:
        f = 1.94384 if w.group(4) == "MPS" else 1.0
        out["wind_dir"] = None if w.group(1) == "VRB" else float(w.group(1))
        out["wind_kt"] = float(w.group(2)) * f
        out["gust_kt"] = float(w.group(3)) * f if w.group(3) else None
    if " CAVOK " in body:
        out["vis_m"] = 10000.0
    elif w:
        toks = body[w.end():].split()
        if toks and re.fullmatch(r"\d{3}V\d{3}", toks[0]):
            toks = toks[1:]
        if toks and re.fullmatch(r"\d{4}", toks[0]):
            out["vis_m"] = 10000.0 if toks[0] == "9999" else float(toks[0])
    codes = []
    for m in WX.finditer(body):
        tok = m.group(0)
        if not tok or not any(m.groups()[1:]):
            continue
        codes.append(tok)
        if "TS" in tok:
            if tok.startswith("VC"):
                out["vcts"] = True
            else:
                out["ts"] = True
        if any(p in tok for p in ("RA", "DZ", "SN", "GR", "GS")):
            out["precip"] = max(out["precip"], 3 if tok.startswith("+") else 1 if tok.startswith("-") else 2)
    out["weather"] = " ".join(codes)[:64]
    ceil = None
    for amt, hgt, typ in CLOUD.findall(body):
        if typ == "CB":
            out["cb"] = True
        if typ == "TCU":
            out["tcu"] = True
        if amt in ("BKN", "OVC", "VV") and hgt.isdigit():
            h = float(hgt) * 100
            ceil = h if ceil is None else min(ceil, h)
    out["ceiling_ft"] = ceil
    tm = TEMP.search(body)
    if tm:
        out["temp_c"], out["dew_c"] = _t(tm.group(1)), _t(tm.group(2))
    q = QNH.search(body)
    if q:
        out["qnh_hpa"] = float(q.group(1))
    ws = WS.search(text)
    if ws:
        out["wind_shear"] = ws.group(0)
    out["parse_ok"] = bool(w) and bool(q)
    return out


def _bmkg_get(path: str, params: dict) -> list[dict]:
    if not config.BMKG_TOKEN:
        raise RuntimeError("BMKG is not configured (NAVRUNA_BMKG_TOKEN)")
    url = f"{config.BMKG_URL.rstrip('/')}{path}?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    r = httpx.get(url, headers={"Authorization": f"Bearer {config.BMKG_TOKEN}", "Accept": "application/json",
                                "User-Agent": "curl/8.7.1"}, timeout=90)
    if r.status_code == 401:
        raise RuntimeError("BMKG refused the token (401)")
    if r.text.lstrip().startswith("<"):
        raise RuntimeError(f"BMKG returned a web page instead of data (HTTP {r.status_code}); its bot protection may be blocking the server")
    r.raise_for_status()
    js = r.json()
    if isinstance(js, list):
        return js
    rows = []
    for v in js.values():
        if isinstance(v, list):
            rows += v
    return rows


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("T", " ")[:19])
    except ValueError:
        return None


def store_reports(db: Session, rows: list[dict]) -> tuple[int, int]:
    """Upsert keeping the report received last per station, time and kind (DR-007). Returns (written, unparsed)."""
    written = bad = 0
    seen: dict[tuple, WxReport] = {}
    for r in rows:
        raw = (r.get("data_text") or r.get("raw") or "").strip()
        icao = (r.get("icao_code") or r.get("icao") or "").upper()
        obs = r.get("observed") or _dt(r.get("observed_time"))
        if not raw or not icao or obs is None:
            continue
        kind = r.get("kind") or ("SPECI" if raw.startswith("SPECI") or r.get("type_code") == "SP" else "METAR")
        rec = r.get("received") or _dt(r.get("insert_time")) or now()
        key = (icao, obs, kind)
        old = seen.get(key) or db.query(WxReport).filter_by(icao=icao, observed=obs, kind=kind).first()
        if old and old.received and rec and old.received >= rec:
            continue
        p = parse_metar(raw)
        bad += int(not p["parse_ok"])
        if old is None:
            old = WxReport(icao=icao, observed=obs, kind=kind)
            db.add(old)
        seen[key] = old
        old.raw, old.received = raw, rec
        for k, v in p.items():
            setattr(old, k, v)
        written += 1
    db.commit()
    return written, bad


def fetch(db: Session, stations: list[str], t0: datetime, t1: datetime) -> dict:
    """Download METAR+SPECI (and TAF) for the stations, one day per request."""
    if config.DEMO:
        from . import demo
        rows = []
        d = t0.date()
        while d <= t1.date():
            for s in stations:
                if s in demo.DEMO_AERODROMES:
                    rows += demo.weather(s, d)
            d += timedelta(days=1)
        w, bad = store_reports(db, rows)
        return {"reports": w, "unparsed": bad, "demo": True}
    total = bad = 0
    st = ",".join(stations)
    t = t0
    while t < t1:
        nxt = min(t + timedelta(days=1), t1)
        rows = _bmkg_get(f"/metar-speci/{st}", {"from": f"{t:%Y-%m-%d %H:%M:%S}", "to": f"{nxt - timedelta(seconds=1):%Y-%m-%d %H:%M:%S}"})
        w, b = store_reports(db, rows)
        total, bad = total + w, bad + b
        try:
            for r in _bmkg_get(f"/taf/{st}", {"from": f"{t:%Y-%m-%d %H:%M:%S}", "to": f"{nxt:%Y-%m-%d %H:%M:%S}"}):
                issued = _dt(r.get("observed_time") or r.get("filling_time"))
                icao = (r.get("icao_code") or "").upper()
                if issued and icao and not db.query(TafReport).filter_by(icao=icao, issued=issued).first():
                    db.add(TafReport(icao=icao, issued=issued, raw=r.get("data_text", "")))
            db.commit()
        except Exception:  # noqa: BLE001 - TAF is optional (FR-GA-035)
            db.rollback()
        t = nxt
    return {"reports": total, "unparsed": bad}


class WxLookup:
    """Nearest-in-time report per station, loaded once per job."""

    def __init__(self, db: Session, icao: str, t0: datetime, t1: datetime, max_age_min: float = 75):
        self.max_age = timedelta(minutes=max_age_min)
        self.rows = db.query(WxReport).filter(WxReport.icao == icao, WxReport.observed >= t0 - self.max_age,
                                              WxReport.observed <= t1 + self.max_age).order_by(WxReport.observed).all()
        self.times = [r.observed for r in self.rows]

    def at(self, t: datetime) -> WxReport | None:
        if not self.rows:
            return None
        import bisect
        i = bisect.bisect_left(self.times, t)
        cands = [self.rows[j] for j in (i - 1, i) if 0 <= j < len(self.rows)]
        best = min(cands, key=lambda r: abs((r.observed - t).total_seconds()))
        return best if abs(best.observed - t) <= self.max_age else None


def wind_components(wx: WxReport | None, runway_brg: float) -> tuple[float | None, float | None]:
    """(headwind, crosswind) in kt; negative headwind = tailwind; crosswind as magnitude."""
    if wx is None or wx.wind_kt is None:
        return None, None
    if wx.wind_dir is None:
        return 0.0, float(wx.wind_kt) if wx.wind_kt else 0.0
    a = math.radians(wx.wind_dir - runway_brg)
    return round(wx.wind_kt * math.cos(a), 1), round(abs(wx.wind_kt * math.sin(a)), 1)


def days_missing(db: Session, icao: str, d0: date, d1: date) -> list[date]:
    have = {r[0].date() for r in db.query(WxReport.observed).filter(WxReport.icao == icao,
                                                                    WxReport.observed >= datetime(d0.year, d0.month, d0.day),
                                                                    WxReport.observed < datetime(d1.year, d1.month, d1.day) + timedelta(days=1))}
    out, d = [], d0
    while d <= d1:
        if d not in have:
            out.append(d)
        d += timedelta(days=1)
    return out
