"""Capture the manual's screenshots into docs/img from a running NavRUNA (normally a demo installation).

Usage: python3 docs/tools/screenshots.py http://localhost:8000 <admin user> <admin password>
The account must already have changed its first password.
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, USER, PW = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
IMG = Path(__file__).resolve().parents[1] / "img"
IMG.mkdir(exist_ok=True)


def api(path, body=None, token=None):
    req = urllib.request.Request(BASE + "/api" + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    token = api("/v1/auth/login", {"username": USER, "password": PW})["token"]
    ga = api("/v1/ga/events?date_from=2026-09-01&date_to=2026-10-27&aerodrome=WIII", token=token)
    ga_id = (ga[0] if isinstance(ga, list) else ga["rows"][0])["id"] if ga else None
    routes = api("/v1/tdm/routes", token=token)
    route = max(routes, key=lambda r: r.get("flights_90d") or r.get("flights") or 0)["id"] if routes else None
    ews = api("/v1/ews/anomalies?date_from=2026-08-01&date_to=2026-10-27", token=token)

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1.5)

        def shot(name, path=None, click=None, wait=2500, full=False, before=None):
            if path:
                pg.goto(BASE + path)
            if click:
                for c in click if isinstance(click, list) else [click]:
                    pg.get_by_text(c, exact=True).first.click()
                    pg.wait_for_timeout(600)
            if before:
                before()
            pg.wait_for_timeout(wait)
            pg.screenshot(path=str(IMG / f"{name}.png"), full_page=full)
            print("captured", name)

        shot("01_login", "/", wait=1500)
        pg.evaluate(f"sessionStorage.setItem('navruna_token', {json.dumps(token)})")
        shot("02_dashboard", "/dashboard", wait=4000)
        shot("03_ga_map", "/ga?aerodrome=WIII", click="Last 7 days", wait=4000)
        shot("04_ga_trend", "/ga?aerodrome=WIII&tab=trend", wait=3500)
        shot("05_ga_events", "/ga?tab=events", wait=3000)
        if ga_id:
            shot("06_replay", f"/ga/approach/{ga_id}", wait=4500, full=True)
        shot("07_ga_heat", "/ga?aerodrome=WIII&tab=heat", wait=3500)
        shot("08_ga_factors", "/ga?tab=factors", wait=3500)
        shot("09_ga_validation", "/ga?tab=validation", wait=3500)
        shot("10_spis", "/spis", wait=3000)
        shot("11_spi_detail", "/spis/GA-01", wait=4000, full=True)
        shot("12_alerts", "/alerts", wait=3000)
        shot("13_tdm_routes", "/tdm", wait=3000)
        if route:
            shot("14_tdm_route", f"/tdm/routes/{route}?day=2026-10-27", wait=5000)
            shot("15_tdm_points", None, click="Critical points", wait=3000)
        shot("16_tdm_hazards", "/tdm", click="Hazard candidates (SRM)", wait=3500)
        shot("17_cxi_heat", "/cxi", wait=4000)
        shot("18_cxi_map", "/cxi", click="Map by hour", wait=4000,
             before=lambda: pg.locator("input[type=datetime-local]").fill("2026-10-27T08:00"))
        shot("19_ews", "/ews", wait=3500)
        if ews:
            pg.locator("tbody tr").first.click()
            shot("20_ews_cell", None, wait=3000)
        shot("21_reports", "/reports", wait=2500)
        shot("22_refs", "/admin/refs", wait=3000)
        shot("23_params", "/admin/params", wait=2500)
        shot("24_jobs", "/admin/jobs", wait=2500)
        shot("25_settings", "/admin/settings", wait=2500)
        shot("26_users", "/admin/users", wait=2500)
        shot("27_audit", "/admin/audit", wait=2500)
        b.close()


if __name__ == "__main__":
    main()
