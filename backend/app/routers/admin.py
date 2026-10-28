"""Administration: users, parameters, settings, connections, jobs and the audit log."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config
from ..core import alerts as alertcore
from ..core import jobs as jobcore
from ..core import navtrace
from ..db import get_db
from ..models import ROLES, ApiToken, AppSetting, AuditLog, JobRun, ParamSet, User, now
from ..params import REGISTRY, current, validate
from ..security import audit, check_password_policy, hash_password, require_role
from .auth import user_out
from .common import export, parse_day

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
admin_only = require_role("admin")


# ------------------------------------------------------------------ users
class UserIn(BaseModel):
    username: str
    full_name: str = ""
    email: str = ""
    role: str = "viewer"
    scope: list[str] = []
    password: str | None = None
    active: bool = True


@router.get("/users")
def users(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.query(User).order_by(User.username)]


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, f"Role must be one of {', '.join(ROLES)}")
    if db.query(User).filter_by(username=body.username.strip()).first():
        raise HTTPException(409, "Username already exists")
    check_password_policy(body.password or "")
    u = User(username=body.username.strip(), full_name=body.full_name, email=body.email, role=body.role,
             scope=body.scope, password_hash=hash_password(body.password), must_change_password=True, active=body.active)
    db.add(u)
    db.commit()
    audit(db, user, "user_created", "user", u.id, {"username": u.username, "role": u.role, "scope": u.scope})
    return user_out(u)


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    if body.role not in ROLES:
        raise HTTPException(400, "Unknown role")
    before = user_out(u)
    if u.id == user.id and (body.role != "admin" or not body.active):
        raise HTTPException(400, "You cannot remove your own administrator rights")
    u.full_name, u.email, u.role, u.scope, u.active = body.full_name, body.email, body.role, body.scope, body.active
    if body.password:
        check_password_policy(body.password)
        u.password_hash, u.must_change_password = hash_password(body.password), True
    u.locked_until, u.failed_attempts = (None, 0) if body.active else (u.locked_until, u.failed_attempts)
    if not body.active:
        for t in db.query(ApiToken).filter_by(user_id=u.id):
            t.revoked = True
    db.commit()
    audit(db, user, "user_updated", "user", u.id, {"before": before, "after": user_out(u), "password_reset": bool(body.password)})
    return user_out(u)


# ------------------------------------------------------------------ parameters (FR-CORE-010/011)
@router.get("/params")
def params(user: User = Depends(require_role("viewer")), db: Session = Depends(get_db)):
    vals, v = current(db)
    return {"version": v,
            "items": [{"key": k, "value": vals[k], "default": p.default, "unit": p.unit, "min": p.lo, "max": p.hi,
                       "group": p.group, "description": p.text} for k, p in REGISTRY.items()],
            "versions": [{"version": s.version, "author": s.author, "reason": s.reason, "active": s.active,
                          "created_at": s.created_at.isoformat()} for s in db.query(ParamSet).order_by(ParamSet.version.desc())]}


class ParamsIn(BaseModel):
    values: dict
    reason: str


@router.post("/params")
def new_params(body: ParamsIn, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    if not body.reason.strip():
        raise HTTPException(400, "Give a reason for the change")
    try:
        changes = validate(body.values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    vals, v = current(db)
    diff = {k: {"from": vals.get(k), "to": nv} for k, nv in changes.items() if vals.get(k) != nv}
    if not diff:
        raise HTTPException(400, "Nothing changed")
    for s in db.query(ParamSet).filter_by(active=True):
        s.active = False
    last = db.query(ParamSet).order_by(ParamSet.version.desc()).first()
    ps = ParamSet(version=(last.version if last else 0) + 1, values={**vals, **changes}, author=user.username,
                  reason=body.reason, active=True)
    db.add(ps)
    db.commit()
    audit(db, user, "params_changed", "params", ps.version, {"changes": diff, "reason": body.reason})
    return {"version": ps.version, "changes": diff}


@router.post("/params/{version}/activate")
def activate_params(version: int, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    ps = db.query(ParamSet).filter_by(version=version).first()
    if not ps:
        raise HTTPException(404, "Version not found")
    for s in db.query(ParamSet).filter_by(active=True):
        s.active = False
    ps.active = True
    db.commit()
    audit(db, user, "params_activated", "params", version)
    return {"ok": True}


@router.get("/params/{version}")
def param_version(version: int, user: User = Depends(require_role("viewer")), db: Session = Depends(get_db)):
    ps = db.query(ParamSet).filter_by(version=version).first()
    if not ps:
        raise HTTPException(404, "Version not found")
    return {"version": ps.version, "values": ps.values, "author": ps.author, "reason": ps.reason}


# ------------------------------------------------------------------ settings and connections
@router.get("/settings")
def settings(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    def get(k, d):
        s = db.get(AppSetting, k)
        return s.value if s else d
    return {"alert_routing": alertcore.routing(db), "channels": alertcore.channel_status(),
            "srb_report_day": get("srb_report_day", {"day": 3}), "srb_distribution": get("srb_distribution", {"emails": []}),
            "connections": {"navtrace": {"configured": bool(config.NAVTRACE_DSN), "table": config.NAVTRACE_TABLE, "demo": config.DEMO},
                            "bmkg": {"configured": bool(config.BMKG_TOKEN), "url": config.BMKG_URL, "token": config.masked(config.BMKG_TOKEN)},
                            "navearth": {"url": config.NAVEARTH_WFS},
                            "flight_api": {"configured": bool(config.FLIGHT_API_URL), "url": config.FLIGHT_API_URL}},
            "scheduler": {"enabled": config.SCHEDULER, "daily_utc": config.DAILY_RUN_UTC, "next": jobcore.next_runs()}}


class SettingIn(BaseModel):
    value: dict


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if key not in ("alert_routing", "srb_report_day", "srb_distribution"):
        raise HTTPException(400, "Unknown setting")
    s = db.get(AppSetting, key) or AppSetting(key=key)
    before = s.value
    s.value, s.updated_by = body.value, user.username
    db.add(s)
    db.commit()
    audit(db, user, "setting_changed", "setting", key, {"before": before, "after": body.value})
    return {"ok": True}


@router.post("/test/{what}")
def test_connection(what: str, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if what in alertcore.CHANNELS:
        return alertcore.test_channel(what)
    if what == "navtrace":
        try:
            return navtrace.source(db).status()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if what == "bmkg":
        from ..core.weather import _bmkg_get
        try:
            rows = _bmkg_get("/metar/WIII", {"from": f"{now():%Y-%m-%d} 00:00:00", "to": f"{now():%Y-%m-%d %H:%M:%S}"})
            return {"ok": True, "reports": len(rows), "sample": rows[0].get("data_text") if rows else None}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    if what == "navearth":
        from ..core.refdata import LAYERS, wfs_get
        out = {}
        for k in ("UTA", "TMA", "CTA"):
            try:
                out[k] = len(wfs_get(LAYERS[k]))
            except Exception as e:  # noqa: BLE001
                out[k] = f"error: {e}"
        return {"ok": all(isinstance(v, int) for v in out.values()), "layers": out}
    raise HTTPException(400, "Unknown connection")


# ------------------------------------------------------------------ jobs (FR-CORE-002/004)
def job_out(r: JobRun) -> dict:
    live = jobcore.LIVE.get(r.id, {}) if r.status == "running" else {}
    return {"id": r.id, "job": r.job, "title": jobcore.JOBS.get(r.job, (r.job,))[0], "from": str(r.period_from) if r.period_from else None,
            "to": str(r.period_to) if r.period_to else None, "status": "stopping" if r.id in jobcore.STOP else r.status, "attempt": r.attempt,
            "progress": live.get("progress", r.progress), "message": live.get("message", r.message),
            "rows_read": live.get("rows_read", r.rows_read), "rows_written": live.get("rows_written", r.rows_written), "warnings": r.warnings,
            "param_version": r.param_version, "triggered_by": r.triggered_by,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None, "created_at": r.created_at.isoformat()}


@router.get("/jobs")
def jobs(limit: int = 100, job: str | None = None, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    q = db.query(JobRun)
    if job:
        q = q.filter_by(job=job)
    return {"jobs": [job_out(r) for r in q.order_by(JobRun.id.desc()).limit(limit)],
            "available": [{"name": k, "title": v[0]} for k, v in jobcore.JOBS.items()], "next": jobcore.next_runs()}


class JobIn(BaseModel):
    job: str
    date_from: str
    date_to: str | None = None
    options: dict = {}


@router.post("/jobs")
def submit_job(body: JobIn, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    if body.job not in jobcore.JOBS:
        raise HTTPException(400, "Unknown job")
    d0 = parse_day(body.date_from)
    d1 = parse_day(body.date_to, d0)
    if (d1 - d0).days > 400:
        raise HTTPException(400, "At most 400 days per run; split longer re-processing")
    rid = jobcore.submit(body.job, d0, d1, user.username, body.options or None)
    audit(db, user, "job_started", "job", rid, {"job": body.job, "from": str(d0), "to": str(d1), "options": body.options})
    return job_out(db.get(JobRun, rid))


@router.post("/jobs/{rid}/stop")
def stop_job(rid: int, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    ok = jobcore.request_stop(rid)
    audit(db, user, "job_stop", "job", rid)
    return {"ok": ok}


@router.get("/jobs/{rid}")
def job_detail(rid: int, user: User = Depends(require_role("analyst")), db: Session = Depends(get_db)):
    r = db.get(JobRun, rid)
    if not r:
        raise HTTPException(404, "Job not found")
    return job_out(r)


# ------------------------------------------------------------------ audit (FR-CORE-032)
@router.get("/audit")
def audit_log(user_name: str | None = None, action: str | None = None, date_from: str | None = None, date_to: str | None = None,
              limit: int = Query(200, le=5000), fmt: str | None = None, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    q = db.query(AuditLog)
    if user_name:
        q = q.filter(AuditLog.username == user_name)
    if action:
        q = q.filter(AuditLog.action.contains(action))
    if date_from:
        q = q.filter(AuditLog.at >= parse_day(date_from))
    if date_to:
        from datetime import timedelta
        q = q.filter(AuditLog.at < parse_day(date_to) + timedelta(days=1))
    rows = [{"at": a.at.isoformat(timespec="seconds"), "user": a.username, "action": a.action, "object": a.obj_type,
             "id": a.obj_id, "detail": a.detail} for a in q.order_by(AuditLog.id.desc()).limit(limit)]
    if fmt in ("csv", "xlsx"):
        audit(db, user, "export", "audit", "", {"rows": len(rows)})
        return export(rows, fmt, "navruna_audit")
    return rows
