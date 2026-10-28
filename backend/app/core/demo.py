"""Synthetic demo data: flights, weather and sectors for a few Indonesian aerodromes.

Used when NAVRUNA_DEMO=1 (try NavRUNA without the AirNav network), by the tests, and to make the manual's
screenshots. Everything is deterministic for a given day, so all modules see the same flights.
Values are plausible, not real: never use demo results for safety decisions.
"""
from __future__ import annotations

import calendar
import hashlib
import math
from datetime import date, datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from .geo import bearing_deg, haversine_nm

DEMO_AERODROMES = {   # icao: (lat, lon, elev ft, runways in use pairs, daily arrivals)
    "WIII": (-6.1236, 106.6611, 28, [("07L", "25R"), ("07R", "25L")], 300),
    "WARR": (-7.3808, 112.7864, 11, [("10", "28")], 140),
    "WADD": (-8.7475, 115.1692, 14, [("09", "27")], 150),
    "WAAA": (-5.0608, 119.5544, 44, [("03", "21")], 90),
}
PAIRS = [("WIII", "WARR", 32), ("WIII", "WADD", 30), ("WIII", "WAAA", 18), ("WARR", "WADD", 14),
         ("WARR", "WAAA", 12), ("WADD", "WAAA", 10)]
OPERATORS = ["GIA", "LNI", "BTK", "CTV", "SJY", "AWQ", "IDX", "TNU", "WON", "NIH"]
TYPES = {"GIA": "B738", "LNI": "B739", "BTK": "A320", "CTV": "A320", "SJY": "B738", "AWQ": "A320",
         "IDX": "A320", "TNU": "A320", "WON": "AT72", "NIH": "AT72"}


def _rng(*key) -> np.random.Generator:
    h = hashlib.sha256("|".join(map(str, key)).encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def _wet(d: date) -> bool:
    return d.month in (11, 12, 1, 2, 3, 4)


# ------------------------------------------------------------------ weather
@lru_cache(maxsize=64)
def weather(icao: str, d: date) -> list[dict]:
    """Half-hourly METARs (plus a SPECI when a storm starts) for one aerodrome and UTC day."""
    lat, lon, elev, _, _ = DEMO_AERODROMES[icao]
    rng = _rng("wx", icao, d)
    base_dir = float(rng.choice([90, 110, 270, 250, 80, 290]))
    qnh0 = 1010 + rng.normal(0, 1.5)
    storm_start = rng.integers(6, 11) if rng.random() < (0.45 if _wet(d) else 0.12) else None   # UTC hour (afternoon local)
    out = []
    for k in range(48):
        t = datetime(d.year, d.month, d.day) + timedelta(minutes=30 * k)
        h = t.hour + t.minute / 60
        wdir = (base_dir + rng.normal(0, 15)) % 360
        wspd = max(0.0, 6 + 4 * math.sin((h - 3) / 24 * 2 * math.pi) + rng.normal(0, 2))
        gust = None
        storm = storm_start is not None and storm_start <= h < storm_start + 2.5
        near = storm_start is not None and (storm_start - 1 <= h < storm_start or storm_start + 2.5 <= h < storm_start + 3.5)
        vis = 9999
        wx = ""
        cloud = "FEW020"
        if storm:
            wdir = (base_dir + 160 + rng.normal(0, 30)) % 360
            wspd = 14 + rng.normal(0, 4)
            gust = wspd + 10 + rng.normal(0, 3)
            vis = int(rng.choice([2000, 3000, 4000]))
            wx = rng.choice(["TSRA", "+TSRA", "TS"])
            cloud = "SCT015CB BKN080"
        elif near:
            wx = "VCTS"
            cloud = "FEW018CB"
        qnh = round(qnh0 + 1.2 * math.sin((h - 4) / 12 * 2 * math.pi) + (-1.5 if storm else 0))
        ttt = round(26 + 5 * math.sin((h - 2) / 24 * 2 * math.pi))
        wind = f"{int(round(wdir / 10) * 10) % 360 or 360:03d}{int(round(wspd)):02d}" + (f"G{int(round(gust)):02d}" if gust else "") + "KT"
        raw = f"METAR {icao} {t:%d%H%M}Z {wind} {vis:04d} {wx + ' ' if wx else ''}{cloud} {ttt:02d}/{ttt - 3:02d} Q{qnh} NOSIG="
        out.append({"icao": icao, "observed": t, "kind": "METAR", "raw": raw, "received": t + timedelta(minutes=4)})
        if storm and storm_start is not None and abs(h - storm_start) < 0.01:
            ts = t + timedelta(minutes=12)
            out.append({"icao": icao, "observed": ts, "kind": "SPECI", "received": ts + timedelta(minutes=3),
                        "raw": raw.replace("METAR", "SPECI").replace(f"{t:%d%H%M}Z", f"{ts:%d%H%M}Z")})
    return out


def runway_in_use(icao: str, d: date, hour: float, runways: dict) -> str:
    """Runway end facing the wind (runways: name -> bearing)."""
    obs = weather(icao, d)
    w = min(obs, key=lambda o: abs((o["observed"] - datetime(d.year, d.month, d.day)).total_seconds() / 3600 - hour))
    try:
        part = [p for p in w["raw"].split() if p.endswith("KT")][0]
        wdir = float(part[:3])
    except (IndexError, ValueError):
        wdir = 90.0
    best = min(runways, key=lambda r: abs(((runways[r] - wdir + 180) % 360) - 180))
    return best


# ------------------------------------------------------------------ flights
def _dest_point(lat, lon, brg, dist_nm):
    R = 3440.065
    lat1, lon1, b = math.radians(lat), math.radians(lon), math.radians(brg)
    d = dist_nm / R
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(b))
    lon2 = lon1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)


