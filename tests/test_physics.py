"""Probability of collision, the CW manoeuvre optimizer and ground-station passes (ported from OrbitalGuard)."""
import math
from datetime import datetime

import numpy as np
import pytest

from orbitwatch.physics import collision, ground, maneuver


def test_foster_matches_closed_form_for_a_centred_isotropic_encounter():
    # Zero miss, isotropic sigma: Pc = 1 - exp(-R^2 / (2 sigma^2)).
    sigma, hbr = 0.2, 0.02
    pc, _, _ = collision.foster_pc([0.0, 0.0, 0.0], [0.0, 7.5, 0.0], np.eye(3) * sigma ** 2, hbr)
    assert pc == pytest.approx(1 - math.exp(-hbr ** 2 / (2 * sigma ** 2)), rel=1e-6)


def test_pc_falls_with_miss_distance_and_ignores_the_velocity_axis():
    cov = np.diag([0.3 ** 2, 0.3 ** 2, 0.3 ** 2])
    near = collision.foster_pc([0.1, 0.0, 0.0], [0, 7.5, 0], cov, 0.02)[0]
    far = collision.foster_pc([1.0, 0.0, 0.0], [0, 7.5, 0], cov, 0.02)[0]
    along = collision.foster_pc([0.1, 50.0, 0.0], [0, 7.5, 0], cov, 0.02)[0]  # offset along v projects out
    assert near > far > 0
    assert along == pytest.approx(near, rel=1e-9)


def test_covariance_grows_with_age_and_is_largest_in_track():
    young = collision.ric_sigmas_km(0, "Payload")
    old = collision.ric_sigmas_km(24, "Payload")
    debris = collision.ric_sigmas_km(24, "Debris")
    assert all(old > young) and old[1] == max(old) and all(debris > old)
    assert collision.ric_sigmas_km(24, "Payload", drag_scalar=2.0)[1] > old[1]


def test_pc_tiers():
    assert [collision.pc_tier(x) for x in (2e-4, 3e-5, 4e-6, 1e-9)] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def _head_on_encounter():
    # Primary on a circular 500 km orbit; secondary crossing 200 m away at 12 km/s.
    r = 6878.0
    v = math.sqrt(maneuver.MU / r)
    p_pos, p_vel = [r, 0.0, 0.0], [0.0, v, 0.0]
    s_pos, s_vel = [r + 0.2, 0.0, 0.0], [0.0, v * math.cos(math.radians(105)), v * math.sin(math.radians(105))]
    return maneuver.Encounter(p_pos, p_vel, s_pos, s_vel, 6.0, 6.0, "Payload", "Debris", 1.0, 20.0)


def test_cw_response_is_zero_at_zero_time_and_grows_along_track():
    n = 0.0011
    assert np.allclose(maneuver.phi_rv(n, 0.0), 0.0)
    m = maneuver.phi_rv(n, 2700.0)
    assert abs(m[1, 1]) > abs(m[0, 0])          # in-track impulse moves the in-track position most


def test_optimiser_finds_a_small_burn_that_meets_the_pc_target():
    enc = _head_on_encounter()
    pc0 = enc.pc_after(np.zeros(3), 1.0)
    assert pc0 > 1e-4                           # dangerous before the burn
    c = maneuver.optimise(enc, 2700.0)          # half an orbit before TCA
    assert c["converged"]
    assert c["pc_after"] <= maneuver.PC_TARGET
    assert 0 < c["dv_mps"] < 2.0                # centimetres-to-decimetres per second, not metres
    assert c["sma_drift_km"] <= maneuver.MAX_SMA_DRIFT_KM
    assert c["miss_after_km"] > 0.2


def test_ground_passes_for_a_polar_orbit_reach_every_station():
    stations = [{"station_id": "N", "name": "north", "latitude": 78.2, "longitude": 15.4, "altitude_m": 400, "min_elevation_deg": 10},
                {"station_id": "S", "name": "south", "latitude": -77.8, "longitude": 166.7, "altitude_m": 10, "min_elevation_deg": 10}]
    el = {"norad_id": 1, "epoch": datetime(2026, 10, 5), "mean_motion": 14.8, "eccentricity": 0.001, "inclination": 98.0,
          "raan": 10.0, "arg_perigee": 0.0, "mean_anomaly": 0.0, "bstar": 0.0, "mean_motion_dot": 0.0, "mean_motion_ddot": 0.0}
    p = ground.passes(el, stations, datetime(2026, 10, 5), hours=12)
    assert {x["station_id"] for x in p} == {"N", "S"}
    assert all(x["los"] >= x["aos"] and 10 <= x["max_elevation_deg"] <= 90 for x in p)
    equatorial = {**el, "inclination": 5.0}
    assert ground.passes(equatorial, stations, datetime(2026, 10, 5), hours=12) == []
