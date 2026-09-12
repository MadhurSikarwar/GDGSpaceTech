"""
Delta-V cost-function optimizer: scipy.optimize.minimize (SLSQP) finds the
minimum-magnitude impulsive delta-v (RIC frame, applied now) that drops the
predicted probability of collision at TCA below a critical threshold,
subject to (a) a maximum single-burn delta-v cap (thruster/fuel realism) and
(b) an operational-slot-retention bound on the resulting semi-major-axis
drift -- the two "constraint-awareness" requirements.

Rebuilds the exact physics inputs the screening pipeline used to compute the
ORIGINAL Pc (TLEs -> SGP4 state at TCA -> RIC covariance -> Foster 2D Pc),
reusing services/propagation/app/physics/* directly rather than duplicating
that logic -- consistent with this codebase's existing cross-service import
precedent (services/maneuver/app/main.py already imports
services.propagation.app.database... and services.risk.app.scoring
directly).

Requires full orbital data for both objects (TLEs + TCA), which is only
available when the caller resolves maneuvers via `conjunction_id` (the path
the frontend always uses). The direct-RiskAssessment-body contract carries
only scalar hazard factors -- no orbital state at all -- so it cannot run a
real physics optimizer no matter how it's implemented; main.py keeps a
documented heuristic fallback for exactly that reduced-information case
(see main.py's _generate_heuristic_candidates), the same
live-optimizer-when-possible / honest-fallback-otherwise split already used
throughout this codebase's frontend (api.js).
"""

import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from services.propagation.app.config import settings as propagation_settings
from services.propagation.app.physics import covariance as covariance_physics
from services.propagation.app.physics import probability_of_collision as pc_physics
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine
from services.maneuver.app.cw_transition import (
    MU_EARTH_KM3_S2,
    mean_motion_from_semi_major_axis,
    position_response_matrix,
    semi_major_axis_drift_km,
)

logger = logging.getLogger(__name__)

PC_CRITICAL_THRESHOLD = propagation_settings.PC_CRITICAL_THRESHOLD
MAX_DELTA_V_KM_S = float(os.getenv("MAX_DELTA_V_MPS", "20.0")) / 1000.0
MAX_SMA_DRIFT_KM = float(os.getenv("MAX_SMA_DRIFT_KM", "5.0"))


def _vec3_to_array(v) -> np.ndarray:
    return np.array([v.x, v.y, v.z], dtype=float)


def build_optimizer_inputs(
    primary_tle1: str, primary_tle2: str, primary_name: str, primary_epoch: datetime, primary_type: str,
    secondary_tle1: str, secondary_tle2: str, secondary_name: str, secondary_epoch: datetime, secondary_type: str,
    tca_dt: datetime, combined_hbr_km: float, drag_activity_scalar: float = 1.0,
) -> Dict[str, Any]:
    """Propagate both objects to TCA and assemble everything predicted_pc_for_delta_v needs."""
    state_p = SGP4PropagationEngine(primary_tle1, primary_tle2, primary_name).propagate_state(tca_dt)
    state_s = SGP4PropagationEngine(secondary_tle1, secondary_tle2, secondary_name).propagate_state(tca_dt)

    pos_p = _vec3_to_array(state_p.position_km)
    vel_p = _vec3_to_array(state_p.velocity_km_s)
    pos_s = _vec3_to_array(state_s.position_km)
    vel_s = _vec3_to_array(state_s.velocity_km_s)

    r = float(np.linalg.norm(pos_p))
    v = float(np.linalg.norm(vel_p))
    semi_major_axis_km = 1.0 / (2.0 / r - (v * v) / MU_EARTH_KM3_S2)
    n = mean_motion_from_semi_major_axis(semi_major_axis_km)

    def _age_hours(epoch: datetime) -> float:
        e = epoch if epoch.tzinfo else epoch.replace(tzinfo=tca_dt.tzinfo)
        return abs((tca_dt - e).total_seconds()) / 3600.0

    combined_cov = covariance_physics.combined_covariance_teme_km2(
        pos_p, vel_p, _age_hours(primary_epoch), primary_type,
        pos_s, vel_s, _age_hours(secondary_epoch), secondary_type,
        drag_activity_scalar,
    )
    ric_rotation = covariance_physics.ric_basis(pos_p, vel_p)  # RIC(t) -> TEME(t)

    return {
        "n": n,
        "ric_rotation": ric_rotation,
        "combined_cov": combined_cov,
        "rel_pos_teme": pos_p - pos_s,
        "rel_vel_teme": vel_p - vel_s,
        "combined_hbr_km": combined_hbr_km,
    }


def shifted_relative_position_teme(delta_v_ric_km_s: np.ndarray, inputs: Dict[str, Any], time_to_tca_s: float) -> np.ndarray:
    shift_ric = position_response_matrix(inputs["n"], time_to_tca_s) @ np.asarray(delta_v_ric_km_s, dtype=float)
    shift_teme = inputs["ric_rotation"] @ shift_ric
    return inputs["rel_pos_teme"] + shift_teme


