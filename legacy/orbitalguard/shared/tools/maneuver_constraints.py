"""
Tool: evaluate_maneuver_constraints
Evaluates a maneuver candidate against physical and operational constraints:
  - Delta-V budget / thruster capability constraint
  - Collision probability (Pc) safety constraint
  - Operational slot retention / semi-major-axis drift constraint
  - Ground-station line-of-sight communication visibility constraint
Reuses existing physics models from services/maneuver/app/ and services/propagation/app/.
"""

import math
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from shared.schemas.maneuver import ManeuverCandidate
from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.ground_station import PassWindow
from shared.tools.models import ManeuverConstraintEvaluation
from shared.tools.errors import ObjectNotFoundError, ConjunctionNotFoundError
from services.propagation.app.config import settings as prop_settings
from services.maneuver.app.delta_v_optimizer import (
    MAX_DELTA_V_KM_S,
    MAX_SMA_DRIFT_KM,
    PC_CRITICAL_THRESHOLD,
)
from services.maneuver.app.cw_transition import (
    MU_EARTH_KM3_S2,
    mean_motion_from_semi_major_axis,
    semi_major_axis_drift_km,
)
from services.propagation.app.physics.ground_stations import compute_passes_for_object
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository
from services.risk.app.database import get_conjunction_from_db


