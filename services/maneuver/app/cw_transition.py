"""
Clohessy-Wiltshire (CW / Hill) linearized relative-motion equations, in the
primary's RIC frame (Radial / In-track / Cross-track -- same axis ordering
and convention as services/propagation/app/physics/covariance.py's
ric_basis(), so a vector computed there and one computed here compose
directly without a re-derivation).

Assumes a near-circular reference (primary) orbit -- true for the ISS
(e~0.0006) and effectively every catalog LEO object this pipeline screens,
but a materially eccentric secondary/reference orbit would need the fuller
Tschauner-Hempel equations instead. Stated here rather than silently assumed
away.

The one formula the optimizer actually needs: because CW is LINEAR, an
impulsive delta-v applied at the burn time shifts the relative position at
any later time t by exactly Phi_rv(t) @ delta_v, independent of whatever the
pre-burn trajectory already was. That means the optimizer never has to
re-propagate the whole encounter with scipy -- it only ever evaluates this
one closed-form 3x3 matrix-vector product per candidate delta-v, which is
what makes an SLSQP inner loop over delta-v cheap enough to run interactively.
"""

import math
from typing import Tuple

import numpy as np

MU_EARTH_KM3_S2 = 398600.4418


def mean_motion_from_semi_major_axis(semi_major_axis_km: float) -> float:
    """Kepler's third law: n = sqrt(mu / a^3), rad/s."""
    return math.sqrt(MU_EARTH_KM3_S2 / (semi_major_axis_km ** 3))


def cw_state_transition_matrix(n: float, t: float) -> np.ndarray:
    """
    6x6 CW state-transition matrix Phi(t) such that
    [delta_r(t); delta_v(t)] = Phi(t) @ [delta_r0; delta_v0]
    in the RIC frame ([radial, in-track, cross-track] ordering).
    Standard closed-form solution (Clohessy & Wiltshire 1960 / Vallado).
    """
    nt = n * t
    s, c = math.sin(nt), math.cos(nt)

    phi_rr = np.array([
        [4.0 - 3.0 * c, 0.0, 0.0],
        [6.0 * (s - nt), 1.0, 0.0],
        [0.0, 0.0, c],
    ])
    phi_rv = np.array([
        [s / n, (2.0 / n) * (1.0 - c), 0.0],
        [-(2.0 / n) * (1.0 - c), (1.0 / n) * (4.0 * s - 3.0 * nt), 0.0],
        [0.0, 0.0, s / n],
    ])
    phi_vr = np.array([
        [3.0 * n * s, 0.0, 0.0],
        [-6.0 * n * (1.0 - c), 0.0, 0.0],
        [0.0, 0.0, -n * s],
    ])
    phi_vv = np.array([
        [c, 2.0 * s, 0.0],
        [-2.0 * s, 4.0 * c - 3.0, 0.0],
        [0.0, 0.0, c],
    ])

    phi = np.zeros((6, 6))
    phi[0:3, 0:3] = phi_rr
    phi[0:3, 3:6] = phi_rv
    phi[3:6, 0:3] = phi_vr
    phi[3:6, 3:6] = phi_vv
    return phi


def position_response_matrix(n: float, t: float) -> np.ndarray:
    """
    The Phi_rv(t) block alone: maps a delta-v impulse (RIC, km/s) applied at
    t=0 to the resulting shift in relative position (RIC, km) at time t.
    This is the only piece of the CW solution the optimizer needs -- see
    module docstring.
    """
    nt = n * t
    s, c = math.sin(nt), math.cos(nt)
    return np.array([
        [s / n, (2.0 / n) * (1.0 - c), 0.0],
        [-(2.0 / n) * (1.0 - c), (1.0 / n) * (4.0 * s - 3.0 * nt), 0.0],
        [0.0, 0.0, s / n],
    ])


def miss_vector_shift_from_impulse(delta_v_ric_km_s: np.ndarray, n: float, time_to_tca_s: float) -> np.ndarray:
    """Shift in the TCA relative-position vector (RIC, km) caused by delta_v_ric_km_s applied now."""
    return position_response_matrix(n, time_to_tca_s) @ np.asarray(delta_v_ric_km_s, dtype=float)


def semi_major_axis_drift_km(delta_v_intrack_km_s: float, n: float) -> float:
    """
    First-order semi-major-axis change from a small IN-TRACK impulse on a
    near-circular orbit: da ~= 2*dv_t/n. Radial and cross-track components
    do not change semi-major axis to first order (they perturb eccentricity/
    argument-of-perigee or inclination/RAAN instead) -- this is why the
    slot-keeping constraint in delta_v_optimizer.py is evaluated against the
    in-track component specifically, not against ||delta_v|| as a whole.
    """
    return 2.0 * delta_v_intrack_km_s / n
