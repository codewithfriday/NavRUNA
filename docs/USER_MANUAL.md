# 1. About NavRUNA

NavRUNA (AirNav Runway Utilization and Network Analytics) turns NavTrace ADS-B trajectories into safety performance indicators (SPIs), alerts and reports for AirNav Indonesia's safety management system. It has five modules sharing one core:

| Module | What it does |
|---|---|
| **Go-arounds (GA)** | Finds every approach to the monitored runways and every go-around; rates per runway, hour and operator; alerts when a rate rises; explains which conditions raise the risk |
| **SPI register (SPI)** | One register of all indicators, defined as data, computed on schedule, tested against alert levels and targets, reported monthly to the Safety Review Board (SRB) |
| **Route deviations (TDM)** | Learns the nominal paths of each route and measures every day's flights against them at critical points |
| **Complexity (CXI)** | Traffic, interactions, crossings, level and heading changes per sector and hour, combined into an index |
| **Early warning (EWS)** | Flags aerodrome-day-block-operator combinations that behave unlike their own history |

**Intended use.** NavRUNA is a safety analysis tool for the SMS. It is not an air traffic control system: it gives no instructions to controllers or pilots, and its results are not used for separation or real-time decisions. Reports, exports and alerts carry this statement.

**Standards.** The software follows the AirNav Safety Analytics Platform SRS, written to ISO/IEC/IEEE 29148. Quality requirements follow ISO/IEC 25010, testing ISO/IEC/IEEE 29119, security ISO/IEC 27001 and OWASP ASVS level 2. Safety performance concepts come from ICAO Annex 19 and Doc 9859, and the protection of safety data from Annex 19 and Indonesia's personal data protection law (UU 27/2022).

## 1.1 Data sources

| Source | Used for | Needed? |
|---|---|---|
| NavTrace (PostgreSQL + MobilityDB, read-only) | All tracks | Yes |
| Runway threshold file | Approach corridors, gates, wind components | Yes (bundled; editable in the app) |
| NavEarth WFS | Sectors (UTA, TMA, CTA), fixes and navaids | Yes for complexity and route deviations |
| BMKG aviation weather API | METAR, SPECI (QNH, wind, thunderstorms), TAF | Strongly recommended; without it QNH 1013.25 hPa is assumed and weather fields stay empty |
| AirNav flight data API | Aircraft type | Optional |

NavRUNA never writes to NavTrace or NavEarth.

## 1.2 Roles

| Role | Can |
|---|---|
| Viewer | See dashboards, maps and released reports, possibly limited to some aerodromes or units. Never sees callsigns, registrations or operator names (operators appear as "Operator A, B…") |
| Reviewer | Everything a viewer can, with identifiers; approve SPI changes and models; release reports |
| Analyst | Everything a reviewer can, plus run jobs, validate go-arounds, label early-warning alerts, edit SPI drafts, parameters and reference data |
| Administrator | Everything, plus users, connections, alert routing and the audit log |

# 2. Installation

## 2.1 Requirements

- One Linux server inside the AirNav network: 8 vCPU, 32 GB RAM and 500 GB disk are recommended for 10 monitored aerodromes and a year of history.
- Either Docker (with Compose), or Python 3.12+ and Node.js 20+.
- Network access from the server to NavTrace (PostgreSQL port), NavEarth (HTTPS), BMKG (HTTPS) and, if used, the SMTP server and the Telegram Bot API.
- A read-only NavTrace database account (section 2.6), a BMKG API token (request it from ksdip@bmkg.go.id) and, for e-mail alerts, an SMTP account.

## 2.2 Getting the software

```
git clone https://github.com/codewithfriday/NavRUNA.git
cd NavRUNA
cp .env.example .env
```

## 2.3 Configuration (.env)

All settings are environment variables in `.env`. Secrets (database password, BMKG token, Telegram token, webhook key) are only ever read from there: they are never stored in the database and the interface shows them masked. Put values that contain spaces or a `$` in single quotes.

