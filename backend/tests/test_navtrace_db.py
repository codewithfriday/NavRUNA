"""NavTrace SQL against a real MobilityDB (skipped unless NAVRUNA_TEST_MOBILITYDB is set, e.g.
'host=/var/run/postgresql port=5433 dbname=navtrace_flight user=postgres')."""
import os
from datetime import date, datetime, timezone

import numpy as np
import pytest

DSN = os.environ.get("NAVRUNA_TEST_MOBILITYDB")
pytestmark = pytest.mark.skipif(not DSN, reason="NAVRUNA_TEST_MOBILITYDB not set")


@pytest.fixture(scope="module")
def table():
    import psycopg
    from app.core import demo
    from app.core.refdata import runways_as_dicts
    con = psycopg.connect(DSN, autocommit=True)
    con.execute("CREATE SCHEMA IF NOT EXISTS navruna_test")
    con.execute("DROP TABLE IF EXISTS navruna_test.flight_trajectory")
    con.execute("CREATE TABLE navruna_test.flight_trajectory (gufi text PRIMARY KEY, icao24 text, callsign text, registration text, "
                "dof date, adep text, ades text, first_seen timestamptz, last_seen timestamptz, track tgeompoint, alt_baro tfloat, "
                "gs tfloat, pushed_at timestamptz)")
    rw = [r for r in runways_as_dicts() if r["icao"] == "WARR" and r["runway"] == "28"][0]
    rng = np.random.default_rng(0)
    d = date(2026, 3, 2)
    for k, ga in enumerate((None, 3.0)):
        df = demo._fly(demo._approach_wps(rw, ga, rng), datetime(2026, 3, 2, 5 + k))
        iso = lambda t: datetime.fromtimestamp(int(t), timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")
        tr = "SRID=4326;[" + ",".join(f"POINT({a:.6f} {b:.6f})@{iso(t)}" for a, b, t in zip(df.lon, df.lat, df.t)) + "]"
        al = "[" + ",".join(f"{a:.0f}@{iso(t)}" for a, t in zip(df.alt, df.t)) + "]"
        gs = "[" + ",".join(f"{a:.0f}@{iso(t)}" for a, t in zip(df.gs, df.t)) + "]"
        con.execute("INSERT INTO navruna_test.flight_trajectory VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::tgeompoint,%s::tfloat,%s::tfloat,now())",
                    (f"DB-{k}", "8a0001", f"GIA{k}", "PK-AAA", d, "WIII", "WARR", iso(df.t.iloc[0]), iso(df.t.iloc[-1]), tr, al, gs))
    yield "navruna_test.flight_trajectory"
    con.execute("DROP SCHEMA navruna_test CASCADE")


def test_db_source_detects_go_around(db, table):
    from app.core.navtrace import DbSource
    from app.models import GaApproach
    from app.modules.ga.detect import detect_day
    from app.params import defaults
    src = DbSource(DSN, table)
    assert src.status()["flights"] == 2
    detect_day(db, src, "WARR", date(2026, 3, 2), defaults(), 1)
    rows = {a.gufi: a for a in db.query(GaApproach).filter(GaApproach.gufi.like("DB-%"), GaApproach.approach_no == 1)}
    assert rows["DB-0"].outcome == "landed" and rows["DB-1"].outcome == "go_around"
    m, pts = src.flight_track("DB-1", 112.7864, -7.3808, 40)
    assert m["callsign"] == "GIA1" and len(pts) > 100
    got = list(src.flights_in_window(datetime(2026, 3, 2, 5), datetime(2026, 3, 2, 7), (110, -10, 115, -5), step_s=30))
    assert len(got) == 2
