from services.propagation.app.ingestion.parser import parse_tle_pair, parse_tle_epoch, parse_tle_orbital_elements
from services.propagation.app.ingestion.celestrak import CelesTrakIngestionClient

LINE1 = "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993"
LINE2 = "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"


def test_tle_parser():
    parsed = parse_tle_pair(LINE1, LINE2, "ISS (ZARYA)")
    assert parsed["catalog_id"] == "25544"
    assert parsed["int_designator"] == "98067A"
    assert parsed["name"] == "ISS (ZARYA)"

    elements = parse_tle_orbital_elements(LINE2)
    assert round(elements["inclination_deg"], 2) == 51.64
    assert elements["perigee_altitude_km"] > 350.0
    assert elements["apogee_altitude_km"] < 500.0


def test_celestrak_cache_fallback():
    client = CelesTrakIngestionClient()
    tles = client.load_cached_tles()
    assert len(tles) > 0
    assert any(t["catalog_id"] == "25544" for t in tles)
