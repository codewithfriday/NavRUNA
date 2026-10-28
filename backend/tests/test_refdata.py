from app.core.refdata import import_runways, parse_level
from app.models import RefRunway

CSV = """icao,runway,thr_lat,thr_lon,thr_elev_ft,true_brg,length_m,width_m,arp_lat,arp_lon,arp_elev_ft,notes
WXXA,09,-1.0,100.0,10,,2000,45,-1.0,100.009,12,
WXXA,27,-1.0,100.01797,,270,2000,45,-1.0,100.009,12,
WXXB,21,0.98,107.56,11,,1200,30,0.97,107.56,19,
WXXC,10,-2.0,110.0,5,90,3000,45,-2.0,110.01,5,
WXXC,28,-2.0,110.009,5,270,3000,45,-2.0,110.01,5,
"""


def test_import_derives_and_flags(db):
    rep = import_runways(db, CSV, "test")
    r09 = db.query(RefRunway).filter_by(icao="WXXA", runway="09").one()
    assert abs(r09.true_brg - 90) < 0.1 and "true_brg" in r09.derived           # DR-004 from the opposite end
    r27 = db.query(RefRunway).filter_by(icao="WXXA", runway="27").one()
    assert r27.thr_elev_ft == 12                                                 # aerodrome elevation
    one = db.query(RefRunway).filter_by(icao="WXXB").one()
    assert any("one runway end" in i for i in one.issues) and "WXXB 21" in rep["incomplete"]
    assert any("WXXC" in m for m in rep["length_mismatch"])                     # DR-005: 1,000 m apart vs 3,000 m
    from app.core.refdata import seed_runways
    db.query(RefRunway).delete()
    db.commit()
    seed_runways(db)                                                             # restore the real file for other tests


def test_parse_levels():
    assert parse_level("FL245", 0) == (24500, False)
    assert parse_level("10000 ft/12000 ft/FL150*", 0) == (10000, True)
    assert parse_level("FL245/FL370", 0) == (24500, True)
    assert parse_level("SFC", 99) == (0, False)
    assert parse_level("FL 600", 0) == (60000, False)
