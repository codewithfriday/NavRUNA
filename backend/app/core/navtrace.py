"""Reading tracks from NavTrace (PostgreSQL + MobilityDB, read-only) or from the synthetic demo source.

Every reader returns (meta, points) pairs: meta = gufi, callsign, icao24, registration, adep, ades, dof,
pushed_at; points = DataFrame t (epoch s), lon, lat, alt (pressure altitude ft, NaN on the ground), gs (kt).
The platform never writes to NavTrace (SRS 2.4).
"""
from __future__ import annotations

import calendar
import math
import threading
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .. import config
from ..models import utc_now

_sem = threading.BoundedSemaphore(config.NAVTRACE_MAX_CONNECTIONS)   # NFR-006


class NavTraceUnavailable(RuntimeError):
    pass


def _aware(t: datetime) -> datetime:
    """Naive UTC -> aware UTC, so PostgreSQL compares with timestamptz correctly whatever its time zone."""
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _span(t0: datetime, t1: datetime) -> str:
    return f"[{t0:%Y-%m-%d %H:%M:%S}+00, {t1:%Y-%m-%d %H:%M:%S}+00)"


def _box(lon: float, lat: float, radius_nm: float):
    dlat = radius_nm / 60
    dlon = radius_nm / (60 * max(0.2, math.cos(math.radians(lat))))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


