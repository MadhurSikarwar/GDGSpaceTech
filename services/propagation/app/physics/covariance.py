"""
Empirical TLE-age-driven position covariance proxy.

TLEs carry no orbit-determination covariance. Genuine Pc computation needs a
credible position-uncertainty ellipsoid per object; absent real OD covariance,
this module builds one from what a TLE *does* expose: how old the element set
is relative to the epoch being evaluated, and, for LEO objects, how active
the thermosphere currently is (a live NOAA-derived drag_activity_scalar --
see services/propagation/app/spaceweather/). This is the standard fallback
technique used when true covariance isn't available (Vallado-style
"covariance realism" scaling), not a real OD product -- every downstream
consumer must treat resulting Pc values as illustrative, not operational.

Growth is anisotropic in the RIC (Radial / In-track / Cross-track) frame:
along-track error dominates because it accumulates from unmodeled/mismodeled
atmospheric drag (a secular effect), while radial and cross-track errors stay
comparatively small and grow more slowly. This qualitative ordering is well
established in TLE-accuracy literature; the specific coefficients below are
an illustrative calibration (same order of magnitude as published TLE
covariance-realism studies), not a validated fit to a specific dataset.
"""

from typing import Sequence
import numpy as np

# RIC baseline sigma (km) at zero TLE age, and growth rate per hour.
# In-track dominates and is the only axis scaled by drag activity.
_SIGMA0_RADIAL_KM = 0.05
_SIGMA0_INTRACK_KM = 0.10
_SIGMA0_CROSSTRACK_KM = 0.05
_GROWTH_RADIAL_KM_PER_HR = 0.01
_GROWTH_INTRACK_KM_PER_HR = 0.12
_GROWTH_CROSSTRACK_KM_PER_HR = 0.015

# Debris/rocket bodies are tracked less precisely than active cataloged
# satellites (weaker radar cross-section, less frequent re-tracking) -- a
# flat inflation on top of the age-based growth above.
_OBJECT_TYPE_INFLATION = {
    "SATELLITE": 1.0,
    "SYNTHETIC_DEBRIS": 1.0,  # engineered for the demo; treat like a tracked object
    "DEBRIS": 1.6,
    "ROCKET_BODY": 1.3,
    "UNKNOWN": 1.6,
}


def ric_basis(position_km: Sequence[float], velocity_km_s: Sequence[float]) -> np.ndarray:
    """
    3x3 rotation matrix whose COLUMNS are the unit vectors [R, I, C]
    (Radial, In-track, Cross-track -- Vallado's RSW frame) expressed in the
    same inertial frame (TEME) as the input position/velocity. Multiplying a
    RIC-frame vector by this matrix expresses it in TEME.
    """
    r = np.asarray(position_km, dtype=float)
    v = np.asarray(velocity_km_s, dtype=float)
    r_hat = r / np.linalg.norm(r)
    h = np.cross(r, v)
    c_hat = h / np.linalg.norm(h)   # cross-track = orbit-normal
    i_hat = np.cross(c_hat, r_hat)  # in-track completes the right-handed triad
    return np.column_stack([r_hat, i_hat, c_hat])


def empirical_position_covariance_ric_km2(
    data_age_hours: float,
    object_type: str = "SATELLITE",
    drag_activity_scalar: float = 1.0,
) -> np.ndarray:
    """
    3x3 diagonal position covariance (km^2) in the RIC frame: an empirical
    TLE-age proxy, NOT a true OD covariance (see module docstring).
    """
    age = max(0.0, float(data_age_hours))
    inflation = _OBJECT_TYPE_INFLATION.get(object_type, _OBJECT_TYPE_INFLATION["UNKNOWN"])
    scalar = max(1.0, float(drag_activity_scalar))

    sigma_r = (_SIGMA0_RADIAL_KM + _GROWTH_RADIAL_KM_PER_HR * age) * inflation
    sigma_i = (_SIGMA0_INTRACK_KM + _GROWTH_INTRACK_KM_PER_HR * age * scalar) * inflation
    sigma_c = (_SIGMA0_CROSSTRACK_KM + _GROWTH_CROSSTRACK_KM_PER_HR * age) * inflation

    return np.diag([sigma_r ** 2, sigma_i ** 2, sigma_c ** 2])


def position_covariance_teme_km2(
    position_km: Sequence[float],
    velocity_km_s: Sequence[float],
    data_age_hours: float,
    object_type: str = "SATELLITE",
    drag_activity_scalar: float = 1.0,
) -> np.ndarray:
    """Empirical position covariance (km^2), rotated from RIC into the TEME frame."""
    ric_cov = empirical_position_covariance_ric_km2(data_age_hours, object_type, drag_activity_scalar)
    rot = ric_basis(position_km, velocity_km_s)
    return rot @ ric_cov @ rot.T


def combined_covariance_teme_km2(
    primary_position_km: Sequence[float],
    primary_velocity_km_s: Sequence[float],
    primary_age_hours: float,
    primary_type: str,
    secondary_position_km: Sequence[float],
    secondary_velocity_km_s: Sequence[float],
    secondary_age_hours: float,
    secondary_type: str,
    drag_activity_scalar: float = 1.0,
) -> np.ndarray:
    """
    Combined (primary + secondary) position covariance in TEME, assuming
    independent tracking errors -- the standard Pc assumption (Foster/Akella):
    the uncertainty that matters for a collision check is the uncertainty of
    the RELATIVE position, whose covariance is the sum of the two
    independent marginal covariances.
    """
    cov_p = position_covariance_teme_km2(
        primary_position_km, primary_velocity_km_s, primary_age_hours, primary_type, drag_activity_scalar
    )
    cov_s = position_covariance_teme_km2(
        secondary_position_km, secondary_velocity_km_s, secondary_age_hours, secondary_type, drag_activity_scalar
    )
    return cov_p + cov_s
