"""GA jobs: daily detection, near-real-time refresh and model training."""
from __future__ import annotations

from datetime import datetime, timedelta

from ... import config
from ...core import navtrace, weather
from ...core.jobs import Ctx, job
from ...db import SessionLocal
from ...models import Aerodrome
from .detect import detect_day


def monitored(db) -> list[str]:
    return [a.icao for a in db.query(Aerodrome).filter_by(monitored=True).order_by(Aerodrome.icao)]


def ensure_weather(db, ctx: Ctx, icaos: list[str]):
    """Fetch missing weather days before detection (FR-GA-011)."""
    if not (config.BMKG_TOKEN or config.DEMO):
        ctx.warn("BMKG is not configured: weather fields stay empty and QNH 1013.25 hPa is assumed")
        return
    for icao in icaos:
        for d in weather.days_missing(db, icao, ctx.d0, ctx.d1):
            try:
                weather.fetch(db, [icao], datetime(d.year, d.month, d.day), datetime(d.year, d.month, d.day) + timedelta(days=1))
            except Exception as e:  # noqa: BLE001 - NFR-011: go on without weather
                ctx.warn(f"Weather for {icao} {d}: {e}")


@job("ga", "Go-around detection")
def run_ga(ctx: Ctx):
    db = SessionLocal()
    try:
        icaos = ctx.options.get("aerodromes") or monitored(db)
        ensure_weather(db, ctx, icaos)
        src = navtrace.source(db)
        days = list(ctx.days())
        total = len(days) * len(icaos)
        k = 0
        for d in days:
            for icao in icaos:
                ctx.options["_frac"] = k / max(total, 1)
                ctx.progress(k / max(total, 1), f"Go-arounds {icao} {d}")
                try:
                    s = detect_day(db, src, icao, d, ctx.params, ctx.param_version, ctx)
                    ctx.rows_read += s["read_points"]
                    ctx.rows_written += s["approaches"]
                    for rw in s["incomplete_runways"]:
                        ctx.warn(f"{icao} {rw} {d}: low-level coverage below threshold, marked incomplete")
                except navtrace.NavTraceUnavailable:
                    raise
                except ValueError as e:
                    ctx.warn(str(e))
                k += 1
    finally:
        db.close()


@job("ga_recent", "Go-around detection, today so far")
def run_ga_recent(ctx: Ctx):
    run_ga(ctx)


@job("ga_model", "Go-around risk model")
def run_ga_model(ctx: Ctx):
    from .model import train_all
    db = SessionLocal()
    try:
        res = train_all(db, ctx.params, ctx.options.get("scope"))
        ctx.rows_written = len(res)
        for r in res:
            if r.get("skipped"):
                ctx.warn(r["skipped"])
    finally:
        db.close()
