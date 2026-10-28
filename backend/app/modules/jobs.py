"""Jobs for TDM, CXI, EWS, SPI, weather and the daily and monthly pipelines (FR-CORE-001)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import config
from ..core import navtrace, weather
from ..core.jobs import Ctx, job
from ..db import SessionLocal
from ..models import AppSetting, Report, TdmClusterSet, TdmRoute, utc_now
from .ga.jobs import monitored, run_ga


@job("weather", "Weather download (BMKG)")
def run_weather(ctx: Ctx):
    if not (config.BMKG_TOKEN or config.DEMO):
        ctx.warn("BMKG is not configured (NAVRUNA_BMKG_TOKEN)")
        return
    db = SessionLocal()
    try:
        st = monitored(db)
        t0 = datetime(ctx.d0.year, ctx.d0.month, ctx.d0.day)
        t1 = datetime(ctx.d1.year, ctx.d1.month, ctx.d1.day) + timedelta(days=1)
        r = weather.fetch(db, st, t0, min(t1, utc_now() + timedelta(hours=1)))
        ctx.rows_written = r["reports"]
        if r["unparsed"]:
            ctx.warn(f"{r['unparsed']} reports could not be fully parsed")
    finally:
        db.close()


@job("tdm", "Trajectory deviation monitor")
def run_tdm(ctx: Ctx):
    from .tdm.core import build_clusters, monitor_day
    db = SessionLocal()
    try:
        src = navtrace.source(db)
        routes = db.query(TdmRoute).filter_by(active=True).all()
        rebuild = ctx.options.get("rebuild", False)
        for k, r in enumerate(routes):
            ctx.progress(k / max(len(routes), 1), f"Routes {r.adep}-{r.ades}")
            cs = db.query(TdmClusterSet).filter_by(route_id=r.id, active=True).first()
            # monthly rebuild on the rolling window, or when none exists (FR-TDM-005)
            if rebuild or cs is None or (ctx.d1.day == 1 and cs.window_to < ctx.d1 - timedelta(days=20)):
                res = build_clusters(db, src, r, ctx.d1 - timedelta(days=1) if ctx.d1.day == 1 else ctx.d1, ctx.params)
                if res.get("skipped"):
                    ctx.warn(res["skipped"])
                    continue
            for d in ctx.days():
                res = monitor_day(db, src, r, d, ctx.params)
                if res.get("skipped"):
                    ctx.warn(res["skipped"])
                else:
                    ctx.rows_written += res["flights"]
    finally:
        db.close()


@job("cxi", "Complexity index")
def run_cxi(ctx: Ctx):
    from .cxi.core import compute_day
    db = SessionLocal()
    try:
        src = navtrace.source(db)
        days = list(ctx.days())
        for k, d in enumerate(days):
            res = compute_day(db, src, d, ctx.params, ctx.param_version,
                              progress=lambda f, m: ctx.progress((k + f) / len(days), m))
            ctx.rows_written += res["sector_hours"]
    finally:
        db.close()


@job("ews", "Early warning")
def run_ews(ctx: Ctx):
    from .ews.core import cells_day, coverage_day, score_day, store_cells
    db = SessionLocal()
    try:
        icaos = monitored(db)
        for d in ctx.days():
            for icao in icaos:
                coverage_day(db, icao, d, ctx.params)
                store_cells(db, icao, d, cells_day(db, icao, d, ctx.params))
                res = score_day(db, icao, d, ctx.params)
                if res.get("skipped"):
                    ctx.warn(res["skipped"])
                ctx.rows_written += res.get("cells", 0)
    finally:
        db.close()


@job("spi", "SPI calculation")
def run_spi(ctx: Ctx):
    from .spi.engine import approved, compute
    db = SessionLocal()
    try:
        d0 = ctx.d0.replace(day=1) if not ctx.options.get("exact") else ctx.d0
        codes = ctx.options.get("codes")
        spis = [s for s in approved(db) if not codes or s.code in codes]
        for k, s in enumerate(spis):
            ctx.progress(k / max(len(spis), 1), f"SPI {s.code}")
            ctx.rows_written += compute(db, s, d0, ctx.d1, ctx.params, alerts=not ctx.options.get("no_alerts"))
    finally:
        db.close()


def _late_days(ctx: Ctx) -> list:
    """Days before the run period with flights pushed to NavTrace since the last run (DR-001)."""
    from ..models import Watermark
    db = SessionLocal()
    try:
        wm = db.get(Watermark, "navtrace") or Watermark(key="navtrace", value=None)
        try:
            days, new = navtrace.source(db).changed_days(wm.value)
        except navtrace.NavTraceUnavailable:
            return []
        wm.value = new
        db.add(wm)
        db.commit()
        return [d for d in days if ctx.d0 - timedelta(days=30) <= d < ctx.d0]
    finally:
        db.close()


@job("daily", "Daily run (all modules)")
def run_daily(ctx: Ctx):
    steps = [("weather", run_weather), ("ga", run_ga), ("tdm", run_tdm), ("cxi", run_cxi), ("ews", run_ews), ("spi", run_spi)]
    skip = set(ctx.options.get("skip", []))
    late = [] if ctx.options.get("no_late") else _late_days(ctx)
    if late:
        ctx.warn(f"Re-processing {len(late)} earlier day(s) with late NavTrace data: {', '.join(map(str, late))}")
        d0, d1 = ctx.d0, ctx.d1
        for d in late:
            ctx.d0 = ctx.d1 = d
            for name, fn in (("ga", run_ga), ("tdm", run_tdm), ("ews", run_ews)):
                try:
                    fn(ctx)
                except Exception as e:  # noqa: BLE001
                    ctx.warn(f"late {name} {d}: {e}")
        ctx.d0, ctx.d1 = min(late), d1
        if "spi" not in skip:
            run_spi(ctx)
        ctx.d0, ctx.d1 = d0, d1
    for k, (name, fn) in enumerate(steps):
        if name in skip:
            continue
        ctx.progress(k / len(steps), f"Daily run: {name}")
        try:
            fn(ctx)
        except navtrace.NavTraceUnavailable as e:
            ctx.warn(f"{name}: {e}")
            if name in ("ga", "tdm", "cxi"):
                continue
            raise
        except ValueError as e:
            ctx.warn(f"{name}: {e}")


@job("monthly", "Monthly SRB report")
def run_monthly(ctx: Ctx):
    from ..reports.srb import build_srb
    db = SessionLocal()
    try:
        s = db.get(AppSetting, "srb_report_day")
        day = int((s.value or {}).get("day", 3)) if s else 3
        today = ctx.d1 + timedelta(days=1)
        if today.day != day and not ctx.options.get("force"):
            return
        month = ctx.options.get("month") or (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if db.query(Report).filter_by(kind="srb", period=month).filter(Report.status != "draft").first():
            ctx.warn(f"The SRB report for {month} is already in review or released")
            return
        build_srb(db, month, ctx.user)
    finally:
        db.close()