def _fly(wps: list[tuple], t0: datetime, step_s: int = 5) -> pd.DataFrame:
    """wps: [(lat, lon, alt_ft, gs_kt)], flown at linearly varying speed and altitude."""
    rows = []
    t = 0.0
    for (la1, lo1, a1, s1), (la2, lo2, a2, s2) in zip(wps, wps[1:]):
        dist = float(haversine_nm(la1, lo1, la2, lo2))
        if dist < 1e-6:
            continue
        brg = float(bearing_deg(la1, lo1, la2, lo2))
        pos = 0.0
        while pos < dist:
            f = pos / dist
            spd = s1 + (s2 - s1) * f
            la, lo = _dest_point(la1, lo1, brg, pos)
            rows.append((t, lo, la, a1 + (a2 - a1) * f, spd))
            pos += spd * step_s / 3600
            t += step_s
    la, lo, a, s = wps[-1]
    rows.append((t, lo, la, a, s))
    df = pd.DataFrame(rows, columns=["dt", "lon", "lat", "alt", "gs"])
    df["t"] = (calendar.timegm(t0.timetuple()) + df.dt).round().astype("int64")
    return df.drop(columns="dt")


def _approach_wps(rw: dict, ga_at_nm: float | None, rng) -> list[tuple]:
    """From a point 25 NM out to touchdown, optionally with a go-around and a second approach."""
    tl, tn, brg, elev = rw["thr_lat"], rw["thr_lon"], rw["true_brg"], rw["thr_elev_ft"] or 0
    back = (brg + 180) % 360
    side = 90 if rng.random() < 0.5 else -90
    def on_cl(d, h, s, off=0.0):
        la, lo = _dest_point(tl, tn, back, d)
        if off:
            la, lo = _dest_point(la, lo, (back + side) % 360, off)
        return (la, lo, elev + h, s)
    gp = lambda d: d * 318.4 + 50
    wps = [on_cl(22, 7000, 250, 10 + rng.normal(0, 1)), on_cl(14, 3500, 210, 5), on_cl(10, 3000, 170)]
    fins = [on_cl(d, gp(d) + rng.normal(0, 25), 150 - (4 - min(d, 4)) * 4) for d in (8, 6, 4, 2, 1)]
    if ga_at_nm is None:
        return wps + fins + [on_cl(0, 50, 140), on_cl(-0.4, 0, 120), on_cl(-1.0, 0, 40)]
    keep = [w for w, d in zip(fins, (8, 6, 4, 2, 1)) if d > ga_at_nm]
    h_ga = gp(ga_at_nm)
    la, lo = _dest_point(tl, tn, back, ga_at_nm)
    ga = [(la, lo, elev + h_ga - 30, 145)]
    la1, lo1 = _dest_point(la, lo, brg, 1.5)                    # positive climb, about 1,500 ft/min
    ga.append((la1, lo1, elev + h_ga + 1000, 160))
    # climb straight ahead, then a circuit back to a 10 NM final
    la2, lo2 = _dest_point(tl, tn, brg, 3)
    la3, lo3 = _dest_point(la2, lo2, (brg + side) % 360, 6)
    la4, lo4 = _dest_point(la3, lo3, back, 14)
    circuit = [(la2, lo2, elev + max(2500, h_ga + 1200), 180), (la3, lo3, elev + 3000, 200), (la4, lo4, elev + 3000, 200), on_cl(10, 3000, 170)]
    second = [on_cl(d, gp(d), 150) for d in (8, 6, 4, 2, 1)] + [on_cl(0, 50, 140), on_cl(-0.4, 0, 120), on_cl(-1.0, 0, 40)]
    return wps + keep + ga + circuit + second


