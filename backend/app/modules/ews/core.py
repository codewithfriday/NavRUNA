"""Early warning (SRS 9, FR-EWS-001..024).

Cells = aerodrome x day x 4-hour block x operator (the busiest operators; the rest grouped; small cells merged
into the block total). Coverage is checked first; days with unusual ADS-B coverage are reported and not scored.
An Isolation Forest per aerodrome, trained on a rolling window, scores each cell; the explanation lists the
features furthest from their robust baseline (median and MAD for the same aerodrome, block and season).
"""
from __future__ import annotations

import pickle
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ... import config
from ...core.alerts import raise_alert
from ...models import (Aerodrome, CxiSectorHour, EwsCell, EwsCoverage, GaApproach, GaCoverage, GaDeparture, MlModel,
                       RefSector, TdmFlight, TdmRoute, WxReport)

FEATURES = ["arrivals", "departures", "ga_rate", "ga_low", "ttl_median", "unstable_share", "extra_40nm", "holds",
            "tdm_dev_share", "cxi_tma", "ts_hours", "max_cross", "min_vis_km"]
LABELS = {"arrivals": "arrivals", "departures": "departures", "ga_rate": "go-arounds per arrival", "ga_low": "low go-arounds",
          "ttl_median": "median time to leader (s)", "unstable_share": "unstable-gate share", "extra_40nm": "extra track inside 40 NM (NM)",
          "holds": "holding circles", "tdm_dev_share": "route deviation share", "cxi_tma": "TMA complexity index",
          "ts_hours": "thunderstorm reports", "max_cross": "strongest crosswind (kt)", "min_vis_km": "lowest visibility (km)"}


def block_of(t: datetime) -> int:
    return t.hour // 4


def coverage_day(db: Session, icao: str, day: date, P: dict) -> EwsCoverage:
    """FR-EWS-003: flights seen against the 28-day median for the weekday, gap share, low-level coverage."""
    covs = db.query(GaCoverage).filter_by(icao=icao, day=day).all()
    flights = max((c.flights_seen for c in covs), default=0)
    gap = max((c.gap_share for c in covs), default=0.0)
    appr = sum(c.approaches for c in covs if c.runway != "*")
    low = sum(c.reached_1000 for c in covs if c.runway != "*") / appr if appr else None
    hist = db.query(GaCoverage).filter(GaCoverage.icao == icao, GaCoverage.day < day,
                                       GaCoverage.day >= day - timedelta(days=28)).all()
    per_day: dict[date, dict] = {}
    for c in hist:
        e = per_day.setdefault(c.day, {"flights": 0, "gap": 0.0, "appr": 0, "low": 0})
        e["flights"] = max(e["flights"], c.flights_seen)
        e["gap"] = max(e["gap"], c.gap_share)
        if c.runway != "*":
            e["appr"] += c.approaches
            e["low"] += c.reached_1000
    same_wd = [v["flights"] for d, v in per_day.items() if d.weekday() == day.weekday()]
    expected = float(np.median(same_wd)) if same_wd else None
    reasons = []
    k = P["ews.coverage_mad_k"]

    def far(x, series, label, floor):
        if x is None or len(series) < 4:
            return
        med = float(np.median(series))
        # a steady series has a MAD near 0; the floor stops tiny changes from looking anomalous
        mad = max(float(np.median(np.abs(np.array(series) - med))) * 1.4826, floor(med))
        if abs(x - med) > k * mad:
            reasons.append(f"{label}: {x:.2f} against a usual {med:.2f}")
    if expected is not None and len(same_wd) >= 2:
        all_f = [v["flights"] for v in per_day.values()]
        far(float(flights), all_f if len(same_wd) < 4 else same_wd, "flights seen", lambda m: max(2.0, 0.05 * abs(m)))
    far(gap, [v["gap"] for v in per_day.values()], "share of flights with gaps", lambda m: 0.02)
    if low is not None:
        far(low, [v["low"] / v["appr"] for v in per_day.values() if v["appr"]], "arrivals reaching 1,000 ft", lambda m: 0.02)
    rec = db.query(EwsCoverage).filter_by(icao=icao, day=day).first() or EwsCoverage(icao=icao, day=day)
    rec.flights, rec.expected, rec.gap_share, rec.low_share, rec.anomaly, rec.reasons = flights, expected, gap, low, bool(reasons), reasons
    db.add(rec)
    db.commit()
    return rec


