from services.propagation.app.screening.coarse_filter import CoarseFilter
from services.propagation.app.screening.fine_filter import FineFilter

ISS_LINE1 = "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993"
ISS_LINE2 = "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"

GEO_LINE1 = "1 20580U 90037B   26255.31944444  .00001250  00000+0  54210-4 0  9991"
GEO_LINE2 = "2 20580  00.0500 140.2310 0002850 290.1540  69.8120  1.00270000985401"  # GEO mean motion ~1 rev/day


def test_coarse_filter_altitude_elimination():
    coarse = CoarseFilter(buffer_km=50.0)

    iss_obj = {
        "catalog_id": "25544",
        "name": "ISS",
        "object_type": "SATELLITE",
        "tle_line_1": ISS_LINE1,
        "tle_line_2": ISS_LINE2
    }

    geo_obj = {
        "catalog_id": "20580",
        "name": "GEO_SAT",
        "object_type": "DEBRIS",
        "tle_line_1": GEO_LINE1,
        "tle_line_2": GEO_LINE2
    }

    # ISS (LEO ~420km) vs GEO (~35,786km) should NOT overlap
    assert coarse.is_candidate_pair(iss_obj, geo_obj) is False
