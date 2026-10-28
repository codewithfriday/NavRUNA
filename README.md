# NavRUNA

**AirNav Runway Utilization and Network Analytics** turns NavTrace ADS-B trajectories into safety performance indicators, alerts and monthly Safety Review Board reports for AirNav Indonesia's safety management system.

| Module | Purpose |
|---|---|
| Go-arounds | Every approach and go-around at the monitored runways, rates per runway, hour and operator, validation, risk factors and explanatory models (Data4Safety approach) |
| SPI register | Indicators defined as data, computed on schedule, alert levels (Poisson, ICAO Doc 9859, EWMA, fixed), four-eyes approval, drill-down |
| Route deviations | Nominal paths per route (HDBSCAN), deviations at critical points, before/after comparisons, hazard candidates |
| Complexity | Per sector and hour: traffic, EUROCONTROL PRU interactions, crossings, proximity, level and heading changes, index against each sector's baseline |
| Early warning | Isolation Forest on aerodrome × day × 4-hour block × operator cells, with a coverage check first and explanations |

NavRUNA is a safety analysis tool. It is not an air traffic control system and its results are not used for separation or real-time decisions.

The requirements are in the *AirNav Safety Analytics Platform Software Requirements Specification* (ISO/IEC/IEEE 29148). Installation and use are described in the [user manual](docs/USER_MANUAL.md) ([PDF](docs/NavRUNA_User_Manual.pdf)).

## Quick start

With Docker:

```
cp .env.example .env        # set NAVRUNA_SECRET_KEY, NAVRUNA_ADMIN_PASSWORD, NAVRUNA_NAVTRACE_DSN, NAVRUNA_BMKG_TOKEN …
docker compose up -d --build
```

Without Docker (Python 3.12+, Node.js 20+):

```
cp .env.example .env
./scripts/start.sh
```

Open http://localhost:8000 and sign in with the administrator account from `.env`. You are asked to set a new password first.

To try it without the AirNav network, set `NAVRUNA_DEMO=1` and leave `NAVRUNA_NAVTRACE_DSN` empty. Synthetic flights, weather and sectors are generated for WIII, WARR, WADD and WAAA. To fill six months of demo history:

```
cd backend
NAVRUNA_DEMO=1 .venv/bin/python -m app.tools.demo_history --from 2026-05-01 --to 2026-10-27 --cxi-days 21
```

## Data sources

| Source | Use |
|---|---|
| NavTrace (PostgreSQL + MobilityDB `navtrace.flight_trajectory`, read-only) | All tracks |
| Runway thresholds (`backend/app/seed/runway_thresholds.csv`, editable in the app) | Approach corridors, gates, wind components |
| NavEarth WFS (UTA, TMA, CTA, fixes, navaids) | Sectors and critical points |
| BMKG aviation weather API | METAR, SPECI, TAF (QNH, wind, thunderstorms) |

## Development

```
./scripts/dev.sh                       # API with reload on :8000, Vite on :5173
cd backend && .venv/bin/pytest -q      # tests
NAVRUNA_TEST_MOBILITYDB="host=… dbname=… user=…" .venv/bin/pytest -q tests/test_navtrace_db.py
python3 docs/tools/build_manual.py     # rebuild the manual PDF (needs playwright, markdown, pdftotext)
```

The REST API is documented at `/api/docs` (OpenAPI).

## Layout

```
backend/app/
  main.py            app, bootstrap, static interface
  config.py          NAVRUNA_* settings
  models.py          database tables
  params.py          parameter registry and versions
  security.py        sign-in, roles, tokens, audit
  core/              NavTrace access, geometry, reference data, weather, alerts, jobs, demo data
  modules/ga|spi|tdm|cxi|ews/   detection and analytics per module
  reports/srb.py     monthly SRB report (PDF)
  routers/           REST API
  tools/             command-line tools
backend/tests/       pytest suite
frontend/src/        React interface (pages, components)
docs/                user manual and screenshots
```

## Security

Secrets are read only from the environment, never stored in the database, and shown masked. Viewers never see callsigns, registrations or operator names. Every change and export is recorded in the audit log. Put NavRUNA behind HTTPS and set `NAVRUNA_REQUIRE_HTTPS=1`.
