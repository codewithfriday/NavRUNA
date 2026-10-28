"""Trajectory deviation monitor (SRS 7, FR-TDM-001..023).

Nominal paths: every trajectory of the training window is resampled to N points by along-track distance
in a local projection centred on the route, clustered with HDBSCAN on the mean point-to-point distance,
and each cluster becomes a medoid path with a 5–95 % lateral corridor and a vertical profile.
Monitoring: each day's flights are assigned to the nearest nominal path and measured at critical points.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
from scipy.stats import ks_2samp
from sqlalchemy.orm import Session

from ...core import geo
from ...core.alerts import raise_alert
from ...core.refdata import aerodrome_ref, known_airlines, operator_of
from ...models import (RefPoint, RefSector, TdmCluster, TdmClusterSet, TdmCriticalPoint, TdmFlight, TdmPoint,
                       TdmRoute, now)


class Frame:
    """Local projection for a route, centred between the two aerodromes."""

    def __init__(self, a: dict, b: dict):
        self.lon0, self.lat0 = (a["lon"] + b["lon"]) / 2, (a["lat"] + b["lat"]) / 2

    def xy(self, lon, lat):
        return geo.to_xy(lon, lat, self.lon0, self.lat0)

    def ll(self, x, y):
        return geo.to_lonlat(x, y, self.lon0, self.lat0)


def resample(x, y, alt, n: int):
    """n points equally spaced by along-track distance."""
    seg = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] <= 0:
        return None
    grid = np.linspace(0, s[-1], n)
    a = np.where(np.isnan(alt), 0, alt)
    return np.column_stack([np.interp(grid, s, x), np.interp(grid, s, y), np.interp(grid, s, a)])


def mean_dist(A: np.ndarray, B: np.ndarray) -> float:
    return float(np.hypot(A[:, 0] - B[:, 0], A[:, 1] - B[:, 1]).mean())


def cross_track(px, py, path: np.ndarray):
    """Signed cross-track offset (NM, + = right of path) and along-track station of points against a polyline."""
    ax, ay = path[:-1, 0], path[:-1, 1]
    dx, dy = np.diff(path[:, 0]), np.diff(path[:, 1])
    L2 = dx * dx + dy * dy
    L2[L2 == 0] = 1e-12
    seglen = np.sqrt(L2)
    cum = np.concatenate([[0], np.cumsum(seglen)])
    px, py = np.atleast_1d(px)[:, None], np.atleast_1d(py)[:, None]
    tt = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0, 1)
    qx, qy = ax + tt * dx, ay + tt * dy
    d = np.hypot(px - qx, py - qy)
    k = np.argmin(d, axis=1)
    rows = np.arange(len(k))
    sign = np.sign(dx[k] * (py[rows, 0] - ay[k]) - dy[k] * (px[rows, 0] - ax[k]))
    off = -sign * d[rows, k]          # positive to the right of the direction of travel
    station = cum[k] + tt[rows, k] * seglen[k]
    return off, station


def route_refs(db: Session, route: TdmRoute):
    a, b = aerodrome_ref(db, route.adep), aerodrome_ref(db, route.ades)
    if a is None or b is None:
        raise ValueError(f"No runway data for {route.adep} or {route.ades}")
    return a, b


def build_clusters(db: Session, src, route: TdmRoute, d1: date, P: dict) -> dict:
    from sklearn.cluster import HDBSCAN
    a, b = route_refs(db, route)
    fr = Frame(a, b)
    d0 = d1 - timedelta(days=int(P["tdm.window_days"]) - 1)
    n = int(P["tdm.resample_points"])
    trajs, alts = [], []
    for meta, pts in src.flights_between(route.adep, route.ades, d0, d1, step_s=30):
        x, y = fr.xy(pts.lon.values, pts.lat.values)
        r = resample(x, y, pts.alt.values, n)
        if r is not None and len(pts) >= 10:
            trajs.append(r)
    if len(trajs) < max(10, int(P["tdm.min_cluster_size"])):
        return {"skipped": f"{route.adep}-{route.ades}: {len(trajs)} flights in {P['tdm.window_days']:.0f} days, too few to learn nominal paths"}
    rng = np.random.default_rng(0)
    idx = np.arange(len(trajs))
    if len(idx) > P["tdm.max_train_flights"]:
        idx = rng.choice(idx, int(P["tdm.max_train_flights"]), replace=False)
    T = np.stack([trajs[i] for i in idx])
    m = len(T)
    D = np.zeros((m, m))
    for i in range(m):
        D[i] = np.hypot(T[:, :, 0] - T[i, :, 0], T[:, :, 1] - T[i, :, 1]).mean(axis=1)
    mcs = max(int(P["tdm.min_cluster_size"]), int(math.ceil(P["tdm.min_cluster_share"] * m)))
    labels = HDBSCAN(min_cluster_size=mcs, metric="precomputed", allow_single_cluster=True, copy=True).fit_predict(D)
    if (labels >= 0).sum() == 0:
        labels = np.zeros(m, int)
    last = db.query(TdmClusterSet).filter_by(route_id=route.id).order_by(TdmClusterSet.version.desc()).first()
    prev_shares = {}
    if last:
        prev_shares = {c.label: (c.share, c.medoid) for c in db.query(TdmCluster).filter_by(set_id=last.id)}
        for s in db.query(TdmClusterSet).filter_by(route_id=route.id, active=True):
            s.active = False
    cs = TdmClusterSet(route_id=route.id, version=(last.version if last else 0) + 1, window_from=d0, window_to=d1,
                       n_flights=m, n_unclustered=int((labels < 0).sum()), params={k: v for k, v in P.items() if k.startswith("tdm.")})
    db.add(cs)
    db.flush()
    out = []
    for lab in sorted(set(labels) - {-1}):
        mem = np.flatnonzero(labels == lab)
        sub = D[np.ix_(mem, mem)]
        med = T[mem[int(np.argmin(sub.sum(axis=1)))]]
        offs = []
        for j in mem:
            o, _ = cross_track(T[j, :, 0], T[j, :, 1], med)
            offs.append(o)
        offs = np.array(offs)
        lon, lat = fr.ll(med[:, 0], med[:, 1])
        c = TdmCluster(set_id=cs.id, label=int(lab), name=f"Path {chr(65 + int(lab)) if lab < 26 else lab}", n=len(mem),
                       share=round(len(mem) / m, 3),
                       medoid=[[round(float(lo), 4), round(float(la), 4), round(float(al), 0)] for lo, la, al in zip(lon, lat, med[:, 2])],
                       corridor={"p5": np.percentile(offs, 5, axis=0).round(2).tolist(), "p95": np.percentile(offs, 95, axis=0).round(2).tolist()},
                       profile={k: np.percentile(T[mem, :, 2], q, axis=0).round(0).tolist() for k, q in (("p5", 5), ("p50", 50), ("p95", 95))})
        db.add(c)
        out.append(c)
    db.commit()
    _critical_points(db, route, out, fr, P)
    _check_changes(db, route, cs, out, prev_shares, fr, P)
    return {"route": f"{route.adep}-{route.ades}", "set": cs.version, "flights": m, "clusters": len(out),
            "unclustered": cs.n_unclustered}


def _critical_points(db: Session, route: TdmRoute, clusters: list[TdmCluster], fr: Frame, P: dict):
    """Default critical points: fixes and navaids near a nominal path, TMA boundary crossings (FR-TDM-012).
    Points the analyst added or switched off are kept."""
    manual = db.query(TdmCriticalPoint).filter(TdmCriticalPoint.route_id == route.id,
                                               (TdmCriticalPoint.kind == "manual") | (TdmCriticalPoint.active.is_(False))).all()
    keep_names = {c.name for c in manual}
    db.query(TdmCriticalPoint).filter(TdmCriticalPoint.route_id == route.id, TdmCriticalPoint.kind != "manual",
                                      TdmCriticalPoint.active.is_(True)).delete()
    tmas = [s for s in db.query(RefSector).filter_by(kind="TMA", active=True)]
    from ...core.geo import PolygonIndex
    pidx = PolygonIndex([s.geometry for s in tmas])
    pts = db.query(RefPoint).all()
    if pts:
        px, py = fr.xy([p.lon for p in pts], [p.lat for p in pts])
    added = set()
    for c in clusters:
        med = np.array(c.medoid)
        mx, my = fr.xy(med[:, 0], med[:, 1])
        path = np.column_stack([mx, my])
        if pts:
            off, st = cross_track(px, py, path)
            total = float(np.hypot(np.diff(mx), np.diff(my)).sum())
            for p, o, s in zip(pts, off, st):
                if abs(o) <= P["tdm.cp_radius_nm"] and 5 < s < total - 5 and p.name not in added and p.name not in keep_names:
                    in_tma = any(pidx.contains(i, [p.lon], [p.lat])[0] for i in range(len(tmas)))
                    db.add(TdmCriticalPoint(route_id=route.id, name=p.name, kind=p.kind, lat=p.lat, lon=p.lon, in_tma=in_tma))
                    added.add(p.name)
        # TMA boundary crossings along the path
        for i, s in enumerate(tmas):
            inside = pidx.contains(i, med[:, 0], med[:, 1])
            ch = np.flatnonzero(np.diff(inside.astype(int)) != 0)
            for k in ch:
                nm = f"{'IN' if inside[k + 1] else 'OUT'} {s.name[:20]}"
                if nm not in added and nm not in keep_names:
                    db.add(TdmCriticalPoint(route_id=route.id, name=nm, kind="tma", lat=float(med[k + 1, 1]),
                                            lon=float(med[k + 1, 0]), in_tma=True))
                    added.add(nm)
    db.commit()


def _check_changes(db, route, cs, clusters, prev, fr, P):
    """A new path, or a path's share changing by more than the limit, raises an alert (FR-TDM-022)."""
    if not prev:
        return
    old = [(sh, np.array(md)) for sh, md in prev.values()]
    for c in clusters:
        med = np.array(c.medoid)
        mx, my = fr.xy(med[:, 0], med[:, 1])
        best, bshare = None, None
        for sh, om in old:
            ox, oy = fr.xy(om[:, 0], om[:, 1])
            if len(ox) == len(mx):
                d = float(np.hypot(mx - ox, my - oy).mean())
                if best is None or d < best:
                    best, bshare = d, sh
        r = f"{route.adep}-{route.ades}"
        if best is None or best > P["tdm.assign_nm"]:
            raise_alert(db, "TDM", f"route{route.id}", 1, f"New nominal path on {r}",
                        f"{c.name} ({c.share:.0%} of flights) has no counterpart in the previous rebuild",
                        f"A new nominal path appeared on route {r}", breakdown_key=f"set{cs.version}:{c.label}",
                        link=f"/tdm/routes/{route.id}")
        elif abs(c.share - bshare) * 100 > P["tdm.share_change_pp"]:
            raise_alert(db, "TDM", f"route{route.id}", 1, f"Path share changed on {r}",
                        f"{c.name}: {bshare:.0%} → {c.share:.0%}", f"Use of a nominal path on route {r} changed by "
                        f"{abs(c.share - bshare) * 100:.0f} percentage points", breakdown_key=f"set{cs.version}:{c.label}",
                        link=f"/tdm/routes/{route.id}")