| Variable | Purpose |
|---|---|
| `NAVRUNA_SECRET_KEY` | Signs sign-in sessions. Set a random string of at least 32 characters |
| `NAVRUNA_ADMIN_USERNAME` / `NAVRUNA_ADMIN_PASSWORD` | First administrator, created on first start only; the password must be changed at first sign-in |
| `NAVRUNA_DATA_DIR` | Database, models, reports (default `./data`; `/data` in Docker) |
| `NAVRUNA_DB_URL` | Leave empty for SQLite (write-ahead-log mode). For PostgreSQL: `postgresql+psycopg://user:pass@host/navruna` |
| `NAVRUNA_PUBLIC_URL` | Address of NavRUNA, used in alert links |
| `NAVRUNA_NAVTRACE_DSN` | NavTrace connection, for example `'host=172.20.25.30 port=25432 dbname=navtrace_flight user=navruna_ro password=…'` |
| `NAVRUNA_NAVTRACE_TABLE` | Default `navtrace.flight_trajectory` |
| `NAVRUNA_NAVEARTH_WFS` | NavEarth WFS base address |
| `NAVRUNA_BMKG_TOKEN` | BMKG API token |
| `NAVRUNA_SMTP_*`, `NAVRUNA_ALERT_EMAILS` | E-mail alerts |
| `NAVRUNA_WEBHOOK_URL`, `NAVRUNA_WEBHOOK_SECRET` | Webhook alerts, signed with HMAC-SHA256 when a secret is set |
| `NAVRUNA_TELEGRAM_BOT_TOKEN`, `NAVRUNA_TELEGRAM_CHAT_ID` | Telegram alerts |
| `NAVRUNA_SCHEDULER`, `NAVRUNA_DAILY_RUN_UTC` | Scheduler on/off and the daily run time (default 01:00 UTC) |
| `NAVRUNA_TILE_URL` | Online base map; leave empty on air-gapped servers (a built-in coastline is used) |
| `NAVRUNA_DEMO` | `1` = synthetic data, to try NavRUNA without the AirNav network |
| `NAVRUNA_REQUIRE_HTTPS` | `1` behind a TLS proxy: refuse requests that arrived over plain HTTP |

## 2.4 Installing with Docker

```
docker compose up -d --build
```

NavRUNA listens on port 8000. Data is kept in the `navruna-data` volume. After an update, run the same command again: `--build` is needed so the image includes the new interface.

Put NavRUNA behind the AirNav HTTPS reverse proxy (nginx, Apache or the load balancer) and set `NAVRUNA_REQUIRE_HTTPS=1`.

## 2.5 Installing without Docker

```
./scripts/start.sh
```

The script creates a Python virtual environment in `backend/.venv`, installs the packages, builds the interface when its sources changed, loads `.env` and starts the server on port 8000. Run it under systemd or another service manager in production. Always use a single worker: the scheduler and jobs run inside the server process.

On Windows, use Docker Desktop, or run the same steps by hand: `python -m venv backend\.venv`, `backend\.venv\Scripts\pip install -r backend\requirements.txt`, `cd frontend && npm install && npm run build`, then `cd backend && .venv\Scripts\uvicorn app.main:app --port 8000`.

## 2.6 Read-only NavTrace account

Ask the NavTrace database administrator to run (with a strong password):

```
CREATE ROLE navruna_ro LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE navtrace_flight TO navruna_ro;
GRANT USAGE ON SCHEMA navtrace TO navruna_ro;
GRANT SELECT ON navtrace.flight_trajectory TO navruna_ro;
ALTER ROLE navruna_ro SET default_transaction_read_only = on;
```

The table must have the columns `gufi, callsign, icao24, registration, dof, adep, ades, first_seen, last_seen, track (tgeompoint), alt_baro (tfloat, ft), gs (tfloat, kt), pushed_at`. NavRUNA uses at most four connections at a time.

## 2.7 First start and first sign-in

Open `http://<server>:8000` and sign in as the administrator from `.env`. You are asked for a new password first: at least 12 characters, mixing three of lower case, upper case, digits and symbols.

![Sign-in](img/01_login.png)

Accounts lock for 15 minutes after five failed attempts. A session ends after 30 minutes without activity.

