"""Go-around detection on hand-built flights (FR-GA-002..008, FR-GA-020)."""
from datetime import date, datetime

import numpy as np
import pytest

from app.core import demo
from app.models import GaApproach, GaCoverage, RefRunway
from app.modules.ga.detect import detect_day
from app.params import defaults

DAY = date(2026, 3, 2)


class FakeSource:
    def __init__(self, flights):
        self.flights = flights

    def flights_near(self, lon, lat, radius_nm, day, step_s=5, batch=0):
        yield from self.flights


def rw(db):
    r = db.query(RefRunway).filter_by(icao="WARR", runway="28").one()
    return {"thr_lat": r.thr_lat, "thr_lon": r.thr_lon, "true_brg": r.true_brg, "thr_elev_ft": r.thr_elev_ft}


def flight(wps, hour, k, ground_elev=8, day=DAY):
    df = demo._fly(wps, datetime(day.year, day.month, day.day, hour, k))
    df.loc[(df.alt <= ground_elev + 1) & (df.gs < 130), "alt"] = np.nan
    return ({"gufi": f"T-{hour}-{k}", "callsign": f"GIA{hour}{k}", "adep": "WIII", "ades": "WARR", "registration": "PK-XYZ",
             "icao24": "8a0000"}, df)


@pytest.fixture
def results(db):
    r = rw(db)
    rng = np.random.default_rng(0)
    fl = [flight(demo._approach_wps(r, None, rng), 2, 0),             # landed
          flight(demo._approach_wps(r, 4.0, rng), 3, 0),              # go-around high (about 1,320 ft)
          flight(demo._approach_wps(r, 1.0, rng), 4, 0)]              # go-around low (about 370 ft)
    # touch-and-go: down to the runway and away again
    tl, tn, b, e = r["thr_lat"], r["thr_lon"], r["true_brg"], r["thr_elev_ft"]
    p = lambda d, h, s: (*demo._dest_point(tl, tn, (b + 180) % 360, d), e + h, s)
    a = demo._dest_point(tl, tn, b, 3)
    fl.append(flight([p(7, 2300, 120), p(4, 1300, 100), p(2, 640, 90), p(0.5, 160, 80), p(0, 15, 75), p(-0.4, 15, 75), (*a, e + 1500, 100)], 5, 0))
    # departure on runway 28: never an approach
    fl.append(flight([(tl, tn, e, 30), (*demo._dest_point(tl, tn, b, 0.8), e, 150), (*demo._dest_point(tl, tn, b, 10), e + 5000, 230)], 6, 0))
    # rotorcraft approach at 50 kt: excluded
    fl.append(flight([p(6, 1900, 50), p(2, 640, 45), p(0, 50, 40), p(-0.2, 0, 10)], 7, 0))
    stats = detect_day(db, FakeSource(fl), "WARR", DAY, defaults(), 1)
    rows = {a.gufi: a for a in db.query(GaApproach).filter_by(icao="WARR", day=DAY).order_by(GaApproach.approach_no.desc())}
    return stats, rows


def test_classification(results):
    stats, rows = results
    landed, high, low, tng = rows["T-2-0"], rows["T-3-0"], rows["T-4-0"], rows["T-5-0"]
    assert landed.outcome == "landed" and landed.runway == "28"
    assert high.outcome == "go_around" and high.approach_no == 1 and high.ga_band == "high"
    assert 3.5 < high.ga_dist_nm < 4.5 and 1100 < high.ga_height_ft < 1450
    assert low.outcome == "go_around" and low.ga_band == "low" and low.ga_height_ft < 500
    assert tng.outcome in ("touch_and_go", "low_approach")
    assert "T-6-0" not in rows and "T-7-0" not in rows          # departure, rotorcraft


def test_second_approach_and_outcome(results):
    _, rows = results
    high = rows["T-3-0"]
    assert high.ga_outcome == "landed_same" and high.n_approaches == 2 and high.ga_delay_min > 3
    assert high.validation == "pending"                         # every event reviewed in the first months
    assert not high.repeated


def test_gates_and_sequence(results):
    _, rows = results
    g = rows["T-2-0"].gates
    assert {"6", "4", "2", "h1000", "h500"} <= set(g)
    assert abs(g["4"]["dev_ft"]) < 150 and abs(g["4"]["lat_m"]) < 200


def test_coverage_incomplete(db):
    r = rw(db)
    rng = np.random.default_rng(1)
    fl = []
    for k in range(4):                                             # tracks lost at about 1,500 ft
        _, df = flight(demo._approach_wps(r, None, rng), 9, k, day=date(2026, 3, 3))
        df = df[(df.alt > 1500) | (df.index < 10)]
        fl.append(({"gufi": f"C-{k}", "callsign": f"LNI{k}"}, df.reset_index(drop=True)))
    detect_day(db, FakeSource(fl), "WARR", date(2026, 3, 3), defaults(), 1)
    cov = db.query(GaCoverage).filter_by(icao="WARR", day=date(2026, 3, 3), runway="28").one()
    assert cov.incomplete and cov.share < 0.8                       # FR-GA-020
    assert all(a.incomplete for a in db.query(GaApproach).filter_by(icao="WARR", day=date(2026, 3, 3)))


def test_reprocessing_keeps_validation(db, results):
    _, rows = results
    a = rows["T-3-0"]
    a.validation, a.validated_by = "confirmed", "tester"
    db.commit()
    r = rw(db)
    rng = np.random.default_rng(0)
    fl = [flight(demo._approach_wps(r, None, rng), 2, 0), flight(demo._approach_wps(r, 4.0, rng), 3, 0)]
    detect_day(db, FakeSource(fl), "WARR", DAY, defaults(), 2)
    again = db.query(GaApproach).filter_by(icao="WARR", day=DAY, gufi="T-3-0", approach_no=1).one()
    assert again.validation == "confirmed" and again.param_version == 2  # FR-CORE-003, FR-GA-015