def monitor_day(db: Session, src, route: TdmRoute, day: date, P: dict) -> dict:
    cs = db.query(TdmClusterSet).filter_by(route_id=route.id, active=True).order_by(TdmClusterSet.version.desc()).first()
    if cs is None:
        return {"skipped": f"{route.adep}-{route.ades}: no nominal paths yet"}
    clusters = db.query(TdmCluster).filter_by(set_id=cs.id, retired=False).all()
    if not clusters:
        return {"skipped": f"{route.adep}-{route.ades}: no active nominal paths"}
    a, b = route_refs(db, route)
    fr = Frame(a, b)
    n = int(P["tdm.resample_points"])
    paths = {}
    for c in clusters:
        med = np.array(c.medoid)
        x, y = fr.xy(med[:, 0], med[:, 1])
        paths[c.id] = (np.column_stack([x, y, med[:, 2]]), c)
    cps = db.query(TdmCriticalPoint).filter_by(route_id=route.id, active=True).all()
    airlines = known_airlines(db)
    db.query(TdmFlight).filter_by(route_id=route.id, day=day).delete()
    db.flush()
    n_f = n_lat = n_vert = n_off = 0
    for meta, pts in src.flights_between(route.adep, route.ades, day, day, step_s=30):
        x, y = fr.xy(pts.lon.values, pts.lat.values)
        r = resample(x, y, pts.alt.values, n)
        if r is None or len(pts) < 10:
            continue
        n_f += 1
        dists = {cid: mean_dist(r, p[0]) for cid, p in paths.items()}
        cid = min(dists, key=dists.get)
        off_pattern = dists[cid] > P["tdm.assign_nm"]
        path, cl = paths[cid]
        cs_ = (meta.get("callsign") or "").strip().upper()
        off, station = cross_track(x, y, path[:, :2])
        f = TdmFlight(route_id=route.id, gufi=meta["gufi"], callsign=cs_, operator=operator_of(cs_, airlines), day=day,
                      t_dep=None, set_id=cs.id, cluster_id=None if off_pattern else cid, off_pattern=bool(off_pattern),
                      mean_dist_nm=round(dists[cid], 2), max_dev_nm=round(float(np.max(np.abs(off))), 2),
                      track=[[round(float(lo), 3), round(float(la), 3), round(float(al), 0) if al == al else None]
                             for lo, la, al in zip(pts.lon.values[::4], pts.lat.values[::4], pts.alt.values[::4])])
        db.add(f)
        db.flush()
        n_off += int(off_pattern)
        if off_pattern:
            continue
        cum = np.concatenate([[0], np.cumsum(np.hypot(np.diff(path[:, 0]), np.diff(path[:, 1])))])
        for cp in cps:
            cx, cy = fr.xy(cp.lon, cp.lat)
            _, cst = cross_track(np.array([float(cx)]), np.array([float(cy)]), path[:, :2])
            k = int(np.argmin(np.abs(station - cst[0])))
            if abs(station[k] - cst[0]) > 10:
                continue
            lat_nm = float(off[k])
            prof = np.interp(cst[0], cum, np.array(cl.profile["p50"], float))
            alt = pts.alt.values[k]
            alt_diff = float(alt - prof) if alt == alt else None
            limit = P["tdm.lateral_tma_nm"] if cp.in_tma else P["tdm.lateral_enroute_nm"]
            fl = abs(lat_nm) > limit
            fv = alt_diff is not None and abs(alt_diff) > P["tdm.vertical_ft"]
            f.flag_lateral |= fl
            f.flag_vertical |= fv
            db.add(TdmPoint(flight_id=f.id, cp_id=cp.id, lateral_nm=round(lat_nm, 2),
                            alt_diff_ft=round(alt_diff, 0) if alt_diff is not None else None, flag_lateral=fl, flag_vertical=fv))
        n_lat += int(f.flag_lateral)
        n_vert += int(f.flag_vertical)
    db.commit()
    return {"route": f"{route.adep}-{route.ades}", "flights": n_f, "lateral": n_lat, "vertical": n_vert, "off_pattern": n_off}