## 2.8 Trying NavRUNA in demo mode

Set `NAVRUNA_DEMO=1` (and leave `NAVRUNA_NAVTRACE_DSN` empty) to run with synthetic flights, weather and sectors at WIII, WARR, WADD and WAAA. To fill a demo installation with six months of history (roughly 30–40 seconds per simulated day, so about two hours):

```
cd backend
NAVRUNA_DEMO=1 .venv/bin/python -m app.tools.demo_history --from 2026-05-01 --to 2026-10-27 --cxi-days 21
```

Every page then shows a **DEMO DATA** flag. Never use demo results for safety decisions.

## 2.9 Updating, backup and restore

- **Update:** `git pull`, then `docker compose up -d --build` or `./scripts/start.sh`. The database is upgraded automatically at start.
- **Backup:** copy the data folder (SQLite: `navruna.db`, plus `models/` and `reports/`) daily and keep 30 days. With SQLite, use `sqlite3 navruna.db ".backup backup.db"` so a running server does not leave a half-written copy. With PostgreSQL, use `pg_dump`.
- **Restore:** stop NavRUNA, put the files back, start it. Test a restore every quarter.
- **Logs:** the server writes to standard output (`docker compose logs navruna`); every job run is listed under **Jobs**, and every change under **Audit log**.

# 3. First-time setup

Do these steps once, as administrator, in this order.

## 3.1 Reference data

Open **Reference data**.

1. **Aerodromes.** The runway threshold file supplied by AirNav is already loaded (516 runway ends at 251 aerodromes). Tick **Monitored** for each aerodrome NavRUNA should process; WIII, WSSS, WADD, WARR and WMKK are ticked by default. Check each aerodrome's UTC offset (WIB +7, WITA +8, WIT +9), used for local hours, and enter its approach unit, used to limit viewers.
2. **Runways.** Tick **only with issues** to see runway ends NavRUNA could not complete: a missing bearing it could not compute (for example WIDB, which has one end listed), or a threshold pair whose distance differs from the published length by more than 5 %. Click a row to correct it. To replace the whole file, use **Import CSV** with the same columns as the export.
3. **Sectors.** Click **Refresh from NavEarth**. NavRUNA loads the UTA, TMA and CTA polygons with their limits and the unit from `unt_srvc`, plus the fixes and navaids used as critical points. Where a published limit has several values (for example "FL245/FL370" or "10000 ft/12000 ft/FL150*"), the sector is marked **set override**: click it and enter the floor or ceiling to use. Enter declared capacities (flights per hour) if you want sector utilisation.
4. **Airlines.** The ICAO airline designators decide the operator of a flight from its callsign. Add designators that are missing.

![Reference data: aerodromes](img/22_refs.png)

## 3.2 Connections and alerts

Open **Connections**. Test NavTrace, BMKG and NavEarth, and send a test message on each alert channel. Then choose, for each alert type, which channels receive it. Every alert also appears in the alert list.

![Connections and alert routing](img/25_settings.png)

If the BMKG test fails with "a web page instead of data", BMKG's website protection is blocking the server: ask BMKG to exempt API calls carrying a valid token, or to allow the server's address.

## 3.3 Users

Open **Users** and create accounts with the least role each person needs (section 1.2). Viewers can be limited to aerodromes or units (comma-separated, for example `WIII, WARR`). New users must change their password at first sign-in. Switching an account off also revokes its access tokens.

![Users](img/26_users.png)

## 3.4 Parameters

Every threshold (corridor size, climb trigger, alert levels, clustering, complexity weights, early-warning limits) is a parameter with a default, unit and allowed range. Review them under **Parameters** before loading history. A change creates a new version; results record the version they were computed with.

![Parameters](img/23_params.png)

## 3.5 Loading history

Open **Jobs**, choose **Daily run (all modules)** and a period (at most 400 days per run; start with the last 12 months), and click **Start**. The daily run fetches weather, detects go-arounds, measures route deviations, computes complexity and early warning, and then the SPIs. For long periods, run the modules separately in this order: Weather, Go-around detection, Trajectory deviation monitor, Complexity index, Early warning, SPI calculation.

