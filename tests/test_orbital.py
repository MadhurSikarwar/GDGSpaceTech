"""Orbit helpers: OMM -> SGP4 initialisation, derived altitudes, frame conversion."""
from datetime import datetime, timedelta

import numpy as np
from sgp4 import omm
from sgp4.api import Satrec

from orbitwatch import orbital

# ISS element set in CelesTrak's OMM JSON form.
ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-10-04T22:01:12.110016",
    "MEAN_MOTION": 15.49712548, "ECCENTRICITY": 0.0006791, "INCLINATION": 51.6316, "RA_OF_ASC_NODE": 140.2051,
    "ARG_OF_PERICENTER": 78.0021, "MEAN_ANOMALY": 282.1734, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 55020, "BSTAR": 0.00021866,
    "MEAN_MOTION_DOT": 0.00012017, "MEAN_MOTION_DDOT": 0,
}


def test_parse_omm_and_derived_altitudes():
    el = orbital.parse_omm(ISS_OMM)
    assert el["norad_id"] == 25544
    assert el["epoch"] == datetime(2026, 10, 4, 22, 1, 12, 110016)
    assert 410 < el["perigee_km"] < el["mean_altitude_km"] < el["apogee_km"] < 435
    assert abs(el["period_min"] - 1440 / 15.49712548) < 1e-9


def test_parse_omm_accepts_space_track_strings():
    strings = {k: str(v) for k, v in ISS_OMM.items()}
    assert orbital.parse_omm(strings)["mean_motion"] == ISS_OMM["MEAN_MOTION"]


def test_satrec_matches_sgp4_reference_initialiser():
    """Our initialisation from stored elements must equal sgp4's own OMM initialiser."""
    mine = orbital.satrec_from_elements(orbital.parse_omm(ISS_OMM))
    ref = Satrec()
    omm.initialize(ref, {k: str(v) for k, v in ISS_OMM.items()})
    for hours in (0, 6, 24, 72):
        jd, fr = orbital.julian(datetime(2026, 10, 4, 22, 1, 12) + timedelta(hours=hours))
        e1, r1, _ = mine.sgp4(jd, fr)
        e2, r2, _ = ref.sgp4(jd, fr)
        assert e1 == e2 == 0
        assert np.linalg.norm(np.subtract(r1, r2)) < 1e-6


def test_batch_propagation_and_geodetic():
    sat = orbital.satrec_from_elements(orbital.parse_omm(ISS_OMM))
    jd, fr = orbital.time_grid(datetime(2026, 10, 5), np.arange(0, 5580, 60))
    err, r, v = orbital.propagate([sat], jd, fr)
    assert r.shape == (1, 93, 3) and not err.any()
    speed = np.linalg.norm(v[0], axis=1)
    assert np.all((speed > 7.5) & (speed < 7.8))
    lat, lon, alt = orbital.ecef_to_geodetic(orbital.teme_to_ecef(r[0], jd, fr))
    # Geodetic latitude peaks slightly above the (geocentric) inclination: up to ~0.19 deg more at mid-latitudes.
    assert np.all(np.abs(lat) <= 51.632 + 0.2)
    assert np.all((alt > 405) & (alt < 440))
    assert lat.max() > 50 and lat.min() < -50   # one orbit reaches both extremes


def test_gmst_known_value():
    # GMST at J2000.0 (2000-01-01 12:00 UT1) is 280.46061837 degrees.
    jd, fr = orbital.julian(datetime(2000, 1, 1, 12))
    assert abs(np.degrees(orbital.gmst(np.array(jd), np.array(fr))) - 280.46061837) < 1e-4