class DbSource:
    name = "navtrace"

    def __init__(self, dsn: str | None = None, table: str | None = None):
        self.dsn = dsn or config.NAVTRACE_DSN
        self.table = table or config.NAVTRACE_TABLE
        if not self.dsn:
            raise NavTraceUnavailable("NavTrace is not configured (NAVRUNA_NAVTRACE_DSN)")

    def connect(self):
        import psycopg
        try:
            return psycopg.connect(self.dsn, connect_timeout=15)
        except Exception as e:  # noqa: BLE001
            raise NavTraceUnavailable(f"NavTrace not reachable: {e}") from e

    def _query(self, sql: str, params: dict) -> list[tuple]:
        with _sem, self.connect() as con, con.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def status(self) -> dict:
        rows = self._query(f"SELECT count(*), min(dof), max(dof), max(pushed_at) FROM {self.table}", {})
        n, d0, d1, pushed = rows[0]
        return {"ok": True, "flights": n, "first_day": str(d0) if d0 else None, "last_day": str(d1) if d1 else None,
                "last_pushed": str(pushed) if pushed else None}

    def changed_days(self, since: datetime | None) -> tuple[list[date], datetime | None]:
        """Days with flights pushed after the watermark (DR-001), and the new watermark."""
        if since is None:
            rows = self._query(f"SELECT DISTINCT dof FROM {self.table} WHERE dof >= current_date - 2", {})
        else:
            rows = self._query(f"SELECT DISTINCT dof FROM {self.table} WHERE pushed_at > %(s)s", {"s": _aware(since)})
        mx = self._query(f"SELECT max(pushed_at) FROM {self.table}", {})[0][0]
        return sorted(r[0] for r in rows if r[0]), (mx.astimezone(timezone.utc).replace(tzinfo=None) if mx else since)

    def _points(self, gufis: list[str], traj: str, params: dict) -> pd.DataFrame:
        if not gufis:
            return pd.DataFrame(columns=["gufi", "t", "lon", "lat", "alt", "gs"])
        sql = f"""
            SELECT f.gufi, extract(epoch FROM getTimestamp(i))::bigint AS t,
                   ST_X(getValue(i)) AS lon, ST_Y(getValue(i)) AS lat,
                   valueAtTimestamp(f.alt_baro, getTimestamp(i)) AS alt,
                   valueAtTimestamp(f.gs, getTimestamp(i)) AS gs
            FROM {self.table} f CROSS JOIN LATERAL unnest(instants({traj})) AS i
            WHERE f.gufi = ANY(%(gufis)s) ORDER BY f.gufi, t"""
        rows = self._query(sql, dict(params, gufis=gufis))
        return pd.DataFrame(rows, columns=["gufi", "t", "lon", "lat", "alt", "gs"])

    _META = "gufi, callsign, icao24, registration, adep, ades, dof, pushed_at"

    def _meta(self, where: str, params: dict) -> list[dict]:
        rows = self._query(f"SELECT {self._META} FROM {self.table} f WHERE track IS NOT NULL AND {where}", params)
        keys = self._META.split(", ")
        return [dict(zip(keys, r)) for r in rows]

    def _pairs(self, metas, df):
        g = dict(tuple(df.groupby("gufi"))) if len(df) else {}
        for m in metas:
            pts = g.get(m["gufi"])
            if pts is None or len(pts) < 2:
                continue
            yield m, _clean(pts.drop(columns="gufi"))

    def flights_near(self, lon: float, lat: float, radius_nm: float, day: date, step_s: int = 5, batch: int = 300):
        x0, y0, x1, y1 = _box(lon, lat, radius_nm)
        t0 = datetime(day.year, day.month, day.day) - timedelta(hours=1)
        t1 = t0 + timedelta(hours=26)
        p = {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "span": _span(t0, t1)}
        metas = self._meta("dof BETWEEN %(d0)s AND %(d1)s AND track && stbox(ST_MakeEnvelope(%(x0)s,%(y0)s,%(x1)s,%(y1)s,4326))",
                           dict(p, d0=day - timedelta(days=1), d1=day + timedelta(days=1)))
        traj = "atStbox(atTime(f.track, %(span)s::tstzspan), stbox(ST_MakeEnvelope(%(x0)s,%(y0)s,%(x1)s,%(y1)s,4326)))"
        if step_s > 5:
            traj = f"tsample({traj}, interval '{int(step_s)} seconds')"
        for k in range(0, len(metas), batch):
            part = metas[k:k + batch]
            yield from self._pairs(part, self._points([m["gufi"] for m in part], traj, p))

    def flights_between(self, adep: str, ades: str, d0: date, d1: date, step_s: int = 30, batch: int = 300):
        metas = self._meta("adep = %(a)s AND ades = %(b)s AND dof BETWEEN %(d0)s AND %(d1)s",
                           {"a": adep, "b": ades, "d0": d0, "d1": d1})
        traj = f"tsample(f.track, interval '{int(step_s)} seconds')"
        for k in range(0, len(metas), batch):
            part = metas[k:k + batch]
            yield from self._pairs(part, self._points([m["gufi"] for m in part], traj, {}))

    def flights_in_window(self, t0: datetime, t1: datetime, bbox: tuple, step_s: int = 10, batch: int = 500):
        x0, y0, x1, y1 = bbox
        p = {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "span": _span(t0, t1)}
        metas = self._meta("dof BETWEEN %(d0)s AND %(d1)s AND first_seen < %(t1)s AND last_seen >= %(t0)s "
                           "AND track && stbox(ST_MakeEnvelope(%(x0)s,%(y0)s,%(x1)s,%(y1)s,4326))",
                           dict(p, d0=t0.date() - timedelta(days=1), d1=t1.date(), t0=_aware(t0), t1=_aware(t1)))
        traj = f"tsample(atTime(f.track, %(span)s::tstzspan), interval '{int(step_s)} seconds')"
        for k in range(0, len(metas), batch):
            part = metas[k:k + batch]
            yield from self._pairs(part, self._points([m["gufi"] for m in part], traj, p))

    def flight_track(self, gufi: str, lon: float, lat: float, radius_nm: float):
        """One flight's points around an aerodrome, for the replay screen."""
        x0, y0, x1, y1 = _box(lon, lat, radius_nm)
        p = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
        metas = self._meta("gufi = %(g)s", {"g": gufi})
        traj = "atStbox(f.track, stbox(ST_MakeEnvelope(%(x0)s,%(y0)s,%(x1)s,%(y1)s,4326)))"
        for m, df in self._pairs(metas, self._points([gufi], traj, p)):
            return m, df
        return None, None

    def top_pairs(self, n: int, d0: date, d1: date) -> list[tuple[str, str, int]]:
        rows = self._query(f"SELECT adep, ades, count(*) FROM {self.table} WHERE dof BETWEEN %(d0)s AND %(d1)s "
                           "AND adep IS NOT NULL AND ades IS NOT NULL AND adep <> ades GROUP BY adep, ades "
                           "ORDER BY count(*) DESC LIMIT %(n)s", {"d0": d0, "d1": d1, "n": n})
        return [(a, b, c) for a, b, c in rows]