def _runways(icao: str, rwys: list[dict]) -> dict:
    return {r["runway"]: r for r in rwys if r["icao"] == icao and r.get("true_brg") is not None}


@lru_cache(maxsize=16)
def _day_flights(d: date, rwy_key: tuple) -> list[tuple[dict, pd.DataFrame]]:
    rwys = [dict(zip(("icao", "runway", "thr_lat", "thr_lon", "thr_elev_ft", "true_brg"), r)) for r in rwy_key]
    rng = _rng("day", d)
    flights = []
    k = 0
    for a, b, n in PAIRS:
        for direction in ((a, b), (b, a)):
            adep, ades = direction
            for i in range(int(n * (0.85 + 0.3 * rng.random()))):
                k += 1
                op = OPERATORS[int(rng.integers(0, len(OPERATORS)))]
                if op in ("WON", "NIH") and ("WIII" in direction):
                    op = "LNI"
                hour = float(np.clip(rng.normal(9, 4.5), 0, 22.5))
                t0 = datetime(d.year, d.month, d.day) + timedelta(hours=hour)
                df, meta = _make_flight(adep, ades, op, t0, rwys, rng, d, k)
                if df is not None:
                    flights.append((meta, df))
    return flights


def _make_flight(adep, ades, op, t0, rwys, rng, d, k):
    A, B = DEMO_AERODROMES[adep], DEMO_AERODROMES[ades]
    rA, rB = _runways(adep, rwys), _runways(ades, rwys)
    if not rA or not rB:
        return None, None
    hourA = (t0 - datetime(d.year, d.month, d.day)).total_seconds() / 3600
    dep_rw = rA[runway_in_use(adep, d, hourA, {r: v["true_brg"] for r, v in rA.items()})]
    dist = float(haversine_nm(A[0], A[1], B[0], B[1]))
    hourB = hourA + dist / 430 + 0.4
    arr_rw = rB[runway_in_use(ades, d, min(hourB, 23.9), {r: v["true_brg"] for r, v in rB.items()})]
    typ = TYPES[op]
    cruise = 24000 if typ.startswith("AT") else int(rng.choice([31000, 33000, 35000, 37000]))
    vcr = 270 if typ.startswith("AT") else 450
    # departure: straight out 8 NM climbing
    la1, lo1 = _dest_point(dep_rw["thr_lat"], dep_rw["thr_lon"], dep_rw["true_brg"], 0.8)
    la2, lo2 = _dest_point(dep_rw["thr_lat"], dep_rw["thr_lon"], dep_rw["true_brg"], 8)
    eA = dep_rw["thr_elev_ft"] or 0
    wps = [(dep_rw["thr_lat"], dep_rw["thr_lon"], eA, 30), (la1, lo1, eA, 150), (la2, lo2, eA + 4000, 220)]
    # cruise with route variants and occasional deviations
    brg = float(bearing_deg(A[0], A[1], B[0], B[1]))
    mid_la, mid_lo = _dest_point(A[0], A[1], brg, dist / 2)
    variant = rng.random()
    if variant < 0.25:
        mid_la, mid_lo = _dest_point(mid_la, mid_lo, (brg + 90) % 360, 25)
    if rng.random() < 0.04:          # weather deviation
        mid_la, mid_lo = _dest_point(mid_la, mid_lo, (brg - 90) % 360, float(rng.uniform(6, 15)))
    q1 = _dest_point(A[0], A[1], brg, min(60, dist * 0.25))
    wps += [(q1[0], q1[1], cruise, vcr), (mid_la, mid_lo, cruise, vcr)]
    # arrival
    storm = any(o["observed"].hour == int(min(hourB, 23)) and ("TS" in o["raw"]) for o in weather(ades, d))
    p_ga = 0.035 if storm else 0.004
    ga_at = float(rng.uniform(0.6, 5.0)) if rng.random() < p_ga else None
    app = _approach_wps(arr_rw, ga_at, rng)
    wps += app
    # touch-and-go training flights are generated separately; here a rare low approach
    df = _fly(wps, t0)
    eB = arr_rw["thr_elev_ft"] or 0
    # ground: no pressure altitude (like real ADS-B), slow
    ground = (df.alt <= eA + 1) & (df.index < 20) | (df.alt <= eB + 1) & (df.gs < 130)
    qa = 1011.0
    df["alt"] = df.alt - 27.3 * (qa - 1013.25)
    df.loc[ground, "alt"] = np.nan
    # a few random coverage gaps
    if rng.random() < 0.05:
        a0 = int(rng.integers(50, max(51, len(df) - 60)))
        df = df.drop(df.index[a0:a0 + int(rng.integers(15, 40))])
    cs = f"{op}{100 + (k * 7) % 900}"
    meta = {"gufi": f"DEMO-{d:%Y%m%d}-{k:05d}", "callsign": cs, "icao24": f"8a{k:04x}", "registration": f"PK-{chr(65 + k % 26)}{chr(65 + (k // 26) % 26)}{chr(65 + (k // 676) % 26)}",
            "adep": adep, "ades": ades, "dof": d, "aircraft_type": typ}
    return df.reset_index(drop=True), meta