def shifted_miss_distance_km(delta_v_ric_km_s: np.ndarray, inputs: Dict[str, Any], time_to_tca_s: float) -> float:
    return float(np.linalg.norm(shifted_relative_position_teme(delta_v_ric_km_s, inputs, time_to_tca_s)))


def predicted_pc_for_delta_v(delta_v_ric_km_s: np.ndarray, inputs: Dict[str, Any], time_to_tca_s: float) -> float:
    new_rel_pos_teme = shifted_relative_position_teme(delta_v_ric_km_s, inputs, time_to_tca_s)
    pc, _abserr = pc_physics.compute_pc(
        new_rel_pos_teme, inputs["rel_vel_teme"], inputs["combined_cov"], inputs["combined_hbr_km"]
    )
    return pc


def _warm_start(inputs: Dict[str, Any], time_to_tca_s: float) -> np.ndarray:
    """
    A small in-track nudge, correctly signed to increase (not decrease) miss
    distance -- in-track impulses are the most fuel-efficient CW lever for
    opening along-track separation over a coasting arc, and starting SLSQP
    exactly on the wrong side of the Pc constraint (an all-zero guess sits
    ON today's actual, already-too-high Pc) slows convergence for no reason.
    """
    rel_pos_ric = inputs["ric_rotation"].T @ inputs["rel_pos_teme"]
    phi_rv = position_response_matrix(inputs["n"], time_to_tca_s)
    in_track_response = phi_rv[:, 1]
    sign = np.sign(np.dot(rel_pos_ric, in_track_response)) or 1.0
    return np.array([0.0, sign * 1.0e-4, 0.0])


def solve_minimum_delta_v(
    inputs: Dict[str, Any],
    time_to_tca_s: float,
    pc_threshold: float = PC_CRITICAL_THRESHOLD,
    max_delta_v_km_s: float = MAX_DELTA_V_KM_S,
    max_sma_drift_km: float = MAX_SMA_DRIFT_KM,
    safety_margin_factor: float = 0.9,
):
    """
    safety_margin_factor shrinks the INTERNAL optimization target to
    `pc_threshold * safety_margin_factor` -- SLSQP satisfies inequality
    constraints only to its own numerical tolerance, so optimizing to the
    razor's edge of the nominal threshold can converge with the true Pc a
    hair on the wrong side of it (observed in practice: a "minimal" solution
    reporting Pc fractionally >= threshold, misclassifying a safety-
    compliant burn as CRITICAL instead of comfortably HIGH-or-better). A 10%
    margin is standard practice for a hard safety constraint under numerical
    optimization and is applied only here, not to the threshold used
    elsewhere for classification/reporting.
    """
    effective_threshold = pc_threshold * safety_margin_factor

    def objective(dv):
        return float(np.linalg.norm(dv))

    def pc_constraint(dv):
        return effective_threshold - predicted_pc_for_delta_v(dv, inputs, time_to_tca_s)  # >= 0 required

    def sma_constraint(dv):
        drift = semi_major_axis_drift_km(dv[1], inputs["n"])
        return max_sma_drift_km - abs(drift)  # >= 0 required

    x0 = _warm_start(inputs, time_to_tca_s)
    bounds = [(-max_delta_v_km_s, max_delta_v_km_s)] * 3
    constraints = [
        {"type": "ineq", "fun": pc_constraint},
        {"type": "ineq", "fun": sma_constraint},
    ]
    result = minimize(
        objective, x0, method="SLSQP", bounds=bounds, constraints=constraints,
        options={"maxiter": 200, "ftol": 1e-12},
    )
    return result


def classify_constraint_status(result, inputs: Dict[str, Any], time_to_tca_s: float,
                                pc_threshold: float, max_sma_drift_km: float) -> str:
    if not result.success:
        return "INFEASIBLE"
    dv = result.x
    pc = predicted_pc_for_delta_v(dv, inputs, time_to_tca_s)
    drift = abs(semi_major_axis_drift_km(dv[1], inputs["n"]))
    pc_binding = pc >= 0.98 * pc_threshold
    sma_binding = drift >= 0.98 * max_sma_drift_km
    if sma_binding:
        return "SLOT_CONSTRAINT_ACTIVE"
    if pc_binding:
        return "PC_CONSTRAINT_ACTIVE"
    return "SATISFIED"


def burn_direction_label(dv_ric_km_s: np.ndarray) -> str:
    """Classify by the dominant RIC component -- matches the schema's documented values."""
    radial, intrack, crosstrack = dv_ric_km_s
    dominant = max((abs(radial), "RADIAL", radial), (abs(intrack), "INTRACK", intrack), (abs(crosstrack), "CROSSTRACK", crosstrack))
    magnitude, axis, signed = dominant
    if axis == "INTRACK":
        return "POSIGRADE" if signed >= 0 else "RETROGRADE"
    if axis == "CROSSTRACK":
        return "NORMAL" if signed >= 0 else "ANTINORMAL"
    return "RADIAL" if signed >= 0 else "ANTIRADIAL"
