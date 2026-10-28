from datetime import datetime

from app.core.weather import parse_metar, store_reports, wind_components
from app.models import WxReport


def test_parse_real_bmkg_formats():
    p = parse_metar("METAR WIII 010000Z 08004KT 060V120 4500 HZ FEW020 28/24 Q1013 NOSIG=")
    assert (p["wind_dir"], p["wind_kt"], p["vis_m"], p["qnh_hpa"], p["temp_c"]) == (80, 4, 4500, 1013, 28) and p["parse_ok"]
    p = parse_metar("SPECI WADD 011012Z 27015G28KT 2000 +TSRA SCT015CB BKN080 25/23 Q1012=")
    assert p["ts"] and p["cb"] and p["gust_kt"] == 28 and p["precip"] == 3 and p["ceiling_ft"] == 8000
    p = parse_metar("METAR WSSS 010000Z 33005KT 9999 VCTS FEW018CB 27/25 Q1010 TEMPO TSRA=")
    assert p["vcts"] and not p["ts"]                          # trend groups are ignored
    p = parse_metar("METAR WMKK 300100Z VRB01KT 5000 HZ FEW017CB 29/24 Q1013 NOSIG=")
    assert p["wind_dir"] is None and p["wind_kt"] == 1
    p = parse_metar("METAR WARR 010000Z /////KT 9999 FEW020 28/24 Q1013 RMK WS RWY10=")
    assert not p["parse_ok"] and p["wind_shear"] == "WS RWY10"


def test_keep_report_received_last(db):
    t = datetime(2026, 9, 1, 0, 0)
    rows = [{"icao_code": "WIII", "observed_time": "2026-09-01 00:00:00", "insert_time": "2026-09-01 00:05:00",
             "data_text": "METAR WIII 010000Z 08004KT 9999 FEW020 28/24 Q1013="},
            {"icao_code": "WIII", "observed_time": "2026-09-01 00:00:00", "insert_time": "2026-09-01 00:09:00",
             "data_text": "METAR COR WIII 010000Z 09006KT 9999 FEW020 28/24 Q1012="},
            {"icao_code": "WIII", "observed_time": "2026-09-01 00:00:00", "insert_time": "2026-09-01 00:02:00",
             "data_text": "METAR WIII 010000Z 07003KT 9999 FEW020 28/24 Q1014="}]
    store_reports(db, rows)
    w = db.query(WxReport).filter_by(icao="WIII", observed=t).all()
    assert len(w) == 1 and w[0].qnh_hpa == 1012                 # DR-007


def test_wind_components():
    w = WxReport(wind_dir=270, wind_kt=10)
    head, cross = wind_components(w, 90)
    assert head == -10 and cross == 0                            # pure tailwind on runway 09
    head, cross = wind_components(WxReport(wind_dir=0, wind_kt=10), 90)
    assert abs(head) < 1e-6 and cross == 10
