import numpy as np
import pytest

from services.propagation.app.physics.covariance import (
    ric_basis,
    empirical_position_covariance_ric_km2,
    position_covariance_teme_km2,
    combined_covariance_teme_km2,
)

# A representative circular-ish LEO state (~500 km altitude), TEME km / km/s.
LEO_POSITION_KM = [6878.0, 0.0, 0.0]
LEO_VELOCITY_KM_S = [0.0, 7.6126, 0.0]


def test_ric_basis_is_orthonormal_right_handed():
    rot = ric_basis(LEO_POSITION_KM, LEO_VELOCITY_KM_S)
    assert rot.shape == (3, 3)
    np.testing.assert_allclose(rot.T @ rot, np.eye(3), atol=1e-10)
    assert np.linalg.det(rot) == pytest.approx(1.0, abs=1e-9)


def test_intrack_sigma_dominates_and_grows_with_age():
    cov_0h = empirical_position_covariance_ric_km2(data_age_hours=0.0)
    cov_48h = empirical_position_covariance_ric_km2(data_age_hours=48.0)

    sigma_r0, sigma_i0, sigma_c0 = np.sqrt(np.diag(cov_0h))
    sigma_r48, sigma_i48, sigma_c48 = np.sqrt(np.diag(cov_48h))

    # In-track starts comparable to or larger than the other axes and grows
    # by far the most over 48h of TLE age -- it's the drag-mismodeling axis.
    assert sigma_i48 > sigma_i0
    assert (sigma_i48 - sigma_i0) > (sigma_r48 - sigma_r0)
    assert (sigma_i48 - sigma_i0) > (sigma_c48 - sigma_c0)
    assert sigma_i48 > sigma_r48
    assert sigma_i48 > sigma_c48

    # Never zero even at age=0 -- an OD/TLE fit is never perfectly certain.
    assert sigma_r0 > 0 and sigma_i0 > 0 and sigma_c0 > 0


def test_drag_activity_scalar_only_inflates_intrack_and_is_monotonic():
    quiet = empirical_position_covariance_ric_km2(data_age_hours=24.0, drag_activity_scalar=1.0)
    storm = empirical_position_covariance_ric_km2(data_age_hours=24.0, drag_activity_scalar=4.0)

    sigma_r_q, sigma_i_q, sigma_c_q = np.sqrt(np.diag(quiet))
    sigma_r_s, sigma_i_s, sigma_c_s = np.sqrt(np.diag(storm))

    assert sigma_i_s > sigma_i_q
    # Radial/cross-track are not modeled as drag-sensitive.
    assert sigma_r_s == pytest.approx(sigma_r_q)
    assert sigma_c_s == pytest.approx(sigma_c_q)

    # A scalar below the quiet-sun floor (1.0) must not shrink uncertainty.
    floored = empirical_position_covariance_ric_km2(data_age_hours=24.0, drag_activity_scalar=0.2)
    assert np.diag(floored)[1] == pytest.approx(np.diag(quiet)[1])


def test_debris_inflated_relative_to_tracked_satellite():
    sat_cov = empirical_position_covariance_ric_km2(data_age_hours=24.0, object_type="SATELLITE")
    debris_cov = empirical_position_covariance_ric_km2(data_age_hours=24.0, object_type="DEBRIS")
    assert np.all(np.diag(debris_cov) > np.diag(sat_cov))


def test_position_covariance_teme_is_symmetric_positive_definite():
    cov = position_covariance_teme_km2(LEO_POSITION_KM, LEO_VELOCITY_KM_S, data_age_hours=12.0)
    np.testing.assert_allclose(cov, cov.T, atol=1e-12)
    eigvals = np.linalg.eigvalsh(cov)
    assert np.all(eigvals > 0)


def test_combined_covariance_is_sum_of_independent_marginals():
    cov_p = position_covariance_teme_km2(LEO_POSITION_KM, LEO_VELOCITY_KM_S, data_age_hours=6.0, object_type="SATELLITE")
    # A slightly different secondary orbit (still LEO-ish) so the two RIC frames differ.
    secondary_pos = [6878.0, 200.0, 100.0]
    secondary_vel = [-0.3, 7.55, 0.4]
    cov_s = position_covariance_teme_km2(secondary_pos, secondary_vel, data_age_hours=30.0, object_type="DEBRIS")

    combined = combined_covariance_teme_km2(
        LEO_POSITION_KM, LEO_VELOCITY_KM_S, 6.0, "SATELLITE",
        secondary_pos, secondary_vel, 30.0, "DEBRIS",
    )
    np.testing.assert_allclose(combined, cov_p + cov_s, atol=1e-12)