def hotspots(db: Session, route_id: int | None, d0: date, d1: date, P: dict) -> list[dict]:
    """Deviation density on a grid (FR-TDM-014)."""
    q = (db.query(TdmPoint, TdmCriticalPoint, TdmFlight).join(TdmFlight, TdmFlight.id == TdmPoint.flight_id)
         .join(TdmCriticalPoint, TdmCriticalPoint.id == TdmPoint.cp_id)
         .filter(TdmFlight.day >= d0, TdmFlight.day <= d1))
    if route_id:
        q = q.filter(TdmFlight.route_id == route_id)
    cells: dict[tuple, dict] = {}
    g = P["tdm.hotspot_grid_nm"] / 60
    for p, cp, f in q.yield_per(20000):
        key = (round(cp.lat / g), round(cp.lon / g))
        c = cells.setdefault(key, {"lat": key[0] * g, "lon": key[1] * g, "n": 0, "lateral": 0, "vertical": 0,
                                   "left": 0, "right": 0, "above": 0, "below": 0, "points": set()})
        c["n"] += 1
        c["points"].add(cp.name)
        if p.flag_lateral:
            c["lateral"] += 1
            c["right" if p.lateral_nm > 0 else "left"] += 1
        if p.flag_vertical:
            c["vertical"] += 1
            c["above" if (p.alt_diff_ft or 0) > 0 else "below"] += 1
    out = []
    for c in cells.values():
        if c["lateral"] + c["vertical"] == 0:
            continue
        c["share"] = round((c["lateral"] + c["vertical"]) / c["n"], 3)
        c["points"] = sorted(c["points"])
        c["direction"] = ("right" if c["right"] > c["left"] else "left") if c["lateral"] else ("above" if c["above"] > c["below"] else "below")
        out.append(c)
    return sorted(out, key=lambda c: -c["share"])


