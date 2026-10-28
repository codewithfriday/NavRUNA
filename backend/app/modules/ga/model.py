"""Go-around risk factors and model (SRS 5.4, FR-GA-030..034).

After-the-event analysis for safety management, following Figuet et al. (2020) and SafeOPS: which conditions
make a go-around more likely at each runway. It is not a warning tool for controllers.
"""
from __future__ import annotations

import math
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from ... import config
from ...models import Aerodrome, GaApproach, MlModel, now

GATE_FEATURES = {"6": ["g6_dev", "g6_lat", "g6_gs", "g6_vr"], "4": ["g4_dev", "g4_lat", "g4_gs", "g4_vr"],
                 "2": ["g2_dev", "g2_lat", "g2_gs", "g2_vr"]}
CONTEXT = ["ttl_s", "since_dep_s", "head_kt", "cross_kt", "gust_kt", "vis_km", "ceiling_kft", "ts", "cb",
           "precip", "hour_local", "weekday", "wet", "arr_hour"]
MONOTONE = {"ts": 1, "cb": 1, "gust_kt": 1, "cross_kt": 1, "head_kt": -1, "ttl_s": -1, "precip": 1}


def dataset(db: Session, icaos: list[str] | None = None) -> pd.DataFrame:
    q = db.query(GaApproach).filter(GaApproach.outcome.in_(["landed", "go_around"]), GaApproach.incomplete.is_(False))
    if icaos:
        q = q.filter(GaApproach.icao.in_(icaos))
    offs = {a.icao: a.utc_offset for a in db.query(Aerodrome)}
    rows = []
    for a in q.yield_per(2000):
        g = a.gates or {}
        r = {"id": a.id, "icao": a.icao, "runway": a.runway, "day": a.day, "t": a.t_thr, "operator": a.operator,
             "ga": a.outcome == "go_around", "confirmed": a.validation == "confirmed", "rejected": a.validation == "rejected",
             "ttl_s": a.ttl_s, "since_dep_s": a.since_dep_s, "head_kt": a.head_kt, "cross_kt": a.cross_kt,
             "gust_kt": a.gust_kt, "vis_km": a.vis_m / 1000 if a.vis_m else None,
             "ceiling_kft": a.ceiling_ft / 1000 if a.ceiling_ft else 10.0, "ts": float(a.ts), "cb": float(a.cb),
             "precip": a.precip, "hour_local": ((a.t_thr.hour + offs.get(a.icao, 7)) % 24) if a.t_thr else None,
             "weekday": a.day.weekday(), "wet": float(a.day.month in (11, 12, 1, 2, 3, 4))}
        for gate in ("6", "4", "2"):
            v = g.get(gate) or {}
            r.update({f"g{gate}_dev": v.get("dev_ft"), f"g{gate}_lat": v.get("lat_m"), f"g{gate}_gs": v.get("gs"),
                      f"g{gate}_vr": v.get("vr")})
        rows.append(r)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["hour"] = pd.to_datetime(df.t).dt.floor("h")
    df["arr_hour"] = df.groupby(["icao", "hour"]).id.transform("count")
    return df


def factor_effects(df: pd.DataFrame) -> dict:
    """Empirical go-around rates by condition, with Wilson 95 % intervals (FR-GA-033)."""
    def rate(mask):
        n = int(mask.sum())
        k = int((df.ga & mask).sum())
        if n == 0:
            return {"n": 0, "ga": 0, "per_1000": None, "lo": None, "hi": None}
        p = k / n
        z = 1.96
        c = (p + z * z / (2 * n)) / (1 + z * z / n)
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return {"n": n, "ga": k, "per_1000": round(1000 * p, 2), "lo": round(1000 * max(0, c - h), 2), "hi": round(1000 * (c + h), 2)}
    out = {}
    if df.empty:
        return out
    out["thunderstorm"] = {"yes": rate(df.ts == 1), "no": rate(df.ts == 0)}
    tw = -df.head_kt
    out["tailwind"] = {"none (<0 kt)": rate(tw <= 0), "0–5 kt": rate((tw > 0) & (tw <= 5)), "above 5 kt": rate(tw > 5)}
    out["crosswind"] = {"0–10 kt": rate(df.cross_kt <= 10), "10–15 kt": rate((df.cross_kt > 10) & (df.cross_kt <= 15)),
                        "above 15 kt": rate(df.cross_kt > 15)}
    out["time_to_leader"] = {"under 90 s": rate(df.ttl_s < 90), "90–120 s": rate((df.ttl_s >= 90) & (df.ttl_s < 120)),
                             "120–180 s": rate((df.ttl_s >= 120) & (df.ttl_s < 180)), "180 s or more": rate(df.ttl_s >= 180)}
    out["season"] = {"wet (Nov–Apr)": rate(df.wet == 1), "dry (May–Oct)": rate(df.wet == 0)}
    if "g4_dev" in df:
        dev = df.g4_dev.abs()
        out["glide_path_at_4nm"] = {"within 75 ft": rate(dev <= 75), "75–150 ft": rate((dev > 75) & (dev <= 150)),
                                    "above 150 ft": rate(dev > 150)}
    ts = out["thunderstorm"]
    if ts["yes"]["per_1000"] and ts["no"]["per_1000"]:
        out["thunderstorm_ratio"] = round(ts["yes"]["per_1000"] / ts["no"]["per_1000"], 2)
    return out


