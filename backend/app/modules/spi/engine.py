"""SPI engine: periods, breakdowns, baselines and alert methods (SRS 6, FR-SPI-003..007, FR-GA-025..027)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sqlalchemy.orm import Session

from ...core.alerts import close_alert, raise_alert
from ...models import SpiDefinition, SpiObservation, now
from .formulas import REGISTRY

LEVEL_NAMES = {0: "within limits", 1: "alert level 1", 2: "alert level 2", 3: "alert level 3"}


def period_start(d: date, period: str) -> date:
    if period == "D":
        return d
    if period == "W":
        return d - timedelta(days=d.weekday())
    return d.replace(day=1)


def period_end(start: date, period: str) -> date:
    if period == "D":
        return start
    if period == "W":
        return start + timedelta(days=6)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def periods_between(d0: date, d1: date, period: str) -> list[date]:
    out, s = [], period_start(d0, period)
    while s <= d1:
        out.append(s)
        s = period_end(s, period) + timedelta(days=1)
    return out


def bkey(dims: list[str], row) -> str:
    return "|".join(f"{d}={row[d]}" for d in dims)


def approved(db: Session) -> list[SpiDefinition]:
    """Latest approved version of each SPI code."""
    out = {}
    for s in db.query(SpiDefinition).filter_by(status="approved").order_by(SpiDefinition.code, SpiDefinition.version):
        out[s.code] = s
    return list(out.values())


def compute(db: Session, spi: SpiDefinition, d0: date, d1: date, P: dict, alerts: bool = True,
            alert_recent_days: int = 14) -> int:
    """Compute and store observations of one SPI for every period overlapping [d0, d1]."""
    f = REGISTRY.get(spi.formula)
    if f is None:
        raise ValueError(f"SPI {spi.code}: formula '{spi.formula}' is not registered")
    if f.name == "manual":
        return 0
    hist_days = max(spi.baseline_window, 400 if spi.alert_method in ("doc9859", "ewma") else 0)
    first = min(period_start(d0, p) for p in spi.periods or ["M"])
    base = f.fn(db, first - timedelta(days=hist_days + 31), d1)
    written = 0
    breakdowns = [""] + [b for b in (spi.breakdowns or []) if b]
    for period in spi.periods or ["M"]:
        starts = periods_between(d0, d1, period)
        existing = {(o.period_start, o.breakdown_key): o for o in
                    db.query(SpiObservation).filter(SpiObservation.spi_code == spi.code, SpiObservation.spi_version == spi.version,
                                                    SpiObservation.period == period, SpiObservation.period_start.in_(starts))}
        for b in breakdowns:
            dims = [x for x in b.split("+") if x] if b else []
            if any(x not in f.dims for x in dims):
                continue
            written += _compute_breakdown(db, spi, f, base, period, starts, dims, P, alerts, alert_recent_days, existing)
    db.commit()
    return written


def _group(base: pd.DataFrame, dims: list[str], period: str) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["pstart", *dims, "num", "den", "conf"])
    df = base.copy()
    df["pstart"] = df.day.map(lambda d: period_start(d, period))
    for c in ("den", "conf"):
        if c not in df:
            df[c] = np.nan
    for d in dims:
        df[d] = df[d].fillna("").astype(str)
    g = df.groupby(["pstart", *dims], dropna=False).agg(num=("num", "sum"), den=("den", "sum"), conf=("conf", "sum"),
                                                        nmax=("num", "max"))
    return g.reset_index()


def _compute_breakdown(db, spi, f, base, period, starts, dims, P, alerts, recent_days, existing) -> int:
    g = _group(base, dims, period)
    scale = spi.scale or 1.0
    combos = [()] if not dims else sorted({tuple(r) for r in g[dims].itertuples(index=False)})
    written = 0
    alpha = spi.alert_params.get("p", P["alert.poisson_p"])
    for combo in combos:
        sub = g if not dims else g[(g[dims] == pd.Series(combo, index=dims)).all(axis=1)]
        by_start = {r.pstart: r for r in sub.itertuples()}
        key = "|".join(f"{d}={v}" for d, v in zip(dims, combo))
        bd = dict(zip(dims, combo))
        history_vals: list[float] = []
        ewma_z = None
        exceed_run = {1: 0, 2: 0, 3: 0}
        all_starts = sorted(set(by_start) | set(starts))
        for s in all_starts:
            r = by_start.get(s)
            num = float(r.num) if r is not None else 0.0
            den = float(r.den) if (r is not None and not pd.isna(r.den) and f.agg == "ratio") else None
            if f.agg == "ratio" and den is None:
                den = 0.0
            if f.agg == "ratio":
                value = num / den * scale if den else None
            elif f.agg == "max":
                value = float(r.nmax) * scale if r is not None else None
            else:
                value = num * scale
            reliable = (den or 0) >= (spi.min_denominator or 0) if f.agg == "ratio" else True
            state, levels, baseline = 0, {}, None
            if spi.alert_method == "poisson" and f.agg in ("ratio", "count"):
                state, levels, baseline = _poisson(g, sub, dims, combo, s, period, spi, num, den, P, alpha, f.agg)
            elif spi.alert_method == "ewma" and value is not None:
                state, levels, baseline, ewma_z = _ewma(history_vals, value, ewma_z, P, spi.direction)
            elif spi.alert_method == "doc9859" and value is not None:
                state, levels, baseline = _doc9859(history_vals, value, exceed_run, P, spi.direction)
            elif spi.alert_method == "fixed" and value is not None:
                lv = spi.alert_params.get("levels") or []
                levels = {f"l{i + 1}": v for i, v in enumerate(lv)}
                sgn = 1 if spi.direction == "up_bad" else -1
                state = max([i + 1 for i, v in enumerate(lv) if sgn * value >= sgn * v] or [0])
            if not reliable:
                state = 0
            if value is not None:
                history_vals.append(value)
            if s not in starts:
                continue
            conf_share = None
            if r is not None and not pd.isna(r.conf) and num:
                conf_share = round(float(r.conf) / num, 3)
            obs = existing.get((s, key))
            if obs is None:
                obs = SpiObservation(spi_code=spi.code, spi_version=spi.version, period=period, period_start=s,
                                     breakdown_key=key)
                db.add(obs)
                existing[(s, key)] = obs
            elif obs.source == "manual":
                continue
            obs.period_end, obs.breakdown = period_end(s, period), bd
            obs.numerator, obs.denominator = num, den
            obs.value = round(value, 4) if value is not None else None
            obs.baseline = round(baseline, 4) if baseline is not None else None
            obs.levels, obs.alert_state, obs.reliable, obs.confirmed_share = levels, int(state), bool(reliable), conf_share
            obs.computed_at = now()
            written += 1
            if alerts and period_end(s, period) >= date.today() - timedelta(days=recent_days):
                _alert(db, spi, obs, P)
    return written


def _poisson(g, sub, dims, combo, s, period, spi, num, den, P, alpha, agg):
    """Count in the period against the baseline rate of the preceding window (FR-GA-025, FR-GA-027)."""
    w0 = s - timedelta(days=spi.baseline_window)
    hist = sub[(sub.pstart >= w0) & (sub.pstart < s)]
    hn = float(hist.num.sum())
    if agg == "ratio":
        hd = float(hist.den.sum())
        if hd < P["alert.min_baseline_den"] or (den or 0) < P["alert.min_period_den"]:
            return 0, {"note": "too few movements"}, (hn / hd * (spi.scale or 1) if hd else None)
        rate = hn / hd
        expected = rate * (den or 0)
        baseline = rate * (spi.scale or 1)
    else:
        n_periods = len(hist.pstart.unique()) or 1
        n_periods = max(n_periods, round(spi.baseline_window / {"D": 1, "W": 7, "M": 30.4}[period]))
        expected = hn / n_periods
        baseline = expected * (spi.scale or 1)
        if hn == 0 and expected == 0:
            return 0, {}, baseline
    if expected <= 0:
        expected = 0.5 / max(1.0, float(hist.den.sum()) if agg == "ratio" else 1.0) * (den or 1)
    p = float(poisson.sf(num - 1, expected)) if num > 0 else 1.0
    levels = {"expected": round(expected, 3), "p": round(p, 5), "alpha": alpha}
    if spi.direction == "down_bad":
        p = float(poisson.cdf(num, expected))
        levels["p"] = round(p, 5)
    state = 3 if p < alpha / 100 else 2 if p < alpha / 10 else 1 if p < alpha else 0
    return state, levels, baseline


def _ewma(hist, value, z, P, direction):
    lam, L = P["alert.ewma_lambda"], P["alert.ewma_L"]
    n = int(P["alert.doc9859_history"])
    h = hist[-n:]
    if len(h) < 4:
        return 0, {"note": "history too short"}, None, value if z is None else lam * value + (1 - lam) * z
    mu, sd = float(np.mean(h)), float(np.std(h, ddof=1)) or 1e-9
    z = mu if z is None else z
    z = lam * value + (1 - lam) * z
    lim = L * sd * math.sqrt(lam / (2 - lam))
    sgn = 1 if direction == "up_bad" else -1
    state = 1 if sgn * (z - mu) > lim else 0
    return state, {"ewma": round(z, 4), "ucl": round(mu + lim, 4), "lcl": round(mu - lim, 4)}, mu, z


def _doc9859(hist, value, run, P, direction):
    """ICAO Doc 9859: alert levels at mean + 1, 2, 3 SD of the preceding periods; one point above level 3,
    two consecutive above level 2, or three consecutive above level 1 (FR-SPI-005)."""
    n = int(P["alert.doc9859_history"])
    h = hist[-n:]
    if len(h) < 4:
        return 0, {"note": "history too short"}, None
    mu, sd = float(np.mean(h)), float(np.std(h, ddof=1))
    sgn = 1 if direction == "up_bad" else -1
    lv = {f"l{k}": round(mu + sgn * k * sd, 4) for k in (1, 2, 3)}
    for k in (1, 2, 3):
        run[k] = run[k] + 1 if sgn * value > sgn * lv[f"l{k}"] else 0
    state = 3 if run[3] >= 1 else 2 if run[2] >= 2 else 1 if run[1] >= 3 else 0
    return state, lv, mu


def _alert(db, spi, obs, P):
    where = ", ".join(f"{k} {v}" if k != "operator" else "one operator" for k, v in (obs.breakdown or {}).items()) or "all"
    pname = {"D": "day", "W": "week", "M": "month"}[obs.period]
    link = f"/spis/{spi.code}?period={obs.period}&breakdown={obs.breakdown_key}"
    if obs.alert_state > 0:
        val = f"{obs.value:.2f}" if obs.value is not None else "–"
        title = f"{spi.code} {spi.name}: {LEVEL_NAMES[obs.alert_state]} ({where}, {pname} of {obs.period_start})"
        public = f"{spi.code} {spi.name} at {LEVEL_NAMES[obs.alert_state]} for {where}, {pname} of {obs.period_start}: {val} {spi.unit}".strip()
        if "operator" in (obs.breakdown or {}):
            public = f"{spi.code} {spi.name} at {LEVEL_NAMES[obs.alert_state]} for one operator, {pname} of {obs.period_start}"
        raise_alert(db, "SPI", spi.code, obs.alert_state, title,
                    f"Value {val} {spi.unit}, baseline {obs.baseline}, levels {obs.levels}", public,
                    breakdown_key=f"{obs.period}:{obs.breakdown_key}", link=link,
                    data={"value": obs.value, "baseline": obs.baseline, "levels": obs.levels},
                    quiet_hours=P["alert.quiet_hours"])
    else:
        close_alert(db, "SPI", spi.code, f"{obs.period}:{obs.breakdown_key}")


def target_status(db: Session, spi: SpiDefinition, period: str = "M") -> dict:
    """On track / off track against the SPT, from the trend of the last 6 periods (FR-SPI-007)."""
    if spi.target is None:
        return {"status": "no target"}
    obs = (db.query(SpiObservation).filter_by(spi_code=spi.code, period=period, breakdown_key="")
           .filter(SpiObservation.value.isnot(None)).order_by(SpiObservation.period_start.desc()).limit(6).all())
    if len(obs) < 3:
        return {"status": "too little data"}
    vals = np.array([o.value for o in reversed(obs)])
    slope = float(np.polyfit(np.arange(len(vals)), vals, 1)[0])
    last = float(vals[-1])
    good = last <= spi.target if spi.direction == "up_bad" else last >= spi.target
    improving = slope < 0 if spi.direction == "up_bad" else slope > 0
    return {"status": "met" if good else ("on track" if improving else "off track"), "last": round(last, 4),
            "slope_per_period": round(slope, 4), "target": spi.target,
            "target_date": str(spi.target_date) if spi.target_date else None}


DEFAULT_SPIS = [
    # code, name, formula, unit, scale, breakdowns, periods, method, min_den, description
    ("GA-01", "Go-around rate", "ga_rate", "per 1,000 approaches", 1000,
     ["aerodrome", "aerodrome+runway", "operator", "aerodrome+hour_local", "aerodrome+operator"], ["W", "M"], "poisson", 50,
     "Go-arounds per 1,000 approaches; runway-days with incomplete low-level coverage are left out."),
    ("GA-02", "Low go-around rate", "ga_low_rate", "per 1,000 approaches", 1000, ["aerodrome", "aerodrome+runway"], ["M"],
     "poisson", 50, "Go-arounds initiated below 500 ft per 1,000 approaches."),
    ("GA-03", "Repeated go-arounds", "ga_repeated", "flights", 1, ["aerodrome"], ["M"], "poisson", 0,
     "Flights with two or more go-arounds."),
    ("GA-04", "Weather-linked go-arounds", "ga_weather_share", "%", 100, ["aerodrome"], ["M"], "none", 5,
     "Share of go-arounds with thunderstorm, tailwind above 5 kt or crosswind above 15 kt."),
    ("GA-05", "Unstable-gate share", "ga_unstable_share", "%", 100, ["aerodrome", "aerodrome+runway"], ["M"], "doc9859", 50,
     "Share of approaches more than 1 dot off the 3° path or 30 kt above the runway's median speed at 1,000 ft (indicative: ground speed)."),
    ("TDM-01", "Lateral deviation share", "tdm_lateral_share", "%", 100, ["route", "unit"], ["M"], "doc9859", 30,
     "Share of flights deviating laterally beyond the limit at any critical point."),
    ("TDM-02", "Vertical deviation share", "tdm_vertical_share", "%", 100, ["route", "unit"], ["M"], "doc9859", 30,
     "Share of flights beyond the vertical deviation limit at any critical point."),
    ("TDM-03", "Off-pattern share", "tdm_offpattern_share", "%", 100, ["route"], ["M"], "doc9859", 30,
     "Share of flights not following any nominal path."),
    ("TDM-04", "Critical-point lateral deviation share", "tdm_cp_lateral_share", "%", 100, ["route+critical_point"], ["M"],
     "none", 30, "Share of flights deviating laterally at each critical point."),
    ("CXI-01", "Complexity index", "cxi_index_mean", "index", 1, ["sector", "unit", "kind"], ["M"], "none", 0,
     "Mean complexity index per sector-hour (0 = the sector's own baseline mean)."),
    ("CXI-02", "High-complexity hours", "cxi_top5_hours", "hours", 1, ["sector", "unit"], ["M"], "none", 0,
     "Hours in the sector's top 5 % of its baseline."),
    ("CXI-03", "PRU complexity score", "cxi_pru_score", "", 1, ["unit", "kind"], ["M"], "none", 0,
     "Vertical, horizontal and speed interaction hours per flight hour (EUROCONTROL PRU method)."),
    ("CXI-04", "Sector utilisation", "cxi_utilisation", "%", 100, ["sector"], ["M"], "none", 0,
     "Peak hourly entries as a share of declared capacity (sectors with a capacity only)."),
    ("EWS-01", "Early-warning anomalies", "ews_anomalies", "cells", 1, ["aerodrome"], ["M"], "none", 0,
     "Aerodrome-day-block-operator cells flagged as unusual."),
    ("EWS-02", "Anomalies judged real", "ews_real_share", "%", 100, ["aerodrome"], ["M"], "none", 0,
     "Share of labelled early-warning alerts judged a real safety concern."),
    ("EWS-03", "Coverage anomalies", "ews_coverage_anomalies", "days", 1, ["aerodrome"], ["M"], "none", 0,
     "Aerodrome-days with unusual ADS-B coverage, excluded from scoring."),
]


def seed_defaults(db: Session):
    if db.query(SpiDefinition).count():
        return
    owners = {"GA": "Safety and Quality", "TDM": "Safety Risk Management", "CXI": "ATS Operations", "EWS": "Safety and Quality"}
    for code, name, fml, unit, scale, bds, periods, method, mind, desc in DEFAULT_SPIS:
        mod = code.split("-")[0]
        db.add(SpiDefinition(code=code, version=1, name=name, description=desc, owner_unit=owners[mod], module=mod,
                             formula=fml, unit=unit, scale=scale, breakdowns=bds, periods=periods, alert_method=method,
                             baseline_window=90, min_denominator=mind, status="approved", created_by="system",
                             approved_by="system (initial register, to be reviewed by the SRB)", approved_at=now(),
                             effective_from=date(2020, 1, 1), change_note="Initial register from the SRS"))
    db.commit()
