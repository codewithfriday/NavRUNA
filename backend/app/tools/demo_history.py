"""Fill a NavRUNA installation with a period of demo history (NAVRUNA_DEMO=1 only).

    NAVRUNA_DEMO=1 python -m app.tools.demo_history --from 2026-05-01 --to 2026-10-27 --cxi-days 21

Runs weather, go-arounds, route deviations, early warning and SPIs over the period, complexity over the last
--cxi-days days, confirms a share of go-arounds as an analyst would, and drafts the SRB report of the last
full month. Takes about 15 seconds per day.
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from .. import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--cxi-days", type=int, default=14)
    ap.add_argument("--confirm-share", type=float, default=0.9)
    a = ap.parse_args()
    if not config.DEMO:
        raise SystemExit("Only for demo installations: set NAVRUNA_DEMO=1")
    from ..main import bootstrap
    bootstrap()
    from ..core.jobs import run_sync
    from ..db import SessionLocal
    from ..models import GaApproach, JobRun
    d0, d1 = date.fromisoformat(a.d0), date.fromisoformat(a.d1)

    def run(name, x0, x1, opts=None):
        rid = run_sync(name, x0, x1, "demo", opts)
        db = SessionLocal()
        r = db.get(JobRun, rid)
        print(f"{name:8s} {x0} – {x1}: {r.status} {r.message[:120]} {r.warnings[:2]}", flush=True)
        db.close()
    run("weather", d0, d1)
    run("ga", d0, d1)
    run("tdm", d0, d1, {"rebuild": True})
    run("cxi", d1 - timedelta(days=a.cxi_days - 1), d1)
    # an analyst confirms most sampled events and rejects a few
    db = SessionLocal()
    rnd = random.Random(1)
    for g in db.query(GaApproach).filter(GaApproach.outcome == "go_around", GaApproach.validation == "pending",
                                         GaApproach.day <= d1 - timedelta(days=7)):
        if rnd.random() < a.confirm_share:
            g.validation, g.validated_by, g.validation_note = "confirmed", "demo-analyst", "Climb visible on the replay"
        elif rnd.random() < 0.5:
            g.validation, g.validated_by, g.validation_note = "rejected", "demo-analyst", "Track gap, not a go-around"
    db.commit()
    db.close()
    run("ews", d0, d1)
    run("spi", d0, d1, {"no_alerts": False})
    last_month = (d1.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    run("monthly", d1, d1, {"force": True, "month": last_month})


if __name__ == "__main__":
    main()