def cells_day(db: Session, icao: str, day: date, P: dict) -> list[dict]:
    """Feature vectors for one aerodrome-day (FR-EWS-001/002)."""
    apps = db.query(GaApproach).filter(GaApproach.icao == icao, GaApproach.day == day,
                                       GaApproach.outcome.in_(["landed", "go_around"])).all()
    deps = db.query(GaDeparture).filter_by(icao=icao, day=day).all()
    if not apps and not deps:
        return []
    rows = [{"block": block_of(a.t_thr or a.t_start), "operator": a.operator or "OTH", "arr": 1, "dep": 0,
             "ga": int(a.outcome == "go_around"), "ga_low": int(a.ga_band == "low"), "ttl": a.ttl_s,
             "unstable": a.unstable, "extra": a.extra_40nm, "holds": a.holds} for a in apps]
    rows += [{"block": block_of(d.t_dep), "operator": d.operator or "OTH", "arr": 0, "dep": 1, "ga": 0, "ga_low": 0,
              "ttl": None, "unstable": None, "extra": None, "holds": 0} for d in deps]
    df = pd.DataFrame(rows)
    top = df.groupby("operator").size().sort_values(ascending=False).head(int(P["ews.top_operators"])).index
    df["op"] = np.where(df.operator.isin(top), df.operator, "OTHERS")
    # context per block: TDM deviation share of flights arriving here, TMA complexity, weather
    d0 = datetime(day.year, day.month, day.day)
    routes = [r.id for r in db.query(TdmRoute).filter_by(ades=icao)]
    tdm = db.query(TdmFlight).filter(TdmFlight.route_id.in_(routes), TdmFlight.day == day).all() if routes else []
    tdm_share = (sum(f.flag_lateral or f.flag_vertical for f in tdm) / len(tdm)) if tdm else np.nan
    tmas = [s.id for s in db.query(RefSector).filter(RefSector.kind == "TMA", RefSector.name.contains(icao))]
    cxi = {}
    if tmas:
        for c in db.query(CxiSectorHour).filter(CxiSectorHour.sector_id.in_(tmas), CxiSectorHour.hour >= d0,
                                                CxiSectorHour.hour < d0 + timedelta(days=1)):
            if c.index is not None:
                cxi.setdefault(block_of(c.hour), []).append(c.index)
    wx = db.query(WxReport).filter(WxReport.icao == icao, WxReport.observed >= d0, WxReport.observed < d0 + timedelta(days=1)).all()
    wxb: dict[int, dict] = {}
    for w in wx:
        b = wxb.setdefault(block_of(w.observed), {"ts": 0, "vis": [], "wind": []})
        b["ts"] += int(w.ts or w.vcts)
        if w.vis_m:
            b["vis"].append(w.vis_m / 1000)
        if w.wind_kt is not None:
            b["wind"].append(w.wind_kt)
    out = []

    def feats(sub, block):
        arr = int(sub.arr.sum())
        w = wxb.get(block, {"ts": 0, "vis": [], "wind": []})
        return {"arrivals": arr, "departures": int(sub.dep.sum()), "ga_rate": float(sub.ga.sum() / arr) if arr else 0.0,
                "ga_low": int(sub.ga_low.sum()), "ttl_median": float(sub.ttl.dropna().median()) if sub.ttl.notna().any() else np.nan,
                "unstable_share": float(sub.unstable.dropna().astype(float).mean()) if sub.unstable.notna().any() else np.nan,
                "extra_40nm": float(sub.extra.dropna().mean()) if sub.extra.notna().any() else np.nan,
                "holds": int(sub.holds.sum()), "tdm_dev_share": tdm_share,
                "cxi_tma": float(np.mean(cxi[block])) if block in cxi else np.nan, "ts_hours": w["ts"],
                "max_cross": float(max(w["wind"])) if w["wind"] else np.nan, "min_vis_km": float(min(w["vis"])) if w["vis"] else np.nan}
    for block, bsub in df.groupby("block"):
        small = []
        for op, osub in bsub.groupby("op"):
            if len(osub) < P["ews.min_movements"]:
                small.append(op)
                continue
            out.append({"block": int(block), "operator": op, "movements": len(osub), "features": feats(osub, block)})
        out.append({"block": int(block), "operator": "*", "movements": len(bsub), "features": feats(bsub, block)})
    out.append({"block": -1, "operator": "*", "movements": len(df), "features": feats(df, -1) | {"ts_hours": sum(v["ts"] for v in wxb.values())}})
    return out


