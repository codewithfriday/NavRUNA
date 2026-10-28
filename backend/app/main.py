"""NavRUNA — AirNav Runway Utilization and Network Analytics. FastAPI entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .core import jobs as jobcore
from .core import refdata
from .db import Base, SessionLocal, engine
from .models import ParamSet, User
from .modules import jobs as _module_jobs  # noqa: F401  (registers jobs)
from .modules.ga import jobs as _ga_jobs  # noqa: F401
from .modules.spi.engine import seed_defaults
from .params import defaults
from .routers import admin, alerts, auth, cxi, ews, ga, refs, reports, spi, tdm
from .security import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def bootstrap():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if not db.query(User).count():
            db.add(User(username=config.ADMIN_USERNAME, full_name="Administrator", role="admin",
                        password_hash=hash_password(config.ADMIN_PASSWORD), must_change_password=True))
        if not db.query(ParamSet).count():
            db.add(ParamSet(version=1, values=defaults(), author="system", reason="Defaults from the SRS", active=True))
        db.commit()
        refdata.seed_airlines(db)
        refdata.seed_runways(db)
        if config.DEMO:
            refdata.seed_demo_reference(db)
        seed_defaults(db)
    finally:
        db.close()
    jobcore.recover_interrupted()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    jobcore.start_scheduler()
    yield
    jobcore.stop_scheduler()


app = FastAPI(title=f"{config.APP_NAME} — {config.APP_TITLE}", version=config.VERSION, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json",
              description="REST API of NavRUNA (OpenAPI 3.1). Authenticate with a session token from /api/v1/auth/login "
                          "or a personal access token (Bearer). " + config.DISCLAIMER)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
for r in (auth.router, admin.router, refs.router, ga.router, spi.router, alerts.router, tdm.router, cxi.router,
          ews.router, reports.router):
    app.include_router(r)


@app.middleware("http")
async def https_only(request: Request, call_next):
    # SEC-011: behind a TLS proxy, refuse requests that arrived over plain HTTP
    if config.env("REQUIRE_HTTPS", "0") == "1" and request.headers.get("x-forwarded-proto", "https") != "https":
        return JSONResponse(status_code=403, content={"detail": "HTTPS required"})
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.get("/api/health")
def health():
    return {"ok": True, "app": config.APP_NAME, "version": config.VERSION, "demo": config.DEMO}


@app.get("/api/config")
def client_config():
    return {"app": config.APP_NAME, "title": config.APP_TITLE, "version": config.VERSION, "demo": config.DEMO,
            "tile_url": config.TILE_URL, "idle_minutes": config.SESSION_IDLE_MINUTES, "disclaimer": config.DISCLAIMER}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.getLogger("navruna").exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": f"Server error: {exc.__class__.__name__}: {exc}"})


if config.STATIC_DIR.exists() and (config.STATIC_DIR / "index.html").exists():
    if (config.STATIC_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = config.STATIC_DIR / path
        if path and not path.startswith("api/") and f.is_file():
            return FileResponse(f)
        return FileResponse(config.STATIC_DIR / "index.html")
