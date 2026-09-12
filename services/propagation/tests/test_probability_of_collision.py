import math
import numpy as np
import pytest
from scipy.stats import ncx2

from services.propagation.app.physics.probability_of_collision import (
    encounter_plane_basis,
    project_to_encounter_plane,
    foster_2d_pc,
    compute_pc,
)


def test_encounter_plane_basis_is_orthonormal_and_perpendicular_to_velocity():
    v = np.array([1.2, -3.4, 5.6])
    e1, e2 = encounter_plane_basis(v)
    v_hat = v / np.linalg.norm(v)
    assert np.dot(e1, v_hat) == pytest.approx(0.0, abs=1e-12)
    assert np.dot(e2, v_hat) == pytest.approx(0.0, abs=1e-12)
    assert np.dot(e1, e2) == pytest.approx(0.0, abs=1e-12)
    assert np.linalg.norm(e1) == pytest.approx(1.0)
    assert np.linalg.norm(e2) == pytest.approx(1.0)


def test_encounter_plane_basis_rejects_zero_relative_velocity():
    with pytest.raises(ValueError):
        encounter_plane_basis([0.0, 0.0, 0.0])


def test_isotropic_covariance_projects_to_isotropic_2d_with_same_variance():
    sigma2 = 0.7
    cov_3d = np.eye(3) * sigma2
    rel_pos = np.array([0.0, 0.0, 0.0])
    rel_vel = np.array([3.0, -1.0, 2.0])
    miss_2d, cov_2d = project_to_encounter_plane(rel_pos, cov_3d, rel_vel)
    np.testing.assert_allclose(miss_2d, [0.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(cov_2d, np.eye(2) * sigma2, atol=1e-12)


def test_relative_position_along_velocity_projects_to_zero_miss_vector():
    # A miss vector purely along the approach direction carries no
    # information about lateral offset -- the encounter plane should discard it.
    rel_vel = np.array([0.0, 0.0, 5.0])
    rel_pos = np.array([0.0, 0.0, 12.3])  # pure along-track separation
    cov_3d = np.eye(3) * 0.5
    miss_2d, _ = project_to_encounter_plane(rel_pos, cov_3d, rel_vel)
    np.testing.assert_allclose(miss_2d, [0.0, 0.0], atol=1e-9)


def test_pc_exact_reference_case_zero_miss_circular_covariance():
    # Textbook closed form for d=0: Pc = 1 - exp(-R^2 / (2*sigma^2)).
    sigma_km = 1.0
    hbr_km = 0.02
    expected = 1.0 - math.exp(-(hbr_km ** 2) / (2.0 * sigma_km ** 2))
    assert expected == pytest.approx(1.9998e-4, rel=1e-3)

    pc, abserr = foster_2d_pc([0.0, 0.0], np.eye(2) * sigma_km ** 2, hbr_km)
    assert pc == pytest.approx(expected, rel=1e-3)
    assert abserr < max(1e-10, 0.01 * pc)


@pytest.mark.parametrize(
    "miss_km,sigma_km,hbr_km",
    [
        (0.0, 1.0, 0.02),
        (0.5, 1.0, 0.05),
        (1.0, 0.3, 0.02),
        (2.0, 0.5, 0.05),
        (0.1, 0.05, 0.02),
        (5.0, 1.0, 0.02),
    ],
)
def test_pc_matches_noncentral_chi_squared_closed_form_for_circular_covariance(miss_km, sigma_km, hbr_km):
    # For independent equal-variance Gaussians, (X/sigma)^2+(Y/sigma)^2 is
    # exactly noncentral chi-squared (df=2, nc=(d/sigma)^2) -- an exact,
    # library-backed cross-check independent of the dblquad/polar machinery
    # under test, covering roughly the 1e-2..1e-8 Pc range via the parametrized
    # (miss, sigma, hbr) combinations above.
    expected = ncx2.cdf((hbr_km / sigma_km) ** 2, df=2, nc=(miss_km / sigma_km) ** 2)

    pc, abserr = foster_2d_pc([miss_km, 0.0], np.eye(2) * sigma_km ** 2, hbr_km)

    assert abserr < max(1e-12, 0.01 * max(pc, 1e-10))
    if expected < 1e-12:
        assert pc < 1e-9
    else:
        assert pc == pytest.approx(expected, rel=0.02)


def test_pc_elliptical_covariance_matches_monte_carlo():
    rng = np.random.default_rng(42)
    miss = np.array([0.3, -0.15])
    cov = np.array([[0.6 ** 2, 0.05], [0.05, 0.15 ** 2]])  # elliptical, off-diagonal correlation
    hbr_km = 0.25

    pc, abserr = foster_2d_pc(miss, cov, hbr_km)

    samples = rng.multivariate_normal(miss, cov, size=2_000_000)
    inside = np.sum(samples[:, 0] ** 2 + samples[:, 1] ** 2 <= hbr_km ** 2)
    mc_pc = inside / samples.shape[0]
    mc_stderr = math.sqrt(mc_pc * (1 - mc_pc) / samples.shape[0])

    assert abs(pc - mc_pc) < max(5 * mc_stderr, 1e-4)


def test_pc_decreases_monotonically_with_increasing_miss_distance():
    sigma_km = 0.4
    hbr_km = 0.02
    cov = np.eye(2) * sigma_km ** 2
    misses = [0.0, 0.1, 0.3, 0.6, 1.0, 2.0]
    pcs = [foster_2d_pc([m, 0.0], cov, hbr_km)[0] for m in misses]
    assert all(pcs[i] > pcs[i + 1] for i in range(len(pcs) - 1))


def test_compute_pc_end_to_end_with_realistic_leo_conjunction_geometry():
    # A representative close approach: ~1 km lateral miss, few-km/s crossing.
    rel_pos_km = np.array([0.8, -0.3, 0.1])
    rel_vel_km_s = np.array([1.2, 2.5, -0.6])
    combined_cov = np.diag([0.4, 0.4, 0.4]) ** 2
    pc, abserr = compute_pc(rel_pos_km, rel_vel_km_s, combined_cov, combined_hbr_km=0.02)
    assert 0.0 <= pc <= 1.0
    assert abserr < max(1e-10, 0.05 * pc)
