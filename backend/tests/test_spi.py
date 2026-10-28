"""SPI engine: alert methods, approval, registered formulas (FR-SPI-001..006, FR-GA-025..027)."""
from datetime import date, datetime, timedelta

import pandas as pd

from app.models import GaApproach, SpiDefinition, SpiObservation
from app.modules.spi import engine
from app.params import defaults


def test_periods():
    assert engine.period_start(date(2026, 10, 28), "W") == date(2026, 10, 26)
    assert engine.period_end(date(2026, 2, 1), "M") == date(2026, 2, 28)
    assert len(engine.periods_between(date(2026, 1, 15), date(2026, 3, 2), "M")) == 3


def test_doc9859_rules():
    P = defaults()
    hist = [10, 11, 9, 10, 10, 11, 9, 10]
    run = {1: 0, 2: 0, 3: 0}
    state, lv, mu = engine._doc9859(hist, 30, run, P, "up_bad")
    assert state == 3 and lv["l3"] < 30                              # one point above level 3
    run = {1: 0, 2: 0, 3: 0}
    s1, lv, _ = engine._doc9859(hist, lv["l2"] + 0.01, run, P, "up_bad")
    s2, _, _ = engine._doc9859(hist, lv["l2"] + 0.01, run, P, "up_bad")
    assert s1 < 2 and s2 == 2                                        # two consecutive above level 2


def _approaches(db, icao, d0, days, per_day, ga_days):
    for k in range(days):
        d = d0 + timedelta(days=k)
        for j in range(per_day):
            ga = d in ga_days and j < ga_days[d]
            db.add(GaApproach(icao=icao, runway="07", day=d, gufi=f"S-{icao}-{d}-{j}", t_start=datetime(d.year, d.month, d.day, 8),
                              t_end=datetime(d.year, d.month, d.day, 8), t_thr=datetime(d.year, d.month, d.day, 8, j % 60),
                              outcome="go_around" if ga else "landed", min_height_ft=0, min_dist_nm=0, operator="GIA",
                              ga_band="mid" if ga else ""))
    db.commit()


def test_poisson_alert_on_ga_rate(db):
    d0 = date(2025, 1, 6)
    # 120 days at 100 approaches a day, 1 go-around every 4 days (2.5 per 1,000), then a week with 12
    ga_days = {d0 + timedelta(days=k): 1 for k in range(0, 112, 4)}
    ga_days.update({d0 + timedelta(days=k): 2 for k in range(112, 118)})
    _approaches(db, "WTST", d0, 119, 100, ga_days)
    spi = SpiDefinition(code="T-GA", version=1, name="test rate", formula="ga_rate", unit="per 1,000", scale=1000,
                        breakdowns=["aerodrome"], periods=["W"], alert_method="poisson", baseline_window=90, status="approved")
    db.add(spi)
    db.commit()
    engine.compute(db, spi, d0, d0 + timedelta(days=118), defaults(), alerts=False)
    obs = {o.period_start: o for o in db.query(SpiObservation).filter_by(spi_code="T-GA", breakdown_key="aerodrome=WTST")}
    last = obs[engine.period_start(d0 + timedelta(days=114), "W")]
    assert last.alert_state >= 2 and last.levels["p"] < 0.0023
    quiet = obs[engine.period_start(d0 + timedelta(days=100), "W")]
    assert quiet.alert_state == 0
    early = obs[d0]
    assert early.levels.get("note") == "too few movements"               # FR-GA-027


def test_only_registered_formulas_and_four_eyes(client, admin_headers):
    bad = client.post("/api/v1/spis", headers=admin_headers, json={"code": "X-1", "name": "bad", "formula": "os.system"})
    assert bad.status_code == 400 and "not registered" in bad.text      # FR-SPI-002
    ok = client.post("/api/v1/spis", headers=admin_headers, json={"code": "GA-90", "name": "Go-arounds per week", "formula": "ga_count",
                                                                    "periods": ["W"], "alert_method": "none", "breakdowns": ["aerodrome+runway"]})
    assert ok.status_code == 200 and ok.json()["status"] == "draft"
    own = client.post("/api/v1/spis/GA-90/approve", headers=admin_headers, json={"decision": "approve"})
    assert own.status_code == 403                                        # FR-CORE-012: the author cannot approve
