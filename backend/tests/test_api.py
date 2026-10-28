"""Sign-in, roles, masking, tokens, audit and exports through the API (FR-CORE-030..042, SEC-001..012)."""
from datetime import date, datetime

from app.models import AuditLog, GaApproach


def _user(client, h, name, role, scope=None):
    r = client.post("/api/v1/admin/users", headers=h, json={"username": name, "full_name": name, "role": role, "scope": scope or [],
                                                            "password": "Start!Pass2026"})
    assert r.status_code in (200, 409), r.text
    t = client.post("/api/v1/auth/login", json={"username": name, "password": "Start!Pass2026"}).json()["token"]
    return {"Authorization": f"Bearer {t}"}


def test_password_policy_and_lockout(client, admin_headers):
    r = client.post("/api/v1/admin/users", headers=admin_headers, json={"username": "weak", "role": "viewer", "password": "short"})
    assert r.status_code == 400
    _user(client, admin_headers, "locky", "viewer")
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "locky", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "locky", "password": "Start!Pass2026"}).status_code == 423


def test_roles_and_masking(client, admin_headers, db):
    db.add(GaApproach(icao="WIII", runway="25R", day=date(2026, 6, 1), gufi="M-1", callsign="GIA123", registration="PK-GFA",
                      operator="GIA", t_start=datetime(2026, 6, 1, 3), t_end=datetime(2026, 6, 1, 3), t_thr=datetime(2026, 6, 1, 3),
                      outcome="go_around", min_height_ft=600, min_dist_nm=2, ga_height_ft=600, ga_dist_nm=2, ga_band="mid"))
    db.commit()
    viewer = _user(client, admin_headers, "vw", "viewer")
    ev = client.get("/api/v1/ga/events?date_from=2026-06-01&date_to=2026-06-01", headers=viewer).json()
    assert ev and ev[0]["callsign"] == "—" and ev[0]["operator"].startswith("Operator ")         # SEC-001/003
    full = client.get("/api/v1/ga/events?date_from=2026-06-01&date_to=2026-06-01", headers=admin_headers).json()
    assert full[0]["callsign"] == "GIA123" and full[0]["operator"] == "GIA"
    assert client.get("/api/v1/ga/events?operator=GIA", headers=viewer).status_code == 403
    assert client.post("/api/v1/admin/params", headers=viewer, json={"values": {}, "reason": "x"}).status_code == 403
    scoped = _user(client, admin_headers, "vw_warr", "viewer", ["WARR"])
    assert client.get("/api/v1/ga/events?date_from=2026-06-01&date_to=2026-06-01", headers=scoped).json() == []   # FR-CORE-031


def test_params_versioned_and_audited(client, admin_headers, db):
    r = client.post("/api/v1/admin/params", headers=admin_headers, json={"values": {"ga.climb_trigger_ft": 350}, "reason": "test"})
    assert r.status_code == 200 and r.json()["changes"]["ga.climb_trigger_ft"]["to"] == 350
    assert client.post("/api/v1/admin/params", headers=admin_headers, json={"values": {"ga.climb_trigger_ft": 99999}, "reason": "x"}).status_code == 400
    p = client.get("/api/v1/admin/params", headers=admin_headers).json()
    assert p["version"] >= 2 and any(i["key"] == "ga.climb_trigger_ft" and i["value"] == 350 for i in p["items"])
    assert db.query(AuditLog).filter_by(action="params_changed").count() >= 1
    client.post(f"/api/v1/admin/params/1/activate", headers=admin_headers)


def test_tokens(client, admin_headers):
    r = client.post("/api/v1/auth/tokens", headers=admin_headers, json={"name": "monitor", "days": 30}).json()
    h = {"Authorization": f"Bearer {r['token']}"}
    assert client.get("/api/v1/spis", headers=h).status_code == 200                          # FR-CORE-042
    client.delete(f"/api/v1/auth/tokens/{r['id']}", headers=admin_headers)
    assert client.get("/api/v1/spis", headers=h).status_code == 401


def test_exports_and_openapi(client, admin_headers):
    r = client.get("/api/v1/ga/events?fmt=csv&date_from=2026-06-01&date_to=2026-06-01", headers=admin_headers)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "GIA123" in r.text
    x = client.get("/api/v1/alerts?fmt=xlsx", headers=admin_headers)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    spec = client.get("/api/openapi.json").json()
    assert spec["openapi"].startswith("3.1") and "/api/v1/spis/{code}/observations" in spec["paths"]   # NFR-007


def test_spi_endpoints(client, admin_headers):
    spis = client.get("/api/v1/spis", headers=admin_headers).json()
    assert {"GA-01", "TDM-01", "CXI-01", "EWS-01"} <= {s["code"] for s in spis}
    assert client.get("/api/v1/spis/GA-01/observations?period=M", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/alerts?status=open", headers=admin_headers).status_code == 200