def evaluate_maneuver_constraints(
    candidate: ManeuverCandidate,
    conjunction: Optional[ConjunctionCandidate] = None,
    conjunction_id: Optional[str] = None,
    primary_catalog_id: Optional[str] = None,
    max_delta_v_m_s: Optional[float] = None,
    max_sma_drift_km: Optional[float] = None,
    pc_critical_threshold: Optional[float] = None,
    check_ground_station: bool = True,
    ground_station_horizon_minutes: int = 90,
) -> ManeuverConstraintEvaluation:
    """
    Evaluate an avoidance maneuver candidate against physical, safety, and operational constraints.

    Evaluated constraints:
    1. Maximum Delta-V Cap: Ensures candidate does not exceed satellite fuel or single-burn limits.
    2. Critical Pc Threshold: Ensures post-burn collision probability satisfies safety threshold.
    3. Operational Slot Drift: Linearized CW semi-major axis change must stay within slot bound.
    4. Ground Station Visibility: Verifies satellite ground station AOS/LOS visibility during the horizon.

    Args:
        candidate: The ManeuverCandidate to evaluate.
        conjunction: ConjunctionCandidate instance providing encounter context.
        conjunction_id: Unique conjunction ID to resolve from database.
        primary_catalog_id: NORAD Catalog ID of primary satellite for ground-station evaluation.
        max_delta_v_m_s: Max delta-V threshold in m/s (defaults to 20.0 m/s).
        max_sma_drift_km: Max semi-major axis drift in km (defaults to 5.0 km).
        pc_critical_threshold: Max acceptable collision probability (defaults to 1e-4).
        check_ground_station: If True, evaluates ground station contact windows.
        ground_station_horizon_minutes: Horizon duration in minutes for ground station pass checks.

    Returns:
        ManeuverConstraintEvaluation object with per-constraint pass/fail flags and diagnostics.
    """
    max_dv = max_delta_v_m_s or (MAX_DELTA_V_KM_S * 1000.0)
    max_sma = max_sma_drift_km or MAX_SMA_DRIFT_KM
    pc_threshold = pc_critical_threshold or PC_CRITICAL_THRESHOLD

    # 1. Delta-V constraint
    dv_m_s = float(candidate.delta_v_m_s)
    delta_v_satisfied = dv_m_s <= max_dv

    # 2. Pc constraint
    pc_val = candidate.predicted_pc
    pc_satisfied: Optional[bool] = None
    if pc_val is not None:
        pc_satisfied = pc_val <= pc_threshold

    # 3. Slot retention / semi-major axis drift constraint
    # Determine in-track delta-v component in km/s
    dv_intrack_km_s = 0.0
    if candidate.delta_v_vector is not None:
        # RIC vector: [R, I, C] -> y is in-track
        dv_intrack_km_s = candidate.delta_v_vector.y / 1000.0
    else:
        direction = candidate.burn_direction.upper()
        if "POSIGRADE" in direction:
            dv_intrack_km_s = dv_m_s / 1000.0
        elif "RETROGRADE" in direction:
            dv_intrack_km_s = -(dv_m_s / 1000.0)
        else:
            dv_intrack_km_s = 0.0

    # Determine mean motion n from primary object or reference LEO orbit (~400km)
    target_conj = conjunction
    if target_conj is None and conjunction_id is not None:
        target_conj = get_conjunction_from_db(conjunction_id)

    sat_id = primary_catalog_id
    if sat_id is None and target_conj is not None:
        sat_id = target_conj.primary_object

    n_mean_motion = None
    primary_db = None
    if sat_id:
        session = SessionLocal()
        try:
            repo = DatabaseRepository(session)
            primary_db = repo.get_object_by_catalog_id(sat_id)
        finally:
            session.close()

    if primary_db and primary_db.raw_tle_line2:
        try:
            from services.propagation.app.ingestion.parser import parse_tle_orbital_elements
            elems = parse_tle_orbital_elements(primary_db.raw_tle_line2)
            semi_major_axis_km = elems.get("semi_major_axis_km", 6778.0)
            n_mean_motion = mean_motion_from_semi_major_axis(semi_major_axis_km)
        except Exception:
            pass

    if n_mean_motion is None:
        # Standard LEO 400 km altitude reference: a = 6378.137 + 400 = 6778.137 km
        n_mean_motion = mean_motion_from_semi_major_axis(6778.137)

    calculated_drift_km = semi_major_axis_drift_km(dv_intrack_km_s, n_mean_motion)
    slot_drift_satisfied = abs(calculated_drift_km) <= max_sma

    # 4. Ground station visibility constraint
    ground_station_passes: List[PassWindow] = []
    ground_station_satisfied: Optional[bool] = None
    if check_ground_station and primary_db and primary_db.raw_tle_line1 and primary_db.raw_tle_line2:
        start_time = datetime.now(timezone.utc)
        ground_station_passes = compute_passes_for_object(
            tle_line1=primary_db.raw_tle_line1,
            tle_line2=primary_db.raw_tle_line2,
            name=primary_db.name,
            start_dt=start_time,
            horizon_minutes=ground_station_horizon_minutes,
        )
        ground_station_satisfied = len(ground_station_passes) > 0

    # Determine overall status
    is_feasible = delta_v_satisfied and slot_drift_satisfied
    if pc_satisfied is not None:
        is_feasible = is_feasible and pc_satisfied

    if not is_feasible:
        overall_status = "VIOLATED"
    elif pc_val is not None and pc_val >= 0.95 * pc_threshold:
        overall_status = "PC_CONSTRAINT_ACTIVE"
    elif abs(calculated_drift_km) >= 0.95 * max_sma:
        overall_status = "SLOT_CONSTRAINT_ACTIVE"
    else:
        overall_status = "SATISFIED"

    details: Dict[str, Any] = {
        "dominant_axis": candidate.burn_direction,
        "intrack_delta_v_m_s": round(dv_intrack_km_s * 1000.0, 4),
        "mean_motion_rad_s": round(n_mean_motion, 7),
        "total_ground_station_passes": len(ground_station_passes),
    }

    return ManeuverConstraintEvaluation(
        maneuver_id=candidate.maneuver_id,
        is_feasible=is_feasible,
        overall_status=overall_status,
        delta_v_m_s=round(dv_m_s, 4),
        max_delta_v_m_s=max_dv,
        delta_v_satisfied=delta_v_satisfied,
        predicted_pc=pc_val,
        pc_threshold=pc_threshold,
        pc_satisfied=pc_satisfied,
        sma_drift_km=round(calculated_drift_km, 4),
        max_sma_drift_km=max_sma,
        slot_drift_satisfied=slot_drift_satisfied,
        ground_station_visibility_checked=check_ground_station,
        ground_station_satisfied=ground_station_satisfied,
        ground_station_passes=ground_station_passes,
        details=details,
    )