From then on the scheduler runs everything automatically (section 11.3).

## 3.6 SPI register review

The register starts with 16 SPIs from the SRS (Appendix B), marked as approved by "system". The Safety Review Board should review their owners, alert methods and targets, and approve changes through the normal draft-and-approve process (section 6.4).

# 4. Dashboard and alerts

## 4.1 Dashboard

The dashboard shows one card per approved SPI: its latest monthly value, a 12-month sparkline with alert periods marked, and the alert state as words and colour. Above the cards are the number of SPIs above an alert level, open alerts, approaches and go-arounds of the last 30 days, and go-arounds waiting for review. Click a card for the SPI's detail.

![Dashboard](img/02_dashboard.png)

## 4.2 Alerts

Alerts come from SPI alert levels, route changes, early warning, coverage anomalies and failed jobs. The same alert is not sent again while its level is unchanged, until the quiet period (24 hours by default) has passed. Click **Acknowledge** and say what was done, or why no action is needed; a closed alert reopens if its level rises again.

![Alerts](img/12_alerts.png)

Alerts sent outside NavRUNA (e-mail, webhook, Telegram) carry only aggregated information: no callsigns, registrations or operator names.

# 5. Go-arounds

## 5.1 How a go-around is found

For each runway end, NavRUNA builds a final approach corridor along the extended centreline, from the threshold out to 8 NM: 0.5 NM wide on each side at the threshold, growing by 0.1 NM per NM, up to 3,000 ft above the threshold. Pressure altitude from ADS-B is converted to height above the threshold with the QNH of the nearest METAR or SPECI.

- **Approach:** at least 3 consecutive points in the corridor, with the ground track within 30° of the runway bearing, after losing at least 300 ft in the previous 2 minutes. Every approach is stored, so rates have the right denominator.
- **Go-around:** inside the corridor and before any landing, the height rises at least 300 ft above the lowest point, and the climb rate stays above 500 ft/min for 20 s (the Data4Safety trigger pattern: a brief bump in a noisy track does not count).
- **Landed:** below 50 ft and slower than 80 kt, or the track ends within 1 NM of the threshold below 200 ft.
- **Not counted:** touch-and-goes and low approaches (below 50 ft over the runway before climbing), departures, and flights slower than 60 kt (rotorcraft).

Each go-around gets a height band (above 1,000 ft, 500–1,000 ft, below 500 ft), what happened next (landed on the same or another runway, diverted, unknown), the delay and extra distance to the landing, and a repeated flag when a flight goes around twice or more. Every approach also records lateral offset, glide-path deviation, ground speed and vertical rate at the 6, 4 and 2 NM gates and at 1,000 and 500 ft, the time to the preceding arrival, the time since the last departure, and the weather. All values are parameters.

**Coverage.** Where fewer than 80 % of a runway's approaches on a day reach below 1,000 ft in the ADS-B data, that runway-day is marked incomplete and left out of rates and alerts.

## 5.2 Map

**Go-arounds → Map** shows where go-arounds started, coloured by height band, for the last 24 hours, 3 days or 7 days; it refreshes every 15 minutes. Choose an aerodrome to see its final corridors. Click a point to replay the flight.

![Go-around map](img/03_ga_map.png)

## 5.3 Trend

The **Trend** tab shows the weekly or monthly go-around rate per 1,000 approaches, the 90-day baseline, and the approaches per period. A point coloured amber, orange or red is above alert level 1, 2 or 3: the period's count is improbably high for a Poisson count at the baseline rate (p below 0.023, the equivalent of 2σ, for level 1). No alert is raised with fewer than 200 approaches in the baseline or 20 in the period. Filter by aerodrome, runway, operator (reviewers and up) and local hours.

![Go-around trend](img/04_ga_trend.png)

## 5.4 Events

The **Events** tab lists go-arounds with their time (UTC and local), runway, callsign, operator, height band, what happened next, the weather and the validation status. Export it to CSV or Excel. Click a row to open the replay.

![Go-around events](img/05_ga_events.png)

## 5.5 Replay and validation