def compare(db: Session, route_id: int, change: date, days: int = 30) -> dict:
    """Before/after a change date: path use and deviation distributions per critical point (FR-TDM-021)."""
    b0, b1, a0, a1 = change - timedelta(days=days), change - timedelta(days=1), change, change + timedelta(days=days - 1)

    def flights(d0, d1):
        return db.query(TdmFlight).filter(TdmFlight.route_id == route_id, TdmFlight.day >= d0, TdmFlight.day <= d1).all()
    fb, fa = flights(b0, b1), flights(a0, a1)

    def shares(fl):
        n = len(fl) or 1
        out: dict = {}
        for f in fl:
            k = "off-pattern" if f.off_pattern else str(f.cluster_id)
            out[k] = out.get(k, 0) + 1 / n
        return {k: round(v, 3) for k, v in out.items()}
    names = {str(c.id): c.name for c in db.query(TdmCluster)}
    res = {"before": {"from": str(b0), "to": str(b1), "flights": len(fb)}, "after": {"from": str(a0), "to": str(a1), "flights": len(fa)},
           "paths": [], "points": []}
    sb, sa = shares(fb), shares(fa)
    for k in sorted(set(sb) | set(sa)):
        res["paths"].append({"path": names.get(k, k), "before": sb.get(k, 0), "after": sa.get(k, 0),
                             "change_pp": round((sa.get(k, 0) - sb.get(k, 0)) * 100, 1)})
    ids_b, ids_a = {f.id for f in fb}, {f.id for f in fa}
    for cp in db.query(TdmCriticalPoint).filter_by(route_id=route_id):
        pts = db.query(TdmPoint).filter(TdmPoint.cp_id == cp.id).all()
        xb = [p.lateral_nm for p in pts if p.flight_id in ids_b]
        xa = [p.lateral_nm for p in pts if p.flight_id in ids_a]
        if len(xb) >= 10 and len(xa) >= 10:
            st = ks_2samp(xb, xa)
            res["points"].append({"point": cp.name, "n_before": len(xb), "n_after": len(xa),
                                  "median_before": round(float(np.median(xb)), 2), "median_after": round(float(np.median(xa)), 2),
                                  "ks": round(float(st.statistic), 3), "p": round(float(st.pvalue), 4),
                                  "significant": bool(st.pvalue < 0.05)})
    return res