def _fit(df: pd.DataFrame, gate: str, P: dict) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss, precision_score, recall_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    feats = sum((GATE_FEATURES[g] for g in ("6", "4", "2") if float(g) >= float(gate)), []) + CONTEXT
    d = df[~df.rejected & (~df.ga | df.confirmed)].sort_values("t")
    y = d.ga.astype(int).values
    X = d[feats].astype(float)
    months = d.day.map(lambda v: (v.year, v.month))
    order = sorted(set(months))
    cut = order[max(1, int(len(order) * 0.7)) - 1] if len(order) > 1 else order[0]
    train = (months <= cut).values
    test = ~train if (~train).sum() and y[~train].sum() else train
    cst = [MONOTONE.get(f, 0) for f in feats]
    gb = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, monotonic_cst=cst,
                                        class_weight="balanced", random_state=0)
    gb.fit(X[train], y[train])
    lr = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(max_iter=500, class_weight="balanced"))
    lr.fit(X[train], y[train])
    res = {"gate_nm": float(gate), "features": feats, "n_train": int(train.sum()), "n_test": int(test.sum()),
           "ga_train": int(y[train].sum()), "ga_test": int(y[test].sum()), "test_period_from": str(d.day[test].min()),
           "test_same_as_train": bool((test == train).all())}
    for name, m in (("gradient_boosting", gb), ("logistic", lr)):
        p = m.predict_proba(X[test])[:, 1]
        p_train = m.predict_proba(X[train])[:, 1]
        # threshold maximising F1 on the training months
        best_t, best_f = 0.5, -1
        for th in np.unique(np.quantile(p_train, np.linspace(0.5, 0.999, 60))):
            pr = precision_score(y[train], p_train >= th, zero_division=0)
            rc = recall_score(y[train], p_train >= th, zero_division=0)
            f = 2 * pr * rc / (pr + rc) if pr + rc else 0
            if f > best_f:
                best_t, best_f = th, f
        res[name] = {"pr_auc": round(float(average_precision_score(y[test], p)), 3) if y[test].sum() else None,
                     "base_rate": round(float(y[test].mean()), 4),
                     "threshold": round(float(best_t), 4),
                     "precision": round(float(precision_score(y[test], p >= best_t, zero_division=0)), 3),
                     "recall": round(float(recall_score(y[test], p >= best_t, zero_division=0)), 3),
                     "brier": round(float(brier_score_loss(y[test], p)), 5)}
    if hasattr(lr[-1], "coef_"):
        res["logistic_odds_per_sd"] = {f: round(float(math.exp(c)), 3) for f, c in zip(feats, lr[-1].coef_[0])}
    res["_models"] = {"gradient_boosting": gb, "logistic": lr}
    return res


def train_all(db: Session, P: dict, scope: str | None = None) -> list[dict]:
    out = []
    df_all = dataset(db)
    if df_all.empty:
        return [{"skipped": "No approaches stored yet"}]
    conf = df_all[df_all.ga & df_all.confirmed]
    scopes = []
    for icao, n in conf.groupby("icao").size().items():
        scopes.append((icao, [icao]) if n >= P["ga.min_model_events"] else None)
    pooled = [s for s in scopes if s is None]
    scopes = [s for s in scopes if s]
    small = [i for i, n in conf.groupby("icao").size().items() if n < P["ga.min_model_events"]]
    if small and len(conf[conf.icao.isin(small)]) >= P["ga.min_model_events"]:
        scopes.append(("pooled", small))
    if scope:
        scopes = [s for s in scopes if s[0] == scope]
    if not scopes:
        return [{"skipped": f"{len(conf)} confirmed go-arounds; {int(P['ga.min_model_events'])} needed before a model is trained "
                            "(FR-GA-031). Validate detected go-arounds to build the training set."}]
    for name, icaos in scopes:
        df = df_all[df_all.icao.isin(icaos)]
        gates = {g: _fit(df, g, P) for g in ("6", "4", "2")}
        models = {g: r.pop("_models") for g, r in gates.items()}
        last = db.query(MlModel).filter_by(kind="ga", scope=name).order_by(MlModel.version.desc()).first()
        v = (last.version if last else 0) + 1
        path = config.data_path("models", f"ga_{name}_v{v}.pkl")
        with open(path, "wb") as f:
            pickle.dump(models, f)
        effects = factor_effects(df)
        card = {
            "intended_use": "After-the-event analysis of go-around risk factors for the SMS; not for real-time ATC use.",
            "data": {"aerodromes": icaos, "from": str(df.day.min()), "to": str(df.day.max()), "approaches": int(len(df)),
                     "confirmed_go_arounds": int((df.ga & df.confirmed).sum())},
            "method": "Gradient-boosted trees with monotonic constraints, compared with logistic regression; time-based split "
                      "(earlier months train, later months test); positives are analyst-confirmed go-arounds only.",
            "limits": ["ADS-B gives ground speed, not airspeed; wind aloft is unknown",
                       "Weather is the aerodrome METAR/SPECI nearest in time, not conditions on final",
                       "Rare events: precision and recall have wide uncertainty; compare with SafeOPS (Munich, 646 go-arounds: "
                       "precision 0.88–0.91, recall 0.08–0.34 at 6–2 NM)"],
            "standard": "ISO/IEC 23894:2023 model card (FR-GA-034)",
        }
        m = MlModel(kind="ga", scope=name, version=v, path=str(path), metrics={"gates": gates, "effects": effects},
                    card=card, status="draft")
        db.add(m)
        db.commit()
        out.append({"scope": name, "version": v, "id": m.id})
    return out
