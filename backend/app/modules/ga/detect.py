"""Approach and go-around detection (SRS 5.1, FR-GA-001..011, DR-002, DR-003, DR-010).

For one aerodrome and UTC day:
  1. read every flight within `ga.fetch_radius_nm` of the aerodrome from NavTrace;
  2. convert pressure altitude to height above each threshold with the nearest METAR/SPECI QNH;
  3. find approaches: consecutive points inside a runway end's final corridor, aligned with the runway,
     after a descent;
  4. classify each approach: landed, go-around (sustained climb of at least 300 ft above the lowest point,
     confirmed for 20 s), touch-and-go, low approach, or other;
  5. add gate values, sequence (time to leader, time since last departure), weather and arrival efficiency;
  6. store one row per approach, keeping analyst validations across re-processing.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ...core import geo
from ...core.refdata import aerodrome_ref, current_version, known_airlines, operator_of
from ...core.weather import WxLookup, wind_components
from ...models import GaApproach, GaCoverage, GaDeparture, RefRunway, now, utc_from_ts

GATES_NM = (6.0, 4.0, 2.0)
GATES_FT = (1000.0, 500.0)


@dataclass
class Rwy:
    name: str
    lat: float
    lon: float
    elev: float
    brg: float
    x: float = 0.0
    y: float = 0.0
    ux: float = 0.0
    uy: float = 0.0


def _runways(db: Session, icao: str, lon0: float, lat0: float) -> list[Rwy]:
    out = []
    for r in db.query(RefRunway).filter_by(icao=icao).all():
        if r.true_brg is None:
            continue
        x, y = geo.to_xy(r.thr_lon, r.thr_lat, lon0, lat0)
        b = math.radians(r.true_brg)
        out.append(Rwy(r.runway, r.thr_lat, r.thr_lon, r.thr_elev_ft or 0.0, r.true_brg, float(x), float(y),
                       math.sin(b), math.cos(b)))
    return out


def _vr(t, h, n):
    """Vertical rate ft/min from consecutive points, smoothed over n points (DR-003)."""
    if len(t) < 2:
        return np.zeros(len(t))
    dt = np.diff(t).astype(float)
    dt[dt <= 0] = 1
    v = np.diff(h) / dt * 60
    v = np.append(v, v[-1])
    return geo.smooth(v, n)


def _interp_at(xp, fp, x):
    """Linear interpolation of fp at x along a monotone segment xp (either direction)."""
    xp, fp = np.asarray(xp, float), np.asarray(fp, float)
    if len(xp) < 2:
        return None
    for i in range(len(xp) - 1):
        a, b = xp[i], xp[i + 1]
        if (a - x) * (b - x) <= 0 and a != b:
            f = (x - a) / (b - a)
            return float(fp[i] + f * (fp[i + 1] - fp[i]))
    return None


def detect_day(db: Session, src, icao: str, day: date, P: dict, param_version: int, ctx=None) -> dict:
    ref = aerodrome_ref(db, icao)
    if ref is None:
        raise ValueError(f"No runway data for {icao}")
    lon0, lat0 = ref["lon"], ref["lat"]
    rwys = _runways(db, icao, lon0, lat0)
    if not rwys:
        raise ValueError(f"No usable runway ends for {icao}")
    d0 = datetime(day.year, day.month, day.day)
    wx = WxLookup(db, icao, d0 - timedelta(hours=2), d0 + timedelta(hours=26), P["data.max_wx_age_min"])
    if not wx.rows and ctx is not None:
        ctx.warn(f"{icao} {day}: no weather reports; QNH 1013.25 assumed and weather fields left empty")
    airlines = known_airlines(db)
    k_qnh = P["data.qnh_ft_per_hpa"]
    approaches: list[dict] = []
    departures: list[dict] = []
    flights_seen = 0
    gap_flights = 0
    n_read = 0

    for meta, pts in src.flights_near(lon0, lat0, P["ga.fetch_radius_nm"], day):
        n_read += len(pts)
        if pts.alt.dropna().empty:
            continue
        low = pts.alt.isna() | (pts.alt < P["ga.fetch_ceiling_ft"])
        pts = pts[low].reset_index(drop=True)
        if len(pts) < 3:
            continue
        flights_seen += 1
        t = pts.t.values.astype(float)
        x, y = geo.to_xy(pts.lon.values, pts.lat.values, lon0, lat0)
        gs = pts.gs.fillna(0).values.astype(float)
        # QNH at the flight's lowest point (one value per flight is enough within an hour)
        i_low = int(np.nanargmin(np.where(pts.alt.isna(), -1e9, pts.alt.values)))
        w_low = wx.at(utc_from_ts(t[i_low]))
        qnh = (w_low.qnh_hpa if w_low and w_low.qnh_hpa else 1013.25)
        alt_qnh = pts.alt.values + k_qnh * (qnh - 1013.25)        # altitude above mean sea level (DR-002)
        on_ground = np.isnan(alt_qnh)
        track = geo.track_from_xy(geo.smooth(x, P["data.smoothing_points"]), geo.smooth(y, P["data.smoothing_points"]))
        airborne_gs = gs[~on_ground]
        if len(airborne_gs) and np.median(airborne_gs) < P["ga.min_speed_kt"]:
            continue                                                 # rotorcraft (FR-GA-006)
        below10k = t[(np.nan_to_num(alt_qnh, nan=0) < 10000)]
        max_gap = float(np.max(np.diff(below10k))) if len(below10k) > 1 else 0.0
        gap_flights += int(max_gap > 60)
        cs = (meta.get("callsign") or "").strip().upper()
        base = {"gufi": meta["gufi"], "callsign": cs, "registration": meta.get("registration") or "",
                "icao24": meta.get("icao24") or "", "operator": operator_of(cs, airlines),
                "aircraft_type": meta.get("aircraft_type") or "", "adep": meta.get("adep") or "",
                "ades": meta.get("ades") or "", "max_gap_s": max_gap}
        found = []
        dep_cands = []
        for rw in rwys:
            h = np.where(on_ground, 0.0, alt_qnh - rw.elev)
            rx, ry = x - rw.x, y - rw.y
            dist = -(rx * rw.ux + ry * rw.uy)                  # NM before the threshold (positive on final)
            lat_off = rx * rw.uy - ry * rw.ux                   # NM right of the centreline
            half = P["ga.corridor_halfwidth_nm"] + P["ga.corridor_growth"] * np.maximum(dist, 0)
            aligned = geo.angle_diff(track, rw.brg) <= P["ga.track_tolerance_deg"]
            inside = (dist >= -0.5) & (dist <= P["ga.corridor_length_nm"]) & (np.abs(lat_off) <= half) & \
                     (h <= P["ga.corridor_ceiling_ft"]) & (aligned | on_ground)
            vr = _vr(t, h, P["data.smoothing_points"])
            for seg in _segments(inside, t, P["ga.min_points"]):
                a = _classify(seg, t, h, dist, lat_off, gs, vr, on_ground, P)
                if a is None:
                    continue
                a.update(runway=rw.name, rwy=rw)
                a["gates"] = _gates(seg, t, h, dist, lat_off, gs, vr, a["i_min"], P)
                found.append(a)
            # departure from this runway end (FR-GA-010)
            dep = _departure(t, h, dist, track, rw, on_ground, P)
            if dep is not None and utc_from_ts(dep).date() == day:
                k = int(np.searchsorted(t, dep))
                dep_cands.append((abs(float(lat_off[min(k, len(t) - 1)])), rw.name, dep))
        if dep_cands:
            _, rname, tdep = min(dep_cands)
            departures.append(dict(base, runway=rname, t_dep=tdep))
        found.sort(key=lambda a: a["t_min"])
        # an approach seen against two runway ends (parallel runways) is kept on the closest centreline
        dedup = []
        for a in found:
            if dedup and abs(a["t_min"] - dedup[-1]["t_min"]) < 60:
                if abs(a["lat_at_min"]) < abs(dedup[-1]["lat_at_min"]):
                    dedup[-1] = a
                continue
            dedup.append(a)
        for n, a in enumerate(dedup, 1):
            a.update(base)
            a["approach_no"] = n
        _ga_outcomes(dedup, x, y, t, rwys, meta, icao)
        _arrival_efficiency(dedup, x, y, t, track, rwys)
        approaches += [a for a in dedup if utc_from_ts(a["t_min"]).date() == day]
        if ctx is not None and flights_seen % 200 == 0:
            ctx.progress(ctx.options.get("_frac", 0.0), f"{icao} {day}: {flights_seen} flights read")

    _sequence(approaches, departures)
    _unstable(approaches, P)
    cov = _coverage(approaches, flights_seen, gap_flights, P)
    stats = _store(db, icao, day, approaches, departures, cov, wx, P, param_version)
    stats.update(read_points=n_read, flights=flights_seen)
    return stats


def _segments(inside, t, min_points):
    idx = np.flatnonzero(inside)
    if len(idx) == 0:
        return []
    segs, start = [], idx[0]
    for a, b in zip(idx, idx[1:]):
        if b != a + 1 and not (b == a + 2 and t[b] - t[a] <= 60):
            segs.append(np.arange(start, a + 1))
            start = b
    segs.append(np.arange(start, idx[-1] + 1))
    return [s for s in segs if len(s) >= min_points]


def _classify(seg, t, h, dist, lat_off, gs, vr, on_ground, P) -> dict | None:
    hs = h[seg]
    i_min = int(seg[int(np.argmin(hs))])
    h_min = float(h[i_min])
    # descent before the lowest point: excludes departures (FR-GA-002)
    before = (t >= t[i_min] - 120) & (np.arange(len(t)) <= i_min)
    if h[before].max() - h_min < P["ga.descent_required_ft"] and not (on_ground[i_min] and h[seg[0]] > 150):
        return None
    landed = (h_min <= P["ga.landing_height_ft"] and gs[i_min] <= P["ga.landing_speed_kt"]) or on_ground[i_min] and gs[i_min] < P["ga.landing_speed_kt"]
    out = {"i_min": i_min, "t_min": float(t[i_min]), "h_min": h_min, "d_min": float(dist[i_min]),
           "lat_at_min": float(lat_off[i_min]), "t_start": float(t[seg[0]]), "t_end": float(t[seg[-1]]),
           "ga": None}
    # climb after the lowest point, within 4 minutes (FR-GA-004)
    after = np.flatnonzero((t > t[i_min]) & (t <= t[i_min] + 240))
    climb_j = None
    for j in after:
        if h[j] - h_min >= P["ga.climb_trigger_ft"]:
            climb_j = int(j)
            break
        if on_ground[j] and gs[j] < P["ga.landing_speed_kt"]:
            break                                               # rolled out: landed
    if climb_j is not None and not (landed and on_ground[min(i_min + 3, len(t) - 1)] and gs[min(i_min + 3, len(t) - 1)] < 60):
        # the climb must be held for the confirmation time from the +300 ft point (Data4Safety trigger pattern)
        win = (t >= t[climb_j]) & (t <= t[climb_j] + P["ga.confirmation_s"])
        if win.sum() < 2:
            win = (t > t[i_min]) & (t <= t[climb_j])
        rates = vr[win]
        confirmed = len(rates) > 0 and rates.mean() >= P["ga.climb_rate_fpm"] and rates.min() >= 0.5 * P["ga.climb_rate_fpm"]
        if confirmed:
            if h_min <= P["ga.landing_height_ft"] and dist[i_min] <= 0.5:
                out["outcome"] = "touch_and_go" if gs[i_min] <= P["ga.landing_speed_kt"] + 20 or on_ground[i_min] else "low_approach"
            else:
                out["outcome"] = "go_around"
                out["ga"] = {"t": float(t[i_min]), "dist": float(dist[i_min]), "height": h_min}
            out["t_thr"] = float(t[i_min])
            return out
    if landed:
        out["outcome"] = "landed"
    else:
        last = len(t) - 1
        ends_low = t[last] - t[i_min] <= 120 and dist[i_min] <= 1.0 and h_min < 200
        out["outcome"] = "landed" if ends_low else "other"
    # time over the threshold: where dist crosses 0, else the lowest point
    cross = np.flatnonzero((dist[seg][:-1] > 0) & (dist[seg][1:] <= 0))
    out["t_thr"] = float(t[seg[cross[0] + 1]]) if len(cross) else float(t[i_min])
    return out


def _gates(seg, t, h, dist, lat_off, gs, vr, i_min, P) -> dict:
    """Values at the 6/4/2 NM gates and at 1,000/500 ft on the way down (FR-GA-009)."""
    tan_g = math.tan(math.radians(P["ga.glide_deg"]))
    idx = np.arange(max(0, int(seg[0]) - 40), i_min + 1)       # include points just before the corridor
    out = {}
    for g in GATES_NM:
        d = dist[idx]
        k = np.flatnonzero((d[:-1] >= g) & (d[1:] < g))
        if len(k):
            j = idx[k[-1]]
            f = (dist[j] - g) / (dist[j] - dist[j + 1]) if dist[j] != dist[j + 1] else 0
            hh = h[j] + f * (h[j + 1] - h[j])
            out[str(int(g))] = {"lat_m": round(float(lat_off[j] + f * (lat_off[j + 1] - lat_off[j])) * 1852, 0),
                                "dev_ft": round(hh - (g * geo.FT_PER_NM * tan_g + P["ga.tch_ft"]), 0),
                                "gs": round(float(gs[j]), 0), "vr": round(float(vr[j]), 0), "h": round(hh, 0)}
    for g in GATES_FT:
        hh = h[idx]
        k = np.flatnonzero((hh[:-1] >= g) & (hh[1:] < g))
        if len(k):
            j = idx[k[-1]]
            dd = float(dist[j])
            out[f"h{int(g)}"] = {"lat_m": round(float(lat_off[j]) * 1852, 0), "dist_nm": round(dd, 2),
                                 "dev_ft": round(g - (dd * geo.FT_PER_NM * tan_g + P["ga.tch_ft"]), 0),
                                 "gs": round(float(gs[j]), 0), "vr": round(float(vr[j]), 0)}
    return out


def _departure(t, h, dist, track, rw: Rwy, on_ground, P):
    """First airborne point of a take-off on this runway end: low, climbing, aligned, beyond the threshold."""
    n = min(len(t), 60)
    if n < 5 or h[0] > 400:
        return None
    for i in range(n - 3):
        if h[i] >= 50 and not on_ground[i] and h[i + 3] > h[i] + 100 and dist[i] < 0.2 and \
                geo.angle_diff(track[i], rw.brg) <= 20:
            return float(t[i])
        if h[i] > 1500:
            break
    return None


def _ga_outcomes(apps, x, y, t, rwys, meta, icao):
    n_ga = sum(1 for a in apps if a["outcome"] == "go_around")
    for i, a in enumerate(apps):
        a["n_approaches"] = len(apps)
        a["repeated"] = n_ga >= 2 and a["outcome"] == "go_around"
        if a["outcome"] != "go_around":
            continue
        nxt = next((b for b in apps[i + 1:] if b["outcome"] in ("landed", "go_around", "touch_and_go")), None)
        landing = next((b for b in apps[i + 1:] if b["outcome"] == "landed"), None)
        ga_i = int(np.searchsorted(t, a["ga"]["t"]))
        if landing is not None:
            a["ga"]["outcome"] = "landed_same" if landing["runway"] == a["runway"] else "landed_other"
            a["ga"]["delay_min"] = round((landing["t_thr"] - a["ga"]["t"]) / 60, 1)
            j = int(np.searchsorted(t, landing["t_thr"]))
            flown = geo.path_length(x[ga_i:j + 1], y[ga_i:j + 1])
            a["ga"]["extra_nm"] = round(max(0.0, flown - max(0.0, a["ga"]["dist"])), 1)
        else:
            far = math.hypot(x[-1], y[-1])
            if (meta.get("ades") and meta["ades"] != icao) or far > 30:
                a["ga"]["outcome"] = "diverted"
            else:
                a["ga"]["outcome"] = "unknown"
        del nxt


def _arrival_efficiency(apps, x, y, t, track, rwys):
    """Extra distance flown inside 40 NM and holding circles, for the first approach (EWS features)."""
    if not apps:
        return
    a = apps[0]
    j = int(np.searchsorted(t, a["t_thr"]))
    if j <= 2:
        return
    rw = a["rwy"]
    flown = geo.path_length(x[:j + 1], y[:j + 1])
    straight = math.hypot(x[0] - rw.x, y[0] - rw.y)
    a["extra_40nm"] = round(max(0.0, flown - straight), 1)
    turn = np.abs(np.diff(geo.unwrap_deg(track[:j + 1]))).sum()
    a["holds"] = int(max(0, (turn - 270) // 360))


def _sequence(apps, deps):
    by_rw: dict[str, list] = {}
    for a in apps:
        if a["outcome"] in ("landed", "go_around"):
            by_rw.setdefault(a["runway"], []).append(a)
    for rw, lst in by_rw.items():
        lst.sort(key=lambda a: a["t_thr"])
        dts = sorted(d["t_dep"] for d in deps if d["runway"] == rw)
        for k, a in enumerate(lst):
            if k:
                a["ttl_s"] = round(a["t_thr"] - lst[k - 1]["t_thr"], 0)
                g = a["gates"].get("2") or a["gates"].get("4") or {}
                a["dtl_nm"] = round(a["ttl_s"] * (g.get("gs") or 140) / 3600, 1)
            prev = [d for d in dts if d <= a["t_thr"]]
            if prev:
                a["since_dep_s"] = round(a["t_thr"] - prev[-1], 0)


def _unstable(apps, P):
    by_rw: dict[str, list] = {}
    for a in apps:
        g = a["gates"].get("h1000")
        if g:
            by_rw.setdefault(a["runway"], []).append(g["gs"])
    med = {rw: float(np.median(v)) for rw, v in by_rw.items() if v}
    for a in apps:
        g = a["gates"].get("h1000")
        if not g:
            a["unstable"] = None
            continue
        a["unstable"] = abs(g["dev_ft"]) > P["ga.unstable_dev_ft"] or g["gs"] > med.get(a["runway"], 1e9) + P["ga.unstable_speed_kt"]


def _coverage(apps, flights_seen, gap_flights, P) -> dict:
    out = {}
    for a in apps:
        if a["outcome"] in ("touch_and_go", "low_approach"):
            continue
        c = out.setdefault(a["runway"], {"approaches": 0, "reached": 0})
        c["approaches"] += 1
        c["reached"] += int(a["h_min"] < 1000)
    for rw, c in out.items():
        c["share"] = c["reached"] / c["approaches"] if c["approaches"] else 0.0
        c["incomplete"] = c["approaches"] >= 3 and c["share"] < P["ga.coverage_min_share"]
    out["_flights"] = flights_seen
    out["_gap_share"] = gap_flights / flights_seen if flights_seen else 0.0
    return out


def _review_flag(db: Session, icao: str, a: dict, P: dict) -> str:
    if a["outcome"] != "go_around":
        return ""
    first = db.query(GaApproach.day).filter_by(icao=icao, outcome="go_around").order_by(GaApproach.day).first()
    start = first[0] if first else utc_from_ts(a["t_min"]).date()
    in_first = (utc_from_ts(a["t_min"]).date() - start).days <= 30.5 * P["ga.review_all_months"]
    rnd = random.Random(f"{a['gufi']}|{a['runway']}").random() < P["ga.review_sample"]
    special = a["ga"] and a["ga"]["height"] < P["ga.band_low_ft"] or a.get("repeated")
    return "pending" if (in_first or rnd or special) else ""


def _store(db, icao, day, apps, deps, cov, wx, P, param_version) -> dict:
    # keep analyst decisions and manual additions across re-processing (FR-GA-015/017)
    old = db.query(GaApproach).filter_by(icao=icao, day=day).all()
    kept = {(o.gufi, o.runway, o.approach_no): (o.validation, o.validated_by, o.validation_note) for o in old if o.validation}
    for o in old:
        if not o.added_manually:
            db.delete(o)
    db.query(GaDeparture).filter_by(icao=icao, day=day).delete()
    db.query(GaCoverage).filter_by(icao=icao, day=day).delete()
    db.flush()
    rv = current_version(db, "runways")
    n_ga = 0
    for a in apps:
        ts = lambda v: utc_from_ts(v) if v is not None else None
        w = wx.at(ts(a["t_thr"]))
        head, cross = wind_components(w, a["rwy"].brg)
        ga = a.get("ga") or {}
        band = ""
        if ga:
            band = "high" if ga["height"] > P["ga.band_high_ft"] else ("low" if ga["height"] < P["ga.band_low_ft"] else "mid")
            n_ga += 1
        rec = GaApproach(
            icao=icao, runway=a["runway"], day=day, gufi=a["gufi"], callsign=a["callsign"], registration=a["registration"],
            icao24=a["icao24"], operator=a["operator"], aircraft_type=a["aircraft_type"], adep=a["adep"], ades=a["ades"],
            t_start=ts(a["t_start"]), t_end=ts(a["t_end"]), t_thr=ts(a["t_thr"]), approach_no=a["approach_no"],
            outcome=a["outcome"], min_height_ft=round(a["h_min"], 0), min_dist_nm=round(a["d_min"], 2),
            ga_time=ts(ga.get("t")), ga_dist_nm=round(ga["dist"], 2) if ga else None,
            ga_height_ft=round(ga["height"], 0) if ga else None, ga_band=band, ga_outcome=ga.get("outcome", ""),
            ga_delay_min=ga.get("delay_min"), ga_extra_nm=ga.get("extra_nm"), repeated=bool(a.get("repeated")),
            n_approaches=a["n_approaches"], gates=a["gates"], unstable=a.get("unstable"), ttl_s=a.get("ttl_s"),
            dtl_nm=a.get("dtl_nm"), since_dep_s=a.get("since_dep_s"), extra_40nm=a.get("extra_40nm"),
            holds=a.get("holds", 0), max_gap_s=a["max_gap_s"], wx_id=w.id if w else None, head_kt=head,
            cross_kt=cross, gust_kt=w.gust_kt if w else None, vis_m=w.vis_m if w else None,
            ceiling_ft=w.ceiling_ft if w else None, ts=bool(w and (w.ts or w.vcts)), cb=bool(w and (w.cb or w.tcu)),
            precip=w.precip if w else 0, qnh_hpa=w.qnh_hpa if w else None,
            incomplete=bool(cov.get(a["runway"], {}).get("incomplete")), param_version=param_version, ref_version=rv,
            computed_at=now())
        prev = kept.get((rec.gufi, rec.runway, rec.approach_no))
        if prev:
            rec.validation, rec.validated_by, rec.validation_note = prev
        else:
            rec.validation = _review_flag(db, icao, a, P)
        db.add(rec)
    for d in deps:
        db.add(GaDeparture(icao=icao, runway=d["runway"], day=day, gufi=d["gufi"], callsign=d["callsign"],
                           operator=d["operator"], t_dep=utc_from_ts(d["t_dep"])))
    for rw, c in cov.items():
        if rw.startswith("_"):
            continue
        db.add(GaCoverage(icao=icao, runway=rw, day=day, approaches=c["approaches"], reached_1000=c["reached"],
                          share=round(c["share"], 3), flights_seen=cov["_flights"], gap_share=round(cov["_gap_share"], 3),
                          incomplete=c["incomplete"]))
    if not [k for k in cov if not k.startswith("_")]:
        db.add(GaCoverage(icao=icao, runway="*", day=day, approaches=0, reached_1000=0, share=0,
                          flights_seen=cov["_flights"], gap_share=round(cov["_gap_share"], 3), incomplete=False))
    db.commit()
    return {"approaches": len(apps), "go_arounds": n_ga, "departures": len(deps),
            "incomplete_runways": [rw for rw, c in cov.items() if not rw.startswith("_") and c["incomplete"]]}
