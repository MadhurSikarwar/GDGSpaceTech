"""
Foster (1992) 2D probability-of-collision method: project the combined
position-covariance ellipsoid and the miss vector onto the "encounter plane"
(the 2D plane perpendicular to the relative-velocity vector at TCA), then
integrate the resulting bivariate Gaussian over a disk of radius equal to the
combined hard-body radius. Valid under the standard short-encounter
assumptions -- near-linear relative motion around TCA and a covariance that
does not change materially over the encounter window -- both of which hold
for the conjunction windows this pipeline screens (seconds to a couple of
minutes around TCA).

Integration is performed in POLAR coordinates centered on the disk (not
Cartesian x/y): the disk boundary in polar form is the constant r = HBR, a
plain rectangle in (r, theta) space, which is numerically well-behaved for
scipy.integrate.dblquad. The Cartesian form's boundary (y = +-sqrt(R^2-x^2))
has infinite slope at the disk's edges, a known source of slow convergence
and spurious round-off warnings from adaptive quadrature.
"""

import math
from typing import Sequence, Tuple
import numpy as np
from scipy.integrate import dblquad

PC_METHOD = "FOSTER_2D_TLE_AGE_COVARIANCE"


def encounter_plane_basis(relative_velocity_km_s: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    """Two orthonormal vectors spanning the plane perpendicular to relative velocity."""
    v = np.asarray(relative_velocity_km_s, dtype=float)
    speed = np.linalg.norm(v)
    if speed < 1e-9:
        raise ValueError("Relative velocity is ~zero; encounter plane is undefined.")
    v_hat = v / speed
    reference = np.array([0.0, 0.0, 1.0]) if abs(v_hat[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = reference - np.dot(reference, v_hat) * v_hat
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(v_hat, e1)
    return e1, e2


def project_to_encounter_plane(
    relative_position_km: Sequence[float],
    combined_covariance_km2: np.ndarray,
    relative_velocity_km_s: Sequence[float],
) -> Tuple[np.ndarray, np.ndarray]:
    """Project the 3D relative-position vector and covariance onto the 2D encounter plane."""
    e1, e2 = encounter_plane_basis(relative_velocity_km_s)
    projection = np.vstack([e1, e2])  # 2x3
    miss_2d = projection @ np.asarray(relative_position_km, dtype=float)
    cov_2d = projection @ np.asarray(combined_covariance_km2, dtype=float) @ projection.T
    return miss_2d, cov_2d


def _pc_integrand(r: float, theta: float, mx: float, my: float, lam1: float, lam2: float) -> float:
    x = r * math.cos(theta)
    y = r * math.sin(theta)
    exponent = -0.5 * (((x - mx) ** 2) / lam1 + ((y - my) ** 2) / lam2)
    return (r / (2.0 * math.pi * math.sqrt(lam1 * lam2))) * math.exp(exponent)


def foster_2d_pc(
    miss_vector_2d_km: Sequence[float],
    covariance_2d_km2: np.ndarray,
    combined_hbr_km: float,
    epsabs: float = 1e-14,
    epsrel: float = 1e-8,
) -> Tuple[float, float]:
    """
    Numerically integrate the 2D Gaussian (mean = miss_vector_2d_km,
    covariance = covariance_2d_km2) over a disk of radius combined_hbr_km
    centered at the origin. Returns (pc, quadrature_abserr).
    """
    cov = np.asarray(covariance_2d_km2, dtype=float)
    eigvals, eigvecs = np.linalg.eigh(cov)
    # Numerical floor: a covariance singular to machine precision would divide
    # by ~0 below. Real (even empirical-proxy) covariance is never exactly
    # singular; this only guards against degenerate test inputs.
    eigvals = np.clip(eigvals, 1e-12, None)
    lam1, lam2 = float(eigvals[0]), float(eigvals[1])
    mean_rot = eigvecs.T @ np.asarray(miss_vector_2d_km, dtype=float)
    mx, my = float(mean_rot[0]), float(mean_rot[1])

    pc, abserr = dblquad(
        _pc_integrand,
        0.0, 2.0 * math.pi,
        lambda _theta: 0.0, lambda _theta: combined_hbr_km,
        args=(mx, my, lam1, lam2),
        epsabs=epsabs, epsrel=epsrel,
    )
    return float(pc), float(abserr)


def compute_pc(
    relative_position_km: Sequence[float],
    relative_velocity_km_s: Sequence[float],
    combined_covariance_teme_km2: np.ndarray,
    combined_hbr_km: float,
) -> Tuple[float, float]:
    """End-to-end: project onto the encounter plane, then integrate. Returns (pc, abserr)."""
    miss_2d, cov_2d = project_to_encounter_plane(
        relative_position_km, combined_covariance_teme_km2, relative_velocity_km_s
    )
    return foster_2d_pc(miss_2d, cov_2d, combined_hbr_km)
