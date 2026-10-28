"""Each test session gets its own data folder and runs in demo mode without the scheduler."""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="navruna_test_")
os.environ.setdefault("NAVRUNA_DATA_DIR", _tmp)
os.environ["NAVRUNA_SCHEDULER"] = "0"
os.environ.setdefault("NAVRUNA_DEMO", "1")
os.environ.setdefault("NAVRUNA_ADMIN_PASSWORD", "Admin!Test2026x")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app, bootstrap
    bootstrap()
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client):
    from app.db import SessionLocal
    from app.models import User
    db = SessionLocal()
    u = db.query(User).filter_by(username="admin").first()
    u.must_change_password = False
    db.commit()
    db.close()
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": os.environ["NAVRUNA_ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def db(app):
    from app.db import SessionLocal
    s = SessionLocal()
    yield s
    s.close()
