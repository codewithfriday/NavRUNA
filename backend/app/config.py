"""NavRUNA configuration, read from environment variables (see .env.example).

Secrets (database passwords, BMKG and Telegram tokens, webhook keys) are only ever read from the environment
and are never stored in the database or shown in full in the interface (SRS SEC-014).
"""
import os
from pathlib import Path

APP_NAME = "NavRUNA"
APP_TITLE = "AirNav Runway Utilization and Network Analytics"
VERSION = "1.0.0"
DISCLAIMER = ("Results come from ADS-B analysis for safety management. They are not an operational record and "
              "must not be used for real-time air traffic control decisions.")


def env(name: str, default: str = "") -> str:
    return os.environ.get(f"NAVRUNA_{name}", default)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(env("DATA_DIR", str(BASE_DIR.parent.parent / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = env("DB_URL", f"sqlite:///{DATA_DIR / 'navruna.db'}")
SECRET_KEY = env("SECRET_KEY", "change-me-in-production-please-set-NAVRUNA_SECRET_KEY")
JWT_ALGORITHM = "HS256"

SESSION_IDLE_MINUTES = int(env("SESSION_IDLE_MINUTES", "30"))
MAX_FAILED_LOGINS = int(env("MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(env("LOCKOUT_MINUTES", "15"))
MIN_PASSWORD_LENGTH = 12

ADMIN_USERNAME = env("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", "ChangeMe!2026x")

# Demo mode: synthetic tracks, weather and sectors, so the platform can be tried without the AirNav network.
DEMO = env("DEMO", "0").lower() in ("1", "true", "yes")

# NavTrace ADS-B database (PostgreSQL + MobilityDB), libpq form, read-only account
NAVTRACE_DSN = env("NAVTRACE_DSN")
NAVTRACE_TABLE = env("NAVTRACE_TABLE", "navtrace.flight_trajectory")
NAVTRACE_MAX_CONNECTIONS = int(env("NAVTRACE_MAX_CONNECTIONS", "4"))

# NavEarth WFS
NAVEARTH_WFS = env("NAVEARTH_WFS", "https://app-pia.airnavindonesia.co.id/geoserver/enroute_chart/ows")

# BMKG aviation weather API
BMKG_URL = env("BMKG_URL", "https://web-aviation.bmkg.go.id/api/v1")
BMKG_TOKEN = env("BMKG_TOKEN")

# AirNav flight data API (optional, aircraft type)
FLIGHT_API_URL = env("FLIGHT_API_URL", "")
FLIGHT_API_TOKEN = env("FLIGHT_API_TOKEN")

# Alert channels (SRS FR-CORE-020)
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = int(env("SMTP_PORT", "587"))
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
SMTP_FROM = env("SMTP_FROM", "navruna@localhost")
SMTP_TLS = env("SMTP_TLS", "1") in ("1", "true", "yes")
ALERT_EMAILS = [e.strip() for e in env("ALERT_EMAILS").split(",") if e.strip()]
WEBHOOK_URL = env("WEBHOOK_URL")
WEBHOOK_SECRET = env("WEBHOOK_SECRET")
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env("TELEGRAM_CHAT_ID")
PUBLIC_URL = env("PUBLIC_URL", "http://localhost:8000")

# Scheduler (SRS FR-CORE-001): daily run time, UTC
SCHEDULER = env("SCHEDULER", "1") in ("1", "true", "yes")
DAILY_RUN_UTC = env("DAILY_RUN_UTC", "01:00")
WX_POLL_MINUTES = int(env("WX_POLL_MINUTES", "30"))

TILE_URL = env("TILE_URL", "")
STATIC_DIR = Path(env("STATIC_DIR", str(BASE_DIR.parent.parent / "frontend" / "dist")))


def data_path(*parts) -> Path:
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def masked(value: str) -> str:
    if not value:
        return ""
    return value[:2] + "…" + value[-2:] if len(value) > 6 else "…"