def store_cells(db: Session, icao: str, day: date, cells: list[dict]):
    old = {(c.block, c.operator): c for c in db.query(EwsCell).filter_by(icao=icao, day=day)}
    for c in cells:
        rec = old.pop((c["block"], c["operator"]), None) or EwsCell(icao=icao, day=day, block=c["block"], operator=c["operator"])
        rec.movements = c["movements"]
        rec.features = {k: (None if (v is None or (isinstance(v, float) and np.isnan(v))) else v) for k, v in c["features"].items()}
        db.add(rec)
    for rec in old.values():
        db.delete(rec)
    db.commit()


def _matrix(cells: list[EwsCell]) -> np.ndarray:
    X = np.array([[np.nan if c.features.get(f) is None else float(c.features[f]) for f in FEATURES] for c in cells], float)
    ctx = np.array([[c.day.weekday(), float(c.day.month in (11, 12, 1, 2, 3, 4)), c.block] for c in cells], float)
    return np.hstack([X, ctx])


def train(db: Session, icao: str, day: date, P: dict) -> MlModel | None:
    from sklearn.ensemble import IsolationForest
    d0 = day - timedelta(days=int(P["ews.train_days"]))
    hist = (db.query(EwsCell).join(EwsCoverage, (EwsCoverage.icao == EwsCell.icao) & (EwsCoverage.day == EwsCell.day), isouter=True)
            .filter(EwsCell.icao == icao, EwsCell.day >= d0, EwsCell.day < day, (EwsCoverage.anomaly.is_(False)) | (EwsCoverage.id.is_(None)))
            .all())
    days = {c.day for c in hist}
    if len(days) < P["ews.min_history_days"]:
        return None
    X = _matrix(hist)
    med = np.nanmedian(X, axis=0)
    med = np.where(np.isnan(med), 0, med)
    Xf = np.where(np.isnan(X), med, X)
    iso = IsolationForest(n_estimators=200, random_state=0, contamination="auto").fit(Xf)
    train_scores = -iso.score_samples(Xf)
    last = db.query(MlModel).filter_by(kind="ews", scope=icao).order_by(MlModel.version.desc()).first()
    v = (last.version if last else 0) + 1
    path = config.data_path("models", f"ews_{icao}_v{v}.pkl")
    with open(path, "wb") as f:
        pickle.dump({"iso": iso, "median": med, "scores": np.sort(train_scores)}, f)
    m = MlModel(kind="ews", scope=icao, version=v, path=str(path), status="approved",
                metrics={"train_days": len(days), "cells": len(hist), "from": str(min(days)), "to": str(max(days))},
                card={"intended_use": "Flag unusual aerodrome-day-block-operator patterns for analyst review (FR-EWS-010).",
                      "method": "Isolation Forest, 200 trees, rolling window; explanation by robust z-scores."})
    db.add(m)
    db.commit()
    return m