def training_flights(d: date, rwys: list[dict]) -> list[tuple[dict, pd.DataFrame]]:
    """Two touch-and-go circuits per day at WAAA (excluded from go-around rates)."""
    rng = _rng("tng", d)
    rB = _runways("WAAA", rwys)
    out = []
    if not rB:
        return out
    for j in range(2):
        rw = list(rB.values())[j % len(rB)]
        tl, tn, brg, e = rw["thr_lat"], rw["thr_lon"], rw["true_brg"], rw["thr_elev_ft"] or 0
        back = (brg + 180) % 360
        p = lambda dd, h, s: (*_dest_point(tl, tn, back, dd), e + h, s)
        la, lo = _dest_point(tl, tn, brg, 3)
        la2, lo2 = _dest_point(la, lo, (brg + 90) % 360, 3)
        la3, lo3 = _dest_point(la2, lo2, back, 8)
        wps = [p(6, 1500, 110), p(4, 1300, 100), p(2, 640, 90), p(0.5, 160, 80), p(0, 15, 75), p(-0.5, 20, 75),
               (la, lo, e + 1000, 95), (la2, lo2, e + 1500, 110), (la3, lo3, e + 1500, 110), p(6, 1500, 110)]
        t0 = datetime(d.year, d.month, d.day, 2 + 3 * j)
        df = _fly(wps, t0)
        df["alt"] = df.alt + 61
        out.append(({"gufi": f"DEMO-{d:%Y%m%d}-T{j}", "callsign": f"PKTNG{j}", "icao24": f"8b00{j:02x}",
                     "registration": f"PK-TN{j}", "adep": "WAAA", "ades": "WAAA", "dof": d, "aircraft_type": "C172"}, df))
    return out