def hazard_candidates(db: Session, d0: date, d1: date, P: dict) -> list[dict]:
    """Critical points where the flagged share exceeds the limit, for the SRM unit (FR-TDM-023)."""
    out = []
    routes = {r.id: r for r in db.query(TdmRoute)}
    for cp in db.query(TdmCriticalPoint).filter_by(active=True):
        q = (db.query(TdmPoint, TdmFlight).join(TdmFlight, TdmFlight.id == TdmPoint.flight_id)
             .filter(TdmPoint.cp_id == cp.id, TdmFlight.day >= d0, TdmFlight.day <= d1))
        rows = q.all()
        if len(rows) < 10:
            continue
        lat = [p for p, _ in rows if p.flag_lateral]
        vert = [p for p, _ in rows if p.flag_vertical]
        share = (len(lat) + len(vert)) / len(rows)
        if share < P["tdm.hazard_min_share"]:
            continue
        r = routes.get(cp.route_id)
        examples = [f.gufi for p, f in rows if p.flag_lateral or p.flag_vertical][:5]
        pattern = []
        if lat:
            right = sum(1 for p in lat if p.lateral_nm > 0)
            pattern.append(f"lateral, mostly {'right' if right >= len(lat) / 2 else 'left'} of path")
        if vert:
            above = sum(1 for p in vert if (p.alt_diff_ft or 0) > 0)
            pattern.append(f"vertical, mostly {'above' if above >= len(vert) / 2 else 'below'} profile")
        out.append({"route": f"{r.adep}-{r.ades}" if r else "?", "route_id": cp.route_id, "point": cp.name, "lat": cp.lat,
                    "lon": cp.lon, "in_tma": cp.in_tma, "flights": len(rows), "affected": len({f.id for p, f in rows if p.flag_lateral or p.flag_vertical}),
                    "share": round(share, 3), "pattern": "; ".join(pattern), "period": f"{d0} to {d1}", "examples": examples})
    return sorted(out, key=lambda r: -r["share"])
