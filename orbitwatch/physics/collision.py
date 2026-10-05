"""Probability of collision (Foster 1992, 2D) with an element-set-age covariance proxy.

Ported from OrbitalGuard (services/propagation/app/physics/covariance.py and
probability_of_collision.py). Public element sets carry no orbit-determination
covariance, so position uncertainty is modelled from what an element set does
expose: its age at TCA, the object type, and how active the thermosphere is.
Growth is anisotropic in the RIC (radial / in-track / cross-track) frame: the
in-track error dominates because unmodelled drag accumulates along track.
The coefficients are an illustrative calibration of the right order of
magnitude, not a fitted model: Pc values are screening-grade, not operational.

Pc integrates the bivariate Gaussian of the relative position, projected on
the encounter plane (perpendicular to the relative velocity at TCA), over a
disk of the combined hard-body radius. Integration is done in polar
coordinates, where the disk is a rectangle, which keeps adaptive quadrature
well behaved.
"""
import math

import numpy as np
from scipy.integrate import dblquad

PC_METHOD = "FOSTER_2D_ELSET_AGE_COVARIANCE"
DEFAULT_HARD_BODY_RADIUS_M = 20.0

_SIGMA0 = np.array([0.05, 0.10, 0.05])          # km at zero age (R, I, C)
_GROWTH = np.array([0.01, 0.12, 0.015])         # km per hour of element-set age
_TYPE_INFLATION = {"Payload": 1.0, "Rocket Body": 1.3, "Debris": 1.6, "Unknown": 1.6}

# Pc tiers used by OrbitalGuard's risk scoring (and common operational practice).
PC_TIERS = ((1e-4, "CRITICAL"), (1e-5, "HIGH"), (1e-6, "MEDIUM"))


def ric_basis(position_km, velocity_km_s):
    """3x3 matrix whose columns are the R, I, C unit vectors expressed in the input (TEME) frame."""
    r = np.asarray(position_km, dtype=float)
    v = np.asarray(velocity_km_s, dtype=float)
    r_hat = r / np.linalg.norm(r)
    c_hat = np.cross(r, v)
    c_hat /= np.linalg.norm(c_hat)
    i_hat = np.cross(c_hat, r_hat)
    return np.column_stack([r_hat, i_hat, c_hat])


def ric_sigmas_km(age_hours, object_type="Payload", drag_scalar=1.0):
    age = max(0.0, float(age_hours))
    growth = _GROWTH * np.array([1.0, max(1.0, float(drag_scalar)), 1.0])
    return (_SIGMA0 + growth * age) * _TYPE_INFLATION.get(object_type, 1.6)


def position_covariance_teme(position_km, velocity_km_s, age_hours, object_type, drag_scalar=1.0):
    rot = ric_basis(position_km, velocity_km_s)
    return rot @ np.diag(ric_sigmas_km(age_hours, object_type, drag_scalar) ** 2) @ rot.T


def combined_covariance(p_pos, p_vel, p_age, p_type, s_pos, s_vel, s_age, s_type, drag_scalar=1.0):
    """Relative-position covariance assuming independent tracking errors (sum of the two)."""
    return (position_covariance_teme(p_pos, p_vel, p_age, p_type, drag_scalar)
            + position_covariance_teme(s_pos, s_vel, s_age, s_type, drag_scalar))


def encounter_plane(relative_velocity_km_s):
    v = np.asarray(relative_velocity_km_s, dtype=float)
    speed = np.linalg.norm(v)
    if speed < 1e-9:
        raise ValueError("relative velocity is ~0: the encounter plane is undefined")
    v_hat = v / speed
    ref = np.array([0.0, 0.0, 1.0]) if abs(v_hat[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = ref - np.dot(ref, v_hat) * v_hat
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(v_hat, e1)


def _integrand(r, theta, mx, my, l1, l2):
    x, y = r * math.cos(theta), r * math.sin(theta)
    return (r / (2.0 * math.pi * math.sqrt(l1 * l2))) * math.exp(-0.5 * ((x - mx) ** 2 / l1 + (y - my) ** 2 / l2))


def foster_pc(relative_position_km, relative_velocity_km_s, covariance_km2, hard_body_radius_km):
    """Returns (pc, miss_2d_km, sigma_2d_km)."""
    e1, e2 = encounter_plane(relative_velocity_km_s)
    proj = np.vstack([e1, e2])
    miss = proj @ np.asarray(relative_position_km, dtype=float)
    cov = proj @ np.asarray(covariance_km2, dtype=float) @ proj.T
    eigval, eigvec = np.linalg.eigh(cov)
    eigval = np.clip(eigval, 1e-12, None)
    m = eigvec.T @ miss
    pc, _err = dblquad(_integrand, 0.0, 2.0 * math.pi, lambda _t: 0.0, lambda _t: hard_body_radius_km,
                       args=(float(m[0]), float(m[1]), float(eigval[0]), float(eigval[1])), epsabs=1e-14, epsrel=1e-8)
    return float(min(max(pc, 0.0), 1.0)), miss, np.sqrt(eigval)


def pc_tier(pc):
    if pc is None:
        return None
    for limit, tier in PC_TIERS:
        if pc >= limit:
            return tier
    return "LOW"


def encounter_pc(p_state, s_state, p_age_h, s_age_h, p_type, s_type, drag_scalar=1.0,
                 hard_body_radius_m=DEFAULT_HARD_BODY_RADIUS_M):
    """Pc for two (position km, velocity km/s) TEME states at TCA. Returns a dict."""
    (p_pos, p_vel), (s_pos, s_vel) = p_state, s_state
    rel_pos = np.asarray(p_pos) - np.asarray(s_pos)
    rel_vel = np.asarray(p_vel) - np.asarray(s_vel)
    cov = combined_covariance(p_pos, p_vel, p_age_h, p_type, s_pos, s_vel, s_age_h, s_type, drag_scalar)
    pc, miss_2d, sigma_2d = foster_pc(rel_pos, rel_vel, cov, hard_body_radius_m / 1000.0)
    return {"pc": pc, "method": PC_METHOD, "hard_body_radius_m": hard_body_radius_m,
            "miss_km": float(np.linalg.norm(rel_pos)), "encounter_plane_miss_km": [float(x) for x in miss_2d],
            "encounter_plane_sigma_km": [float(x) for x in sigma_2d],
            "primary_ric_sigma_km": [float(x) for x in ric_sigmas_km(p_age_h, p_type, drag_scalar)],
            "secondary_ric_sigma_km": [float(x) for x in ric_sigmas_km(s_age_h, s_type, drag_scalar)],
            "drag_scalar": drag_scalar, "tier": pc_tier(pc)}