_mem: dict = {}


def day_flights(d: date, rwys: list[dict]) -> list[tuple[dict, pd.DataFrame]]:
    """Flights of one demo day; cached in memory and on disk, since every module reads the same days."""
    import pickle
    from .. import config
    key = tuple(sorted((r["icao"], r["runway"], r["thr_lat"], r["thr_lon"], r["thr_elev_ft"], r["true_brg"])
                       for r in rwys if r["icao"] in DEMO_AERODROMES and r.get("true_brg") is not None))
    h = hashlib.sha1(repr(key).encode()).hexdigest()[:10]
    if (d, h) in _mem:
        return _mem[(d, h)]
    f = config.data_path("demo_cache", f"{d}_{h}.pkl")
    if f.exists():
        with open(f, "rb") as fh:
            out = pickle.load(fh)
    else:
        out = list(_day_flights.__wrapped__(d, key)) + training_flights(d, rwys)
        with open(f, "wb") as fh:
            pickle.dump(out, fh)
    if len(_mem) > 40:
        _mem.pop(next(iter(_mem)))
    _mem[(d, h)] = out
    return out


# ------------------------------------------------------------------ sectors
def demo_sectors() -> list[dict]:
    """A coarse sector layout: two upper sectors, one TMA per demo aerodrome."""
    def circle(lat, lon, r_nm, n=36):
        pts = [_dest_point(lat, lon, 360 * i / n, r_nm) for i in range(n)]
        ring = [[round(p[1], 4), round(p[0], 4)] for p in pts]
        return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}
    rect = lambda x0, y0, x1, y1: {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}
    out = [
        {"name": "DEMO UPPER WEST", "kind": "UTA", "unit": "Jakarta ACC", "lower_raw": "FL245", "upper_raw": "FL600", "geometry": rect(104, -10, 111, -3)},
        {"name": "DEMO UPPER EAST", "kind": "UTA", "unit": "Ujung Pandang ACC", "lower_raw": "FL245", "upper_raw": "FL600", "geometry": rect(111, -10, 122, -3)},
    ]
    for icao, unit in (("WIII", "Jakarta APP"), ("WARR", "Surabaya APP"), ("WADD", "Bali APP"), ("WAAA", "Makassar APP")):
        lat, lon = DEMO_AERODROMES[icao][:2]
        out.append({"name": f"DEMO {icao} TMA", "kind": "TMA", "unit": unit, "lower_raw": "1500 ft", "upper_raw": "FL245",
                    "geometry": circle(lat, lon, 50)})
    return out


def demo_points() -> list[dict]:
    out = []
    for i, (a, b, _) in enumerate(PAIRS):
        A, B = DEMO_AERODROMES[a], DEMO_AERODROMES[b]
        brg = float(bearing_deg(A[0], A[1], B[0], B[1]))
        dist = float(haversine_nm(A[0], A[1], B[0], B[1]))
        for j, f in enumerate((0.3, 0.5, 0.7)):
            la, lo = _dest_point(A[0], A[1], brg, dist * f)
            out.append({"name": f"D{i}{j}{'ABC'[j]}X"[:5], "kind": "fix", "lat": round(la, 4), "lon": round(lo, 4)})
    return out
