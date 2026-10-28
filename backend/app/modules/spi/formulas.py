"""Registered SPI calculations (FR-SPI-002). A definition may only use a formula listed here.

Each formula returns day-level rows: a DataFrame with `day`, the breakdown dimension columns it supports,
`num` and (for ratios) `den`; optional `conf` = confirmed events among `num` (FR-GA-018) and `incomplete`.
The engine sums them per period and breakdown; `agg` says how the value is formed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd
from sqlalchemy.orm import Session

from ...models import (Aerodrome, CxiSectorHour, EwsCell, EwsCoverage, GaApproach, RefSector, TdmCriticalPoint,
                       TdmFlight, TdmPoint, TdmRoute)


@dataclass
class Formula:
    name: str
    title: str
    module: str
    dims: tuple[str, ...]
    agg: str            # ratio | count | max
    fn: Callable[[Session, date, date], pd.DataFrame]
    unit: str = ""


REGISTRY: dict[str, Formula] = {}


def formula(name, title, module, dims, agg="ratio", unit=""):
    def deco(fn):
        REGISTRY[name] = Formula(name, title, module, tuple(dims), agg, fn, unit)
        return fn
    return deco


# ------------------------------------------------------------------ GA
GA_DIMS = ("aerodrome", "runway", "operator", "aircraft_type", "hour_utc", "hour_local", "unit")


def _ga_frame(db: Session, d0: date, d1: date) -> pd.DataFrame:
    q = db.query(GaApproach).filter(GaApproach.day >= d0, GaApproach.day <= d1,
                                    GaApproach.outcome.in_(["landed", "go_around"]))
    offs = {a.icao: (a.utc_offset, a.unit) for a in db.query(Aerodrome)}
    rows = []
    for a in q.yield_per(5000):
        off, unit = offs.get(a.icao, (7, ""))
        hu = a.t_thr.hour if a.t_thr else None
        rows.append({"day": a.day, "aerodrome": a.icao, "runway": a.runway, "operator": a.operator or "",
                     "aircraft_type": a.aircraft_type or "", "hour_utc": hu,
                     "hour_local": (hu + off) % 24 if hu is not None else None, "unit": unit,
                     "ga": a.outcome == "go_around" and a.validation != "rejected",
                     "conf": a.outcome == "go_around" and a.validation == "confirmed",
                     "band": a.ga_band, "repeated": a.repeated, "gufi": a.gufi, "incomplete": a.incomplete,
                     "ts": a.ts, "head": a.head_kt, "cross": a.cross_kt, "unstable": a.unstable})
    return pd.DataFrame(rows)


@formula("ga_rate", "Go-arounds per 1,000 approaches", "GA", GA_DIMS)
def ga_rate(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[~df.incomplete]                                  # FR-GA-020
    return df.assign(num=df.ga.astype(int), den=1, conf=df.conf.astype(int))


@formula("ga_low_rate", "Go-arounds initiated below 500 ft per 1,000 approaches", "GA", GA_DIMS)
def ga_low_rate(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[~df.incomplete]
    return df.assign(num=(df.ga & (df.band == "low")).astype(int), den=1, conf=(df.conf & (df.band == "low")).astype(int))


@formula("ga_repeated", "Flights with two or more go-arounds", "GA", ("aerodrome", "runway", "operator", "unit"), agg="count")
def ga_repeated(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df.ga & df.repeated].drop_duplicates("gufi")
    return df.assign(num=1)


@formula("ga_weather_share", "Share of go-arounds with thunderstorm, tailwind above 5 kt or crosswind above 15 kt", "GA",
         ("aerodrome", "runway", "unit"))
def ga_weather_share(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df.ga]
    wx = df.ts | (df["head"].fillna(0) < -5) | (df["cross"].fillna(0) > 15)
    return df.assign(num=wx.astype(int), den=1)


@formula("ga_unstable_share", "Share of approaches off the glide path or fast at 1,000 ft", "GA", GA_DIMS)
def ga_unstable_share(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df.unstable.notna() & ~df.incomplete]
    return df.assign(num=df.unstable.astype(bool).astype(int), den=1)


@formula("ga_count", "Go-arounds", "GA", GA_DIMS, agg="count")
def ga_count(db, d0, d1):
    df = _ga_frame(db, d0, d1)
    if df.empty:
        return df
    return df[df.ga].assign(num=1, conf=df[df.ga].conf.astype(int))


# ------------------------------------------------------------------ TDM
def _tdm_frame(db, d0, d1):
    routes = {r.id: (f"{r.adep}-{r.ades}", r.unit) for r in db.query(TdmRoute)}
    rows = [{"day": f.day, "route": routes.get(f.route_id, ("?", ""))[0], "unit": routes.get(f.route_id, ("?", ""))[1],
             "operator": f.operator or "", "lat": f.flag_lateral, "vert": f.flag_vertical, "off": f.off_pattern}
            for f in db.query(TdmFlight).filter(TdmFlight.day >= d0, TdmFlight.day <= d1).yield_per(5000)]
    return pd.DataFrame(rows)


@formula("tdm_lateral_share", "Share of flights deviating laterally beyond the limit at any critical point", "TDM",
         ("route", "unit", "operator"))
def tdm_lateral_share(db, d0, d1):
    df = _tdm_frame(db, d0, d1)
    return df.assign(num=df.lat.astype(int), den=1) if not df.empty else df


@formula("tdm_vertical_share", "Share of flights beyond the vertical deviation limit at any critical point", "TDM",
         ("route", "unit", "operator"))
def tdm_vertical_share(db, d0, d1):
    df = _tdm_frame(db, d0, d1)
    return df.assign(num=df.vert.astype(int), den=1) if not df.empty else df


@formula("tdm_offpattern_share", "Share of off-pattern flights", "TDM", ("route", "unit", "operator"))
def tdm_offpattern_share(db, d0, d1):
    df = _tdm_frame(db, d0, d1)
    return df.assign(num=df.off.astype(int), den=1) if not df.empty else df


@formula("tdm_cp_lateral_share", "Share of flights deviating laterally at a critical point", "TDM", ("route", "critical_point"))
def tdm_cp_lateral_share(db, d0, d1):
    routes = {r.id: f"{r.adep}-{r.ades}" for r in db.query(TdmRoute)}
    cps = {c.id: (c.name, c.route_id) for c in db.query(TdmCriticalPoint)}
    q = (db.query(TdmPoint.cp_id, TdmPoint.flag_lateral, TdmFlight.day)
         .join(TdmFlight, TdmFlight.id == TdmPoint.flight_id).filter(TdmFlight.day >= d0, TdmFlight.day <= d1))
    rows = [{"day": d, "critical_point": cps.get(c, ("?", 0))[0], "route": routes.get(cps.get(c, ("?", 0))[1], "?"),
             "num": int(fl), "den": 1} for c, fl, d in q.yield_per(10000)]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ CXI
def _cxi_frame(db, d0, d1):
    from datetime import datetime, timedelta
    sec = {s.id: (s.name, s.unit, s.kind, s.declared_capacity) for s in db.query(RefSector)}
    sec[0] = ("Outside controlled sectors", "", "OUT", None)
    rows = []
    for c in db.query(CxiSectorHour).filter(CxiSectorHour.hour >= datetime(d0.year, d0.month, d0.day),
                                            CxiSectorHour.hour < datetime(d1.year, d1.month, d1.day) + timedelta(days=1)).yield_per(10000):
        name, unit, kind, cap = sec.get(c.sector_id, ("?", "", "", None))
        rows.append({"day": c.hour.date(), "sector": name, "unit": unit, "kind": kind, "index": c.index, "top5": c.top5,
                     "vhs": c.vertical_h + c.horizontal_h + c.speed_h, "fh": c.flight_hours, "entries": c.entries, "cap": cap})
    return pd.DataFrame(rows)


@formula("cxi_index_mean", "Mean complexity index per sector-hour", "CXI", ("sector", "unit", "kind"))
def cxi_index_mean(db, d0, d1):
    df = _cxi_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df["index"].notna() & (df.kind != "OUT")]
    return df.assign(num=df["index"], den=1)


@formula("cxi_top5_hours", "Hours in the sector's top 5 % of its baseline", "CXI", ("sector", "unit", "kind"), agg="count")
def cxi_top5_hours(db, d0, d1):
    df = _cxi_frame(db, d0, d1)
    return df[df.top5].assign(num=1) if not df.empty else df


@formula("cxi_pru_score", "PRU complexity score (interaction hours per flight hour)", "CXI", ("unit", "sector", "kind"))
def cxi_pru_score(db, d0, d1):
    df = _cxi_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df.kind != "OUT"]
    return df.assign(num=df.vhs, den=df.fh)


@formula("cxi_utilisation", "Peak hourly entries as a share of declared capacity", "CXI", ("sector", "unit"), agg="max")
def cxi_utilisation(db, d0, d1):
    df = _cxi_frame(db, d0, d1)
    if df.empty:
        return df
    df = df[df.cap.notna() & (df.cap > 0)]
    return df.assign(num=df.entries / df.cap)


# ------------------------------------------------------------------ EWS
@formula("ews_anomalies", "Early-warning anomalies", "EWS", ("aerodrome",), agg="count")
def ews_anomalies(db, d0, d1):
    rows = [{"day": c.day, "aerodrome": c.icao, "num": 1} for c in
            db.query(EwsCell).filter(EwsCell.day >= d0, EwsCell.day <= d1, EwsCell.alert.is_(True))]
    return pd.DataFrame(rows)


@formula("ews_real_share", "Share of labelled anomalies judged a real safety concern", "EWS", ("aerodrome",))
def ews_real_share(db, d0, d1):
    rows = [{"day": c.day, "aerodrome": c.icao, "num": int(c.label == "real"), "den": 1} for c in
            db.query(EwsCell).filter(EwsCell.day >= d0, EwsCell.day <= d1, EwsCell.alert.is_(True), EwsCell.label != "")]
    return pd.DataFrame(rows)


@formula("ews_coverage_anomalies", "Days with ADS-B coverage anomalies", "EWS", ("aerodrome",), agg="count")
def ews_coverage_anomalies(db, d0, d1):
    rows = [{"day": c.day, "aerodrome": c.icao, "num": 1} for c in
            db.query(EwsCoverage).filter(EwsCoverage.day >= d0, EwsCoverage.day <= d1, EwsCoverage.anomaly.is_(True))]
    return pd.DataFrame(rows)


@formula("manual", "Value entered by hand", "MAN", ("aerodrome", "unit"), agg="count")
def manual(db, d0, d1):
    return pd.DataFrame()
