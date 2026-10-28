"""Airspace complexity and workload index (SRS 8, FR-CXI-001..020).

Per sector and UTC hour, from NavTrace tracks resampled every `cxi.sample_s` seconds:
entries, peak count, occupancy, PRU interaction times (20 NM x 20 NM x 3,000 ft cells), crossing pairs,
proximity events, level changes and heading changes; then the PRU indicators and a composite index
standardised against the sector's own baseline.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ...core import geo
from ...models import CxiSectorHour, RefSector

MEASURES = ["entries", "peak", "interaction_h", "crossings", "level_changes", "heading_changes"]
WEIGHT_KEYS = {"entries": "cxi.w_entries", "peak": "cxi.w_peak", "interaction_h": "cxi.w_interactions",
               "crossings": "cxi.w_crossings", "level_changes": "cxi.w_levels", "heading_changes": "cxi.w_headings"}


class SectorModel:
    def __init__(self, db: Session, P: dict):
        self.sectors = [s for s in db.query(RefSector).filter_by(active=True)]
        self.idx = geo.PolygonIndex([s.geometry for s in self.sectors])
        self.lo = np.array([s.override_lower_ft if s.override_lower_ft is not None else s.lower_ft for s in self.sectors], float)
        self.hi = np.array([s.override_upper_ft if s.override_upper_ft is not None else s.upper_ft for s in self.sectors], float)
        self.area = np.array([s.area_nm2 or 1e9 for s in self.sectors], float)
        lons = [c for s in self.sectors for ring in geo.polygon_rings(s.geometry) for c in ring[:, 0]]
        lats = [c for s in self.sectors for ring in geo.polygon_rings(s.geometry) for c in ring[:, 1]]
        self.bbox = (min(lons), min(lats), max(lons), max(lats)) if lons else (90, -15, 145, 10)

    def assign(self, lon, lat, alt) -> np.ndarray:
        """Sector index per point (-1 = outside controlled sectors); the smaller volume wins on overlap (FR-CXI-003)."""
        out = np.full(len(lon), -1, int)
        best_area = np.full(len(lon), np.inf)
        for i in range(len(self.sectors)):
            vin = (alt >= self.lo[i]) & (alt < self.hi[i])
            if not vin.any():
                continue
            m = np.zeros(len(lon), bool)
            m[vin] = self.idx.contains(i, lon[vin], lat[vin])
            better = m & (self.area[i] < best_area)
            out[better] = i
            best_area[better] = self.area[i]
        return out


def compute_hour(db: Session, src, sm: SectorModel, t0: datetime, P: dict) -> dict[int, dict]:
    step = int(P["cxi.sample_s"])
    t1 = t0 + timedelta(hours=1)
    frames = []
    for k, (meta, pts) in enumerate(src.flights_in_window(t0, t1, sm.bbox, step_s=step)):
        pts = pts.copy()
        pts["f"] = k
        frames.append(pts)
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    df = df[df.alt.notna()]
    if df.empty:
        return {}
    df["tb"] = (df.t // step) * step
    lon0, lat0 = float(df.lon.mean()), float(df.lat.mean())
    df["x"], df["y"] = geo.to_xy(df.lon.values, df.lat.values, lon0, lat0)
    df = df.sort_values(["f", "t"])
    g = df.groupby("f")
    dtt = g.t.diff().fillna(step).clip(lower=1).values
    df["vr"] = (g.alt.diff().fillna(0).values / dtt) * 60
    df["trk"] = (np.degrees(np.arctan2(g.x.diff().fillna(0).values, g.y.diff().fillna(0).values)) + 360) % 360
    df.loc[g.cumcount().values == 0, "trk"] = np.nan
    df["trk"] = df.groupby("f").trk.bfill()
    # flight levels above the transition altitude, QNH altitude below (pressure altitude used as proxy)
    df["sec"] = sm.assign(df.lon.values, df.lat.values, df.alt.values)
    vstate = np.where(df.vr > P["cxi.vertical_fpm"], 1, np.where(df.vr < -P["cxi.vertical_fpm"], -1, 0))
    df["vs"] = vstate
    res: dict[int, dict] = defaultdict(lambda: {m: 0.0 for m in ["entries", "peak", "occupancy", "flight_hours", "interaction_h",
                                                              "vertical_h", "horizontal_h", "speed_h", "crossings", "proximity",
                                                              "level_changes", "heading_changes"]})
    dt_h = step / 3600
    # occupancy, entries, peak
    for sec, sub in df.groupby("sec"):
        key = int(sec)
        r = res[key]
        r["flight_hours"] += len(sub) * dt_h
        r["occupancy"] = r["flight_hours"]
        r["peak"] = float(sub.groupby("tb").f.nunique().max())
    seq = df[["f", "sec", "t", "alt", "trk"]].to_numpy()
    prev_f, prev_s = None, None
    for f, s, t, alt, trk in seq:
        if f != prev_f or s != prev_s:
            res[int(s)]["entries"] += 1
        prev_f, prev_s = f, s
    # level and heading changes per flight within each sector
    for (f, sec), sub in df.groupby(["f", "sec"]):
        a = sub.alt.values
        if len(a) > 1:
            res[int(sec)]["level_changes"] += int(_count_steps(a, P["cxi.level_change_ft"]))
            tr = sub.trk.dropna().values
            if len(tr) > 1:
                res[int(sec)]["heading_changes"] += int(_count_steps(geo.unwrap_deg(tr), P["cxi.heading_change_deg"]))
    # PRU interactions: aircraft in the same cell at the same time
    cn, cf = P["cxi.cell_nm"], P["cxi.cell_ft"]
    df["cx"], df["cy"], df["cz"] = (df.x // cn).astype(int), (df.y // cn).astype(int), (df.alt // cf).astype(int)
    df["gk"] = df.groupby(["tb", "cx", "cy", "cz"]).ngroup()
    many = df[df.gk.map(df.gk.value_counts()) >= 2]
    for gk, sub in many.groupby("gk"):
        v = sub[["sec", "vs", "trk", "gs"]].to_numpy(dtype=float)
        for i in range(len(v)):
            for j in range(i + 1, len(v)):
                r = res[int(v[i, 0])]
                r["interaction_h"] += dt_h
                if v[i, 1] != v[j, 1]:
                    r["vertical_h"] += dt_h
                if np.isfinite(v[i, 2]) and np.isfinite(v[j, 2]) and geo.angle_diff(v[i, 2], v[j, 2]) > P["cxi.horizontal_deg"]:
                    r["horizontal_h"] += dt_h
                if abs(np.nan_to_num(v[i, 3]) - np.nan_to_num(v[j, 3])) > P["cxi.speed_kt"]:
                    r["speed_h"] += dt_h
    # proximity events and crossing pairs, once per pair per hour
    from sklearn.neighbors import KDTree
    prox, cross = set(), set()
    band = P["cxi.vertical_band_ft"]
    for tb, sub in df.groupby("tb"):
        if len(sub) < 2:
            continue
        xy = sub[["x", "y"]].to_numpy()
        tree = KDTree(xy)
        nb = tree.query_radius(xy, r=P["cxi.proximity_nm"])
        fa, al, tr, sc = sub.f.values, sub.alt.values, sub.trk.values, sub.sec.values
        for i, js in enumerate(nb):
            for j in js:
                if j <= i or fa[i] == fa[j] or abs(al[i] - al[j]) > band:
                    continue
                pair = (min(fa[i], fa[j]), max(fa[i], fa[j]), int(sc[i]))
                prox.add(pair)
    for f1, f2, sec in prox:
        res[sec]["proximity"] += 1
    # crossings: ground tracks of two flights intersect inside the same sector within the time window
    coarse = df[(df.t % 60) < step]                     # crossings tested on 1-minute segments
    tracks = {f: sub for f, sub in coarse.groupby("f")}
    cand = defaultdict(set)
    for (sec, cx, cy), sub in df.groupby(["sec", "cx", "cy"]):
        fs = sorted(sub.f.unique())
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                cand[int(sec)].add((fs[i], fs[j]))
    for sec, pairs in cand.items():
        for f1, f2 in pairs:
            if f1 in tracks and f2 in tracks and _crosses(tracks[f1], tracks[f2], sec, P):
                res[sec]["crossings"] += 1
    for r in res.values():
        r["adjusted_density"] = r["interaction_h"] / r["flight_hours"] if r["flight_hours"] else 0.0
        r["structural_index"] = (r["vertical_h"] + r["horizontal_h"] + r["speed_h"]) / r["interaction_h"] if r["interaction_h"] else 0.0
        r["pru_score"] = r["adjusted_density"] * r["structural_index"]
    return {(sm.sectors[k].id if k >= 0 else 0): v for k, v in res.items()}


def _count_steps(v, step):
    """Number of times a value moves `step` away from its last anchor (level or heading changes)."""
    n, anchor = 0, v[0]
    for x in v[1:]:
        if abs(x - anchor) >= step:
            n += 1
            anchor = x
    return n


def _crosses(a: pd.DataFrame, b: pd.DataFrame, sec: int, P: dict) -> bool:
    """Do two ground tracks cross inside the sector at a large angle, close in time and level? (vectorised)"""
    a = a[a.sec == sec]
    b = b[b.sec == sec]
    if len(a) < 2 or len(b) < 2:
        return False
    A = a[["x", "y", "t", "alt", "trk"]].to_numpy(dtype=float)
    B = b[["x", "y", "t", "alt", "trk"]].to_numpy(dtype=float)
    p, r = A[:-1, None, :2], (A[1:, :2] - A[:-1, :2])[:, None, :]
    q, s = B[None, :-1, :2], (B[1:, :2] - B[:-1, :2])[None, :, :]
    den = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        tt = ((q[..., 0] - p[..., 0]) * s[..., 1] - (q[..., 1] - p[..., 1]) * s[..., 0]) / den
        uu = ((q[..., 0] - p[..., 0]) * r[..., 1] - (q[..., 1] - p[..., 1]) * r[..., 0]) / den
    hit = (np.abs(den) > 1e-9) & (tt >= 0) & (tt <= 1) & (uu >= 0) & (uu <= 1)
    if not hit.any():
        return False
    i, j = np.nonzero(hit)
    ok = (np.abs(A[i, 2] - B[j, 2]) <= P["cxi.crossing_window_s"]) & (np.abs(A[i, 3] - B[j, 3]) <= P["cxi.vertical_band_ft"]) & \
         (geo.angle_diff(np.nan_to_num(A[i, 4]), np.nan_to_num(B[j, 4])) > P["cxi.crossing_deg"])
    return bool(ok.any())


def compute_day(db: Session, src, day: date, P: dict, pv: int, progress=None) -> dict:
    sm = SectorModel(db, P)
    if not sm.sectors:
        raise ValueError("No sectors loaded: refresh NavEarth airspace first (Reference data)")
    start = datetime(day.year, day.month, day.day)
    db.query(CxiSectorHour).filter(CxiSectorHour.hour >= start, CxiSectorHour.hour < start + timedelta(days=1)).delete()
    n = 0
    for h in range(24):
        t0 = start + timedelta(hours=h)
        if progress:
            progress(h / 24, f"Complexity {day} {h:02d}:00")
        for sid, r in compute_hour(db, src, sm, t0, P).items():
            db.add(CxiSectorHour(sector_id=sid, hour=t0, param_version=pv, **{k: (int(v) if k in ("entries", "peak", "crossings", "proximity", "level_changes", "heading_changes") else round(float(v), 4)) for k, v in r.items()}))
            n += 1
        db.commit()
    standardise(db, day, P)
    return {"sector_hours": n}


def standardise(db: Session, day: date, P: dict):
    """Composite index: weighted mean of z-scores against the sector's own baseline (FR-CXI-012); top 5 % flag."""
    start = datetime(day.year, day.month, day.day)
    b0 = start - timedelta(days=int(P["cxi.baseline_days"]))
    rows = db.query(CxiSectorHour).filter(CxiSectorHour.hour >= b0, CxiSectorHour.hour < start + timedelta(days=1)).all()
    if not rows:
        return
    df = pd.DataFrame([{"id": r.id, "sid": r.sector_id, "hour": r.hour, **{m: getattr(r, m) for m in MEASURES}} for r in rows])
    w = {m: P[WEIGHT_KEYS[m]] for m in MEASURES}
    wsum = sum(w.values()) or 1
    by_id = {r.id: r for r in rows}
    for sid, sub in df.groupby("sid"):
        z = sum(w[m] * (sub[m] - sub[m].mean()) / (sub[m].std(ddof=0) or 1) for m in MEASURES) / wsum
        p95 = float(np.percentile(z, 95)) if len(z) >= 20 else np.inf
        today = sub.hour >= start
        for rid, zz in zip(sub.id[today], z[today]):
            by_id[rid].index = round(float(zz), 3)
            by_id[rid].top5 = bool(zz >= p95)
    db.commit()