The replay screen shows the track coloured by height, the extended centreline with the gates, a player (play, pause, speed), the vertical profile against a 3° glide path, ground speed and vertical rate, the gate values, the preceding arrival and the METAR or SPECI in force.

![Replay of a go-around](img/06_replay.png)

Analysts confirm or reject each go-around offered for review. Rejecting needs a reason. For the first three months every go-around is offered for review; after that a 20 % sample, plus every go-around below 500 ft and every repeated one. Re-processing a day keeps the validations.

## 5.6 Heat table

The **Heat table** tab shows the rate by local hour × runway end, or by operator × month. Darker cells have higher rates; hover a cell for its counts.

![Heat table](img/07_ga_heat.png)

## 5.7 Risk factors and models

The **Risk factors** tab shows the go-around rate by condition, with 95 % intervals: thunderstorm reported or not, tailwind, crosswind, time to the preceding arrival, season, and glide-path deviation at 4 NM.

![Risk factors](img/08_ga_factors.png)

Once at least 300 go-arounds are confirmed (smaller aerodromes are pooled), an analyst can **Train models**. NavRUNA fits gradient-boosted trees with monotonic constraints, and a logistic regression for comparison, at the 6, 4 and 2 NM gates. It trains on earlier months and tests on later ones, and reports PR-AUC, precision, recall and calibration. Each model comes with a model card (ISO/IEC 23894). A reviewer must approve the card before the model is used. The models explain go-around risk after the event; they are not a warning tool for controllers.

## 5.8 Validation statistics and missed go-arounds

The **Validation** tab shows precision (confirmed ÷ reviewed) and recall per aerodrome and month, and the low-level coverage per runway and day. If the tower log or an occurrence report shows a go-around NavRUNA missed, add it here: it is stored as confirmed and counts toward recall.

![Validation and coverage](img/09_ga_validation.png)

# 6. SPI register

## 6.1 Register

**SPI register** lists every SPI with its calculation, periods, alert method, latest value, state and target. Switch between approved, draft, retired and all.

![SPI register](img/10_spis.png)

## 6.2 SPI detail

The detail page shows the trend with the baseline, alert levels and target. Click a point to list the go-arounds or flights behind it. Choose a breakdown (for example aerodrome × runway) to compare values, then click a row to show that breakdown's trend. Below are the definition and its versions.

![SPI detail](img/11_spi_detail.png)

## 6.3 Alert methods

| Method | Use for | Rule |
|---|---|---|
| Poisson | Counts and rates of rare events (go-arounds) | Alert when the period's count is improbably high at the baseline rate: level 1 p < 0.023, level 2 p < 0.0023, level 3 p < 0.00023 |
| ICAO Doc 9859 | SPIs with steady values | Alert levels at the mean + 1, 2 and 3 standard deviations of the preceding 12 periods; alert on one point above level 3, two consecutive above level 2, or three consecutive above level 1 |
| EWMA | Slow drifts | Exponentially weighted average (λ 0.2) beyond 2.7σ |
| Fixed | Agreed limits | Values above the levels you enter |

## 6.4 Creating or changing an SPI

Click **New SPI**, or **New version** on an SPI's page. Choose a registered calculation (NavRUNA never runs free code), the unit and scale, periods, breakdowns, alert method, minimum denominator, target and owner. Save it as a draft. A reviewer other than the author must approve it (**Review draft**). Approving retires the previous version and recomputes the new one over the chosen history (24 months by default); earlier observations are kept.

To record a value from outside NavRUNA (for example an occurrence count), use **Enter a value** and note its source.

# 7. Route deviations

## 7.1 Routes and nominal paths

**Route deviations → Routes** lists the monitored routes. **Busiest pairs in NavTrace** proposes the 50 busiest aerodrome pairs of the last 90 days. For each route, NavRUNA resamples every flight of the last 90 days to 100 points and clusters them with HDBSCAN. Each cluster becomes a nominal path: a medoid trajectory, a 5–95 % lateral corridor and a vertical profile. Paths are rebuilt monthly, and an alert is raised when a new path appears or a path's share changes by more than 10 percentage points.

![Routes](img/13_tdm_routes.png)

