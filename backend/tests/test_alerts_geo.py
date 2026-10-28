"""Alert grouping and webhook signature; geometry helpers for TDM and CXI."""
import hashlib
import hmac
import json

import numpy as np
import pandas as pd

from app.core import alerts, geo
from app.models import Alert
from app.modules.cxi.core import _count_steps, _crosses
from app.modules.tdm.core import cross_track, resample
from app.params import defaults


def test_alert_grouping(db, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "deliver", lambda db_, a: sent.append(a.level) or setattr(a, "last_sent", alerts.now()) or setattr(a, "sent_level", a.level))
    alerts.raise_alert(db, "SPI", "X-1", 1, "t", "m", "p", breakdown_key="k")
    alerts.raise_alert(db, "SPI", "X-1", 1, "t", "m", "p", breakdown_key="k")     # same level, inside quiet period
    alerts.raise_alert(db, "SPI", "X-1", 2, "t", "m", "p", breakdown_key="k")     # level changed
    assert sent == [1, 2] and db.query(Alert).filter_by(ref="X-1").count() == 1   # FR-CORE-022
    alerts.close_alert(db, "SPI", "X-1", "k")
    assert db.query(Alert).filter_by(ref="X-1").one().status == "closed"


def test_webhook_signature(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "WEBHOOK_URL", "http://example.invalid/hook")
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "s3cret")
    seen = {}

    class R:
        status_code = 200
    monkeypatch.setattr(alerts.httpx, "post", lambda url, content, headers, timeout: seen.update(body=content, headers=headers) or R())
    a = Alert(id=7, source="SPI", ref="GA-01", level=2, title="t", public_message="GA-01 at level 2", link="/spis/GA-01",
              first_seen=alerts.now(), status="open", breakdown_key="")
    assert alerts._webhook(a, "x")
    sig = hmac.new(b"s3cret", seen["body"], hashlib.sha256).hexdigest()
    assert seen["headers"]["X-NavRUNA-Signature"] == f"sha256={sig}"                # FR-CORE-024
    assert "callsign" not in json.loads(seen["body"])


def test_cross_track_sign_and_resample():
    path = np.array([[0, 0], [0, 10.0]])                       # northbound
    off, st = cross_track(np.array([1.0, -1.0]), np.array([5.0, 5.0]), path)
    assert off[0] > 0 > off[1] and np.allclose(st, 5)          # + = right of travel
    r = resample(np.array([0, 0, 0.0]), np.array([0, 5, 10.0]), np.array([0, 1000, 2000.0]), 11)
    assert r.shape == (11, 3) and abs(r[5, 1] - 5) < 1e-9


def test_crossing_and_steps():
    P = defaults()
    a = pd.DataFrame({"x": [0, 10.0], "y": [0, 0.0], "t": [0, 60], "alt": [30000, 30000], "trk": [90, 90], "sec": [1, 1]})
    b = pd.DataFrame({"x": [5, 5.0], "y": [-5, 5.0], "t": [10, 70], "alt": [30500, 30500], "trk": [0, 0], "sec": [1, 1]})
    assert _crosses(a, b, 1, P)
    b2 = b.assign(alt=[36000, 36000])
    assert not _crosses(a, b2, 1, P)                               # vertical band 2,000 ft
    assert _count_steps(np.array([0, 400, 900, 1100, 2100, 2200.0]), 1000) == 2


def test_polygon_index():
    g = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    idx = geo.PolygonIndex([g])
    assert list(idx.contains(0, [0.5, 2], [0.5, 0.5])) == [True, False]