class DemoSource:
    """Synthetic flights (core/demo.py) behind the same interface."""
    name = "demo"

    def __init__(self, runways: list[dict]):
        self.runways = runways

    def status(self) -> dict:
        return {"ok": True, "flights": None, "first_day": None, "last_day": None, "last_pushed": None, "demo": True}

    def changed_days(self, since):
        today = utc_now().date()
        return [today - timedelta(days=1)], utc_now()

    def _all(self, day: date):
        from . import demo
        for m, df in demo.day_flights(day, self.runways):
            yield dict(m, pushed_at=datetime(day.year, day.month, day.day) + timedelta(days=1)), df

    def flights_near(self, lon, lat, radius_nm, day, step_s=5, batch=0):
        x0, y0, x1, y1 = _box(lon, lat, radius_nm)
        for d in (day - timedelta(days=1), day):
            for m, df in self._all(d):
                sel = df[(df.lon >= x0) & (df.lon <= x1) & (df.lat >= y0) & (df.lat <= y1)]
                a = calendar.timegm(day.timetuple())
                sel = sel[(sel.t >= a - 3600) & (sel.t < a + 25 * 3600)]
                if len(sel) >= 2:
                    yield m, _clean(sel)

    def flights_between(self, adep, ades, d0, d1, step_s=30, batch=0):
        d = d0
        while d <= d1:
            for m, df in self._all(d):
                if m["adep"] == adep and m["ades"] == ades:
                    yield m, _clean(_resample(df, step_s))
            d += timedelta(days=1)

    def flights_in_window(self, t0, t1, bbox, step_s=10, batch=0):
        a, b = calendar.timegm(t0.timetuple()), calendar.timegm(t1.timetuple())
        for d in sorted({t0.date() - timedelta(days=1), t0.date()}):
            for m, df in self._all(d):
                sel = df[(df.t >= a) & (df.t < b)]
                if len(sel) >= 2:
                    yield m, _clean(_resample(sel, step_s))

    def flight_track(self, gufi, lon, lat, radius_nm):
        try:
            d = datetime.strptime(gufi.split("-")[1], "%Y%m%d").date()
        except (IndexError, ValueError):
            return None, None
        x0, y0, x1, y1 = _box(lon, lat, radius_nm)
        for m, df in self._all(d):
            if m["gufi"] == gufi:
                sel = df[(df.lon >= x0) & (df.lon <= x1) & (df.lat >= y0) & (df.lat <= y1)]
                return m, _clean(sel)
        return None, None

    def top_pairs(self, n, d0, d1):
        from . import demo
        out = []
        for a, b, k in demo.PAIRS:
            out += [(a, b, k * (d1 - d0).days), (b, a, k * (d1 - d0).days)]
        return sorted(out, key=lambda r: -r[2])[:n]


def _resample(df: pd.DataFrame, step_s: int) -> pd.DataFrame:
    if step_s <= 5 or len(df) < 3:
        return df
    t = df.t.values
    grid = np.arange(t[0], t[-1] + 1, step_s)
    out = {"t": grid}
    for c in ("lon", "lat", "alt", "gs"):
        v = df[c].values.astype(float)
        ok = ~np.isnan(v)
        out[c] = np.interp(grid, t[ok], v[ok]) if ok.sum() >= 2 else np.full(len(grid), np.nan)
    res = pd.DataFrame(out)
    # keep ground (no altitude) where the nearest original point had none
    if df.alt.isna().any():
        idx = np.clip(np.searchsorted(t, grid), 1, len(t) - 1)
        nearest = np.where(np.abs(t[idx] - grid) < np.abs(t[idx - 1] - grid), idx, idx - 1)
        res.loc[df.alt.isna().values[nearest], "alt"] = np.nan
    return res


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype({"t": "int64", "lon": float, "lat": float, "alt": float, "gs": float})
    return df.dropna(subset=["lon", "lat"]).sort_values("t").drop_duplicates("t").reset_index(drop=True)


def source(db=None):
    """The configured track source."""
    if config.DEMO or not config.NAVTRACE_DSN:
        if not config.DEMO:
            raise NavTraceUnavailable("NavTrace is not configured: set NAVRUNA_NAVTRACE_DSN, or NAVRUNA_DEMO=1 to try with synthetic data")
        from .refdata import runways_as_dicts
        return DemoSource(runways_as_dicts(db))
    return DbSource()
