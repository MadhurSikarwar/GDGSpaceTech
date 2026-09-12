import math
import numpy as np
import pytest

from services.maneuver.app.cw_transition import (
    MU_EARTH_KM3_S2,
    mean_motion_from_semi_major_axis,
    cw_state_transition_matrix,
    miss_vector_shift_from_impulse,
    semi_major_axis_drift_km,
)

ISS_ALTITUDE_KM = 420.0
EARTH_RADIUS_KM = 6378.137
ISS_SMA_KM = EARTH_RADIUS_KM + ISS_ALTITUDE_KM


def test_mean_motion_matches_known_iss_period():
    n = mean_motion_from_semi_major_axis(ISS_SMA_KM)
    period_minutes = (2 * math.pi / n) / 60.0
    # Real ISS orbital period is ~92-93 minutes.
    assert 90.0 <= period_minutes <= 95.0


def test_stm_at_zero_time_is_identity():
    n = mean_motion_from_semi_major_axis(ISS_SMA_KM)
    phi = cw_state_transition_matrix(n, 0.0)
    np.testing.assert_allclose(phi, np.eye(6), atol=1e-9)


def test_zero_impulse_produces_zero_shift():
    n = mean_motion_from_semi_major_axis(ISS_SMA_KM)
    shift = miss_vector_shift_from_impulse(np.zeros(3), n, time_to_tca_s=600.0)
    np.testing.assert_allclose(shift, np.zeros(3), atol=1e-12)


def test_impulse_shift_is_linear_in_delta_v():
    n = mean_motion_from_semi_major_axis(ISS_SMA_KM)
    dv = np.array([0.001, -0.002, 0.0015])
    shift1 = miss_vector_shift_from_impulse(dv, n, time_to_tca_s=900.0)
    shift2 = miss_vector_shift_from_impulse(2.0 * dv, n, time_to_tca_s=900.0)
    np.testing.assert_allclose(shift2, 2.0 * shift1, rtol=1e-9)


def test_along_track_impulse_matches_independent_vis_viva_sma_change():
    # Independent cross-check via vis-viva rather than re-deriving the same
    # CW formula: for a circular orbit at radius r with circular speed
    # v=sqrt(mu/r), a small purely-tangential burn changes specific energy
    # to eps' = (v+dv)^2/2 - mu/r, giving a' = -mu/(2*eps'). For a SMALL dv
    # this should match the CW first-order formula da ~= 2*dv/n.
    r_km = ISS_SMA_KM
    n = mean_motion_from_semi_major_axis(r_km)
    v = math.sqrt(MU_EARTH_KM3_S2 / r_km)
    dv_t = 0.001  # km/s -- small relative to v (~7.6 km/s), keeps linearization valid

    eps_new = (v + dv_t) ** 2 / 2.0 - MU_EARTH_KM3_S2 / r_km
    a_new = -MU_EARTH_KM3_S2 / (2.0 * eps_new)
    vis_viva_da = a_new - r_km

    cw_da = semi_major_axis_drift_km(dv_t, n)
    assert cw_da == pytest.approx(vis_viva_da, rel=1e-3)


def test_radial_and_crosstrack_impulses_shift_position_differently_than_intrack():
    # Not a claim that radial/cross-track impulses are "zero effect" --
    # they clearly shift the miss vector (that's exactly why the optimizer
    # is allowed to use all 3 components) -- only that they are NOT what the
    # semi-major-axis/slot-keeping constraint should be evaluated against.
    # This test documents that the three response directions are genuinely
    # distinct rather than degenerate/redundant with each other.
    n = mean_motion_from_semi_major_axis(ISS_SMA_KM)
    t = 900.0
    shift_radial = miss_vector_shift_from_impulse(np.array([0.001, 0, 0]), n, t)
    shift_intrack = miss_vector_shift_from_impulse(np.array([0, 0.001, 0]), n, t)
    shift_crosstrack = miss_vector_shift_from_impulse(np.array([0, 0, 0.001]), n, t)

    assert not np.allclose(shift_radial, shift_intrack)
    assert not np.allclose(shift_radial, shift_crosstrack)
    # Cross-track motion stays purely in the cross-track (z) axis in this
    # linearized model -- it never couples into the radial/in-track plane.
    assert shift_crosstrack[0] == pytest.approx(0.0, abs=1e-12)
    assert shift_crosstrack[1] == pytest.approx(0.0, abs=1e-12)
    assert abs(shift_crosstrack[2]) > 0
