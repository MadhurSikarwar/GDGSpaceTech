"""Minimum delta-v collision-avoidance manoeuvre (Clohessy-Wiltshire + SLSQP).

Ported from OrbitalGuard (services/maneuver/app/cw_transition.py and
delta_v_optimizer.py). In the primary's RIC frame the CW equations are
linear, so an impulsive delta-v applied t seconds before TCA shifts the miss
vector at TCA by exactly Phi_rv(t) @ dv. The optimizer therefore evaluates a
closed-form 3x3 product per candidate instead of re-propagating, and SLSQP
finds the smallest |dv| that brings the predicted Pc under the target while
keeping the semi-major-axis drift (slot keeping) within bounds. CW assumes a
near-circular reference orbit, true for the LEO satellites screened here.
"""
import math

import numpy as np
from scipy.optimize import minimize

from orbitwatch.physics.collision import combined_covariance, foster_pc, ric_basis

MU = 398600.4418
MAX_DELTA_V_MPS = 20.0
MAX_SMA_DRIFT_KM = 5.0
PC_TARGET = 1e-5          # aim a decade below the CRITICAL tier
SAFETY_MARGIN = 0.9       # SLSQP meets inequalities only to tolerance: aim slightly lower


def phi_rv(n, t):
    """CW block mapping an RIC impulse (km/s) at t=0 to the RIC position shift (km) at time t."""
    nt = n * t
    s, c = math.sin(nt), math.cos(nt)
    return np.array([[s / n, (2.0 / n) * (1.0 - c), 0.0],
                     [-(2.0 / n) * (1.0 - c), (1.0 / n) * (4.0 * s - 3.0 * nt), 0.0],
                     [0.0, 0.0, s / n]])


def sma_drift_km(dv_intrack_km_s, n):
    """First-order semi-major-axis change from an in-track impulse on a near-circular orbit."""
    return 2.0 * dv_intrack_km_s / n


def direction_label(dv_ric):
    r, i, c = dv_ric
    mag, axis, signed = max((abs(r), "R", r), (abs(i), "I", i), (abs(c), "C", c))
    return {"I": "POSIGRADE" if signed >= 0 else "RETROGRADE", "C": "NORMAL" if signed >= 0 else "ANTINORMAL",
            "R": "RADIAL OUT" if signed >= 0 else "RADIAL IN"}[axis]


class Encounter:
    """Everything the optimizer needs about one encounter at TCA (TEME, km, km/s)."""

    def __init__(self, p_pos, p_vel, s_pos, s_vel, p_age_h, s_age_h, p_type, s_type, drag_scalar, hbr_m):
        p_pos, p_vel = np.asarray(p_pos, float), np.asarray(p_vel, float)
        r, v = np.linalg.norm(p_pos), np.linalg.norm(p_vel)
        a = 1.0 / (2.0 / r - v * v / MU)
        self.n = math.sqrt(MU / a ** 3)
        self.rot = ric_basis(p_pos, p_vel)
        self.cov = combined_covariance(p_pos, p_vel, p_age_h, p_type, s_pos, s_vel, s_age_h, s_type, drag_scalar)
        self.rel_pos = p_pos - np.asarray(s_pos, float)
        self.rel_vel = p_vel - np.asarray(s_vel, float)
        self.hbr_km = hbr_m / 1000.0

    def shifted(self, dv_ric, dt):
        return self.rel_pos + self.rot @ (phi_rv(self.n, dt) @ np.asarray(dv_ric, float))

    def pc_after(self, dv_ric, dt):
        return foster_pc(self.shifted(dv_ric, dt), self.rel_vel, self.cov, self.hbr_km)[0]

    def miss_after(self, dv_ric, dt):
        return float(np.linalg.norm(self.shifted(dv_ric, dt)))


def optimise(enc, dt, pc_target=PC_TARGET, max_dv_mps=MAX_DELTA_V_MPS, max_drift_km=MAX_SMA_DRIFT_KM):
    """Smallest RIC delta-v (applied dt seconds before TCA). Returns a candidate dict."""
    limit = max_dv_mps / 1000.0
    # Warm start: a small in-track nudge on the side that opens the miss distance.
    rel_ric = enc.rot.T @ enc.rel_pos
    sign = np.sign(np.dot(rel_ric, phi_rv(enc.n, dt)[:, 1])) or 1.0
    x0 = np.array([0.0, sign * 1e-4, 0.0])
    res = minimize(lambda dv: float(np.linalg.norm(dv)), x0, method="SLSQP",
                   bounds=[(-limit, limit)] * 3,
                   constraints=[{"type": "ineq", "fun": lambda dv: pc_target * SAFETY_MARGIN - enc.pc_after(dv, dt)},
                                {"type": "ineq", "fun": lambda dv: max_drift_km - abs(sma_drift_km(dv[1], enc.n))}],
                   options={"maxiter": 200, "ftol": 1e-12})
    dv = res.x
    pc = enc.pc_after(dv, dt)
    drift = abs(sma_drift_km(dv[1], enc.n))
    return {"converged": bool(res.success), "dv_ric_mps": [float(x * 1000.0) for x in dv],
            "dv_mps": float(np.linalg.norm(dv) * 1000.0), "direction": direction_label(dv),
            "pc_after": pc, "miss_after_km": enc.miss_after(dv, dt), "sma_drift_km": drift,
            "solver_message": str(res.message)}