## 7.2 Route map

The route page shows the nominal paths with their corridors, the critical points and one day's flights. Flights are coloured by path, red when they deviated beyond the limit, and grey dashed when they followed no path. Use **Nominal paths** to rename or retire a path.

![Route map](img/14_tdm_route.png)

## 7.3 Critical points

Critical points are the NavEarth fixes and navaids within 5 NM of a nominal path, plus TMA boundary crossings. At each one, NavRUNA records every flight's lateral offset and its altitude difference from the median profile. A deviation is flagged above 2 NM en route (RNP 2), 1 NM inside a TMA (RNP 1), or 1,000 ft vertically. Click a point to see its histograms; switch off points that do not matter.

![Critical points](img/15_tdm_points.png)

## 7.4 Before and after a change

For an AIRAC date or a route change, enter the date: NavRUNA compares path use and the lateral offsets at each critical point before and after it, with a Kolmogorov–Smirnov test.

## 7.5 Hazard candidates and hot spots

**Hazard candidates** lists critical points where at least 5 % of flights deviated in the period, with the pattern (left or right, above or below) and example flights. Export it for the SRM unit's hazard identification. **Hot spots** maps the same information on a 5 NM grid.

![Hazard candidates](img/16_tdm_hazards.png)

# 8. Complexity

For each sector and UTC hour NavRUNA counts:

- entries, peak count and occupancy;
- interaction time (two aircraft in the same 20 NM × 20 NM × 3,000 ft cell) and its vertical, horizontal and speed parts, following the EUROCONTROL Performance Review Unit method;
- crossing pairs (tracks crossing at more than 30° within 10 minutes and 2,000 ft);
- proximity events (two aircraft within 10 NM and 2,000 ft; a complexity measure, not a separation assessment);
- level changes of 1,000 ft or more, and heading changes of 15° or more.

The **complexity index** is the weighted mean of these measures, standardised against the sector's own 90-day distribution: 0 is a usual hour for that sector, 2 is two standard deviations busier. Sectors are the UTA sectors (ACC) and, below FL245, the TMAs and CTAs (APP). Traffic outside any sector is counted on a separate line.

![Complexity heat table](img/17_cxi_heat.png)

The **Map by hour** tab shades each sector by its index for a chosen hour. Click a sector for its daily trend, its busiest hours and, for reviewers, the flights in a given hour.

![Complexity map](img/18_cxi_map.png)

# 9. Early warning

Early warning looks for unusual combinations before any single SPI crosses an alert level. Each cell is one aerodrome × day × 4-hour block (UTC) × operator, for the 10 busiest operators, with smaller operators grouped. Its features are arrivals, departures, go-around rate, low go-arounds, median time to leader, unstable-gate share, extra track distance inside 40 NM, holding circles, route deviation share, TMA complexity and weather.

1. **Coverage first.** If the flights seen, the share of flights with gaps, or the share of arrivals reaching 1,000 ft is far from its usual value (3 median absolute deviations), the day is a coverage anomaly. It is reported to administrators and not scored, because missing ADS-B data looks like an unusual day.
2. **Scoring.** An Isolation Forest per aerodrome, trained on the last 180 days (at least 90) and retrained weekly, scores each cell from 0 to 1.
3. **Alert.** A cell above the 99th percentile, with at least two features more than 3 MADs from their usual value, raises an alert.

![Early warning anomalies](img/19_ews.png)

Click an anomaly to see the features furthest from their usual value, the go-arounds in the cell and the coverage that day. Analysts label each alert as a real safety concern, a known cause (weather, runway closure, NOTAM, data issue) or a false alarm. The labels give precision and guide the threshold.

![Early warning cell](img/20_ews_cell.png)

# 10. Reports

On the 3rd of each month (configurable), NavRUNA drafts the Safety Review Board report for the previous month as a PDF. It contains:

- a summary table of all SPIs against their alert levels and targets;
- 12-month trend charts;
- the alerts raised and the actions recorded;
- highlights per module;
- data completeness;
- a sign-off page.

It shows aggregated data only. Analysts can also draft a report for any month with **Draft SRB report**.

