"""Scheduled and manual jobs (FR-CORE-001..006).

Jobs run one at a time on a single worker thread, which keeps SQLite writes serial. Each run is recorded
(JobRun) with progress, rows, warnings and the parameter version; a failed run is retried twice and then
reported through the alert channels. Jobs are idempotent: they replace their own results for the period.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .. import config
from ..db import SessionLocal
from ..models import JobRun, now, utc_now

log = logging.getLogger("navruna.jobs")


@dataclass
class Ctx:
    run_id: int
    d0: date | None
    d1: date | None
    params: dict
    param_version: int
    user: str
    options: dict = field(default_factory=dict)
    rows_read: int = 0
    rows_written: int = 0
    warnings: list = field(default_factory=list)

    def days(self):
        d = self.d0
        while d and d <= self.d1:
            yield d
            d += timedelta(days=1)

    def progress(self, frac: float, message: str = ""):
        """Progress is kept in memory (no database write while the job holds its own transaction)."""
        live = LIVE.setdefault(self.run_id, {})
        live["progress"] = round(max(0.0, min(1.0, frac)), 3)
        if message:
            live["message"] = message[:500]
        live["rows_read"], live["rows_written"] = self.rows_read, self.rows_written
        if self.run_id in STOP:
            raise StopRequested()

    def warn(self, text: str):
        if text not in self.warnings:
            self.warnings.append(text)


class StopRequested(Exception):
    pass


JOBS: dict[str, tuple[str, Callable[[Ctx], None]]] = {}
LIVE: dict[int, dict] = {}     # progress of running jobs
STOP: set[int] = set()         # stop requests


def job(name: str, title: str):
    def deco(fn):
        JOBS[name] = (title, fn)
        return fn
    return deco


_q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def submit(name: str, d0: date | None = None, d1: date | None = None, user: str = "scheduler",
           options: dict | None = None, attempt: int = 1) -> int:
    if name not in JOBS:
        raise ValueError(f"Unknown job {name}")
    from ..params import current
    db = SessionLocal()
    try:
        _, pv = current(db)
        r = JobRun(job=name, period_from=d0, period_to=d1 or d0, status="queued", triggered_by=user,
                   param_version=pv, attempt=attempt, warnings=[], message=str(options or "")[:200] if options else "")
        db.add(r)
        db.commit()
        rid = r.id
        if options:
            _options[rid] = options
    finally:
        db.close()
    _ensure_worker()
    _q.put(rid)
    return rid


_options: dict[int, dict] = {}


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="navruna-jobs", daemon=True)
            _worker.start()


def _loop():
    while True:
        rid = _q.get()
        try:
            run_now(rid)
        except Exception:  # noqa: BLE001
            log.exception("job %s crashed", rid)


def run_now(rid: int):
    """Run a queued job in the current thread (the worker, or tests)."""
    from ..params import current
    db = SessionLocal()
    r = db.get(JobRun, rid)
    if r is None or r.status not in ("queued",):
        db.close()
        return
    title, fn = JOBS[r.job]
    params, pv = current(db)
    r.status, r.started_at, r.param_version = "running", now(), pv
    db.commit()
    ctx = Ctx(run_id=rid, d0=r.period_from, d1=r.period_to, params=params, param_version=pv, user=r.triggered_by,
              options=_options.pop(rid, {}))
    name, attempt, d0, d1, user = r.job, r.attempt, r.period_from, r.period_to, r.triggered_by
    db.close()
    status, message = "done", ""
    LIVE[rid] = {"progress": 0.0}
    try:
        fn(ctx)
        message = f"{title}: done"
    except StopRequested:
        status, message = "stopped", "Stopped by user"
    except Exception as e:  # noqa: BLE001
        status, message = "error", f"{type(e).__name__}: {e}"
        log.error("job %s failed: %s\n%s", name, e, traceback.format_exc())
    live = LIVE.pop(rid, {})
    STOP.discard(rid)
    db = SessionLocal()
    try:
        r = db.get(JobRun, rid)
        r.status, r.ended_at, r.progress = status, now(), 1.0 if status == "done" else live.get("progress", 0.0)
        r.message = live.get("message", "")
        r.message = message[:1000]
        r.rows_read, r.rows_written, r.warnings = ctx.rows_read, ctx.rows_written, ctx.warnings[-50:]
        db.commit()
        if status == "error":
            if attempt < 3:
                submit(name, d0, d1, user, ctx.options, attempt=attempt + 1)     # FR-CORE-005
            else:
                from .alerts import raise_alert
                raise_alert(db, "JOB", name, 2, f"Job {name} failed three times", message,
                            f"Processing job '{name}' failed three times; see the job history", link="/admin/jobs")
    finally:
        db.close()


def request_stop(rid: int) -> bool:
    db = SessionLocal()
    try:
        r = db.get(JobRun, rid)
        if r and r.status == "queued":
            r.status = "stopped"
            db.commit()
            return True
        if r and r.status == "running":
            STOP.add(rid)
            return True
        return False
    finally:
        db.close()


def run_sync(name: str, d0: date | None = None, d1: date | None = None, user: str = "cli", options: dict | None = None) -> int:
    """Create and run a job in the calling thread (command line and tests)."""
    from ..params import current
    db = SessionLocal()
    try:
        _, pv = current(db)
        r = JobRun(job=name, period_from=d0, period_to=d1 or d0, status="queued", triggered_by=user, param_version=pv, warnings=[])
        db.add(r)
        db.commit()
        rid = r.id
    finally:
        db.close()
    if options:
        _options[rid] = options
    run_now(rid)
    return rid


def recover_interrupted():
    db = SessionLocal()
    try:
        for r in db.query(JobRun).filter(JobRun.status.in_(["queued", "running", "stopping"])).all():
            r.status, r.message = "error", "Interrupted by a server restart"
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------ scheduler
_scheduler = None


def start_scheduler():
    global _scheduler
    if not config.SCHEDULER or _scheduler is not None:
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    hh, mm = (int(x) for x in config.DAILY_RUN_UTC.split(":"))
    s = BackgroundScheduler(timezone="UTC")
    yesterday = lambda: utc_now().date() - timedelta(days=1)
    s.add_job(lambda: submit("daily", yesterday(), yesterday()), "cron", hour=hh, minute=mm, id="daily")
    s.add_job(lambda: submit("weather", utc_now().date(), utc_now().date()), "interval",
              minutes=config.WX_POLL_MINUTES, id="weather")
    s.add_job(lambda: submit("ga_recent", utc_now().date(), utc_now().date()), "interval",
              minutes=15, id="ga_recent")
    s.add_job(lambda: submit("monthly", yesterday(), yesterday()), "cron", hour=hh + 1 if hh < 23 else 0, minute=mm, id="monthly")
    s.start()
    _scheduler = s


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_runs() -> list[dict]:
    if _scheduler is None:
        return []
    return [{"id": j.id, "next": j.next_run_time.isoformat() if j.next_run_time else None} for j in _scheduler.get_jobs()]