def score_day(db: Session, icao: str, day: date, P: dict) -> dict:
    cov = db.query(EwsCoverage).filter_by(icao=icao, day=day).first()
    cells = db.query(EwsCell).filter_by(icao=icao, day=day).all()
    if not cells:
        return {"cells": 0}
    if cov and cov.anomaly:
        for c in cells:
            c.score, c.alert, c.explanation = None, False, [{"feature": "coverage", "text": "; ".join(cov.reasons)}]
        db.commit()
        raise_alert(db, "COVERAGE", icao, 1, f"ADS-B coverage anomaly at {icao} on {day}", "; ".join(cov.reasons),
                    f"Unusual ADS-B coverage at {icao} on {day}; the day is not scored", breakdown_key=str(day),
                    link=f"/ews?aerodrome={icao}")
        return {"cells": len(cells), "coverage_anomaly": True}
    m = db.query(MlModel).filter_by(kind="ews", scope=icao).order_by(MlModel.version.desc()).first()
    if m is None or m.created_at.date() < day - timedelta(days=7):
        m = train(db, icao, day, P) or m
    if m is None:
        return {"cells": len(cells), "skipped": f"{icao}: fewer than {P['ews.min_history_days']:.0f} days of history"}
    with open(m.path, "rb") as f:
        mdl = pickle.load(f)
    X = _matrix(cells)
    Xf = np.where(np.isnan(X), mdl["median"], X)
    raw = -mdl["iso"].score_samples(Xf)
    ref = mdl["scores"]
    pct = np.searchsorted(ref, raw) / max(len(ref), 1)
    # robust baselines per block and season
    hist = db.query(EwsCell).filter(EwsCell.icao == icao, EwsCell.day < day,
                                    EwsCell.day >= day - timedelta(days=int(P["ews.train_days"]))).all()
    n_alert = 0
    for c, s in zip(cells, pct):
        same = [h for h in hist if h.block == c.block and h.operator == c.operator and
                (h.day.month in (11, 12, 1, 2, 3, 4)) == (c.day.month in (11, 12, 1, 2, 3, 4))]
        expl = []
        for f in FEATURES:
            x = c.features.get(f)
            vals = [h.features.get(f) for h in same if h.features.get(f) is not None]
            if x is None or len(vals) < 8:
                continue
            med = float(np.median(vals))
            mad = float(np.median(np.abs(np.array(vals) - med))) * 1.4826
            if mad == 0:
                mad = max(1e-6, 0.1 * abs(med)) if med else 1.0
            z = (float(x) - med) / mad
            expl.append({"feature": f, "label": LABELS[f], "value": round(float(x), 3), "baseline": round(med, 3), "z": round(z, 2)})
        expl.sort(key=lambda e: -abs(e["z"]))
        c.explanation = expl[:5]
        c.score = round(float(s), 4)
        c.model_version = m.version
        unusual = sum(1 for e in expl if abs(e["z"]) > P["ews.mad_k"])
        c.alert = bool(s * 100 >= P["ews.score_pct"] and unusual >= P["ews.min_features"])
        if c.alert:
            n_alert += 1
            blk = "whole day" if c.block < 0 else f"{c.block * 4:02d}–{c.block * 4 + 4:02d} UTC"
            top = ", ".join(e["label"] for e in expl[:3])
            raise_alert(db, "EWS", icao, 1, f"Unusual pattern at {icao} on {day}, {blk}, operator {c.operator}",
                        f"Score {c.score:.3f}; furthest from baseline: {top}",
                        f"Unusual traffic pattern at {icao} on {day}, {blk}" + ("" if c.operator == "*" else ", one operator"),
                        breakdown_key=f"{day}:{c.block}:{c.operator}", link=f"/ews?aerodrome={icao}&day={day}")
    db.commit()
    return {"cells": len(cells), "alerts": n_alert, "model": m.version}