The workflow is **Draft → In review → Released**. An analyst sends the draft for review. A reviewer other than the author releases it, or returns it to draft. A released report is frozen and e-mailed to the distribution list set under **Connections**. Viewers see released reports only.

![Reports](img/21_reports.png)

# 11. Administration

## 11.1 Parameters

See section 3.4. To apply a parameter change to past data, re-run the affected jobs for that period. **Make active** on an older version restores it.

## 11.2 Jobs

**Jobs** lists every run with its period, status, progress, rows written, warnings, parameter version and who started it. Start any job for a period to re-process it: jobs replace their own results, so running one twice is safe. Running jobs can be stopped. A failed job is retried twice, then raises an alert.

![Jobs](img/24_jobs.png)

## 11.3 Schedule

| When (UTC) | What |
|---|---|
| Every 30 minutes | Weather download for the monitored aerodromes |
| Every 15 minutes | Go-arounds of today so far (for the live map) |
| Daily at 01:00 | Daily run for yesterday: weather, go-arounds, route deviations, complexity, early warning, SPIs. Days for which NavTrace received late data in the last 30 days are re-processed automatically |
| Daily at 02:00 | On the report day, the SRB report draft |

## 11.4 Audit log

Every change to data, parameters, SPI definitions, validations, labels, users and reports, and every export, is recorded with the user, time and before and after values. The log cannot be edited. Filter it and export it to Excel.

![Audit log](img/27_audit.png)

## 11.5 Access tokens and the API

Scripts and other systems use the REST API with a personal access token. Create one under **Account → Personal access tokens**: it has your rights and an expiry date, and is shown only once. The API is described at `/api/docs` (OpenAPI 3.1). Examples:

```
curl -H "Authorization: Bearer nvr_…" https://navruna/api/v1/spis
curl -H "Authorization: Bearer nvr_…" "https://navruna/api/v1/spis/GA-01/observations?period=M&dims=aerodrome"
curl -H "Authorization: Bearer nvr_…" "https://navruna/api/v1/alerts?status=open"
curl -H "Authorization: Bearer nvr_…" "https://navruna/api/v1/ga/events?date_from=2026-10-01&aerodrome=WIII&fmt=csv"
```

**Webhook alerts** are JSON POSTs with `source, alert_id, type, ref, level, title, first_seen, status, link, breakdown`. With `NAVRUNA_WEBHOOK_SECRET` set, the header `X-NavRUNA-Signature: sha256=<hex>` is the HMAC-SHA256 of the body. A receiver checks it like this:

```
import hmac, hashlib
ok = hmac.compare_digest(header, "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
```

# 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "NavTrace is not configured" | Set `NAVRUNA_NAVTRACE_DSN` in `.env` and restart, or use demo mode |
| "NavTrace not reachable" | The server cannot reach the database (VPN, firewall, password). Test under **Connections** |
| BMKG test: "a web page instead of data" | BMKG's website protection blocked the request: ask BMKG to exempt token-authenticated API calls or allow the server's address |
| Warning "no weather reports; QNH 1013.25 assumed" | No BMKG data for that aerodrome and day. Heights can be off by about 27 ft per hPa; fix the BMKG connection and re-run the go-around job |
| Runway-days marked incomplete | ADS-B coverage does not reach 1,000 ft there. Check the coverage table (Go-arounds → Validation); the parameter `ga.coverage_min_share` sets the limit |
| SPI value marked "too few movements" | The period or baseline has fewer approaches than `alert.min_period_den` / `alert.min_baseline_den`; no alert is raised |
| Route has no nominal paths | Fewer than 10 flights in 90 days, or the job has not run yet. Start **Trajectory deviation monitor** with **rebuild** ticked |
| Complexity: "No sectors loaded" | Refresh NavEarth under **Reference data → Sectors** |
| Early warning: "fewer than 90 days of history" | Load more history (section 3.5) |
| The interface looks outdated after an update | Rebuild the interface (`docker compose up -d --build`, or `./scripts/start.sh`) |
| Sign-in: "Account locked" | Wait 15 minutes, or an administrator saves the account again to unlock it |
