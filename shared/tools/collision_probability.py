"""
Tool: compute_pc
Computes probability of collision using the authoritative Foster (1992) 2D
encounter-plane quadrature method over empirical covariance ellipsoids.
Reuses existing services/propagation/app/physics/* implementations.
"""

from datetime import datetime, timezone
from typing import Optional, Sequence
import numpy as np

from shared.schemas.conjunction import ConjunctionCandidate
from shared.tools.errors import ObjectNotFoundError, ComputationError, ConjunctionNotFoundError
from shared.tools.models import PcComputationResult
from services.propagation.app.config import settings
from services.propagation.app.physics import probability_of_collision as pc_physics
from services.propagation.app.physics import covariance as covariance_physics
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository
from services.risk.app.database import get_conjunction_from_db


def compute_pc(
    primary_catalog_id: Optional[str] = None,
    secondary_catalog_id: Optional[str] = None,
    tca: Optional[datetime] = None,
    combined_hbr_m: Optional[float] = None,
    conjunction_id: Optional[str] = None,
    candidate: Optional[ConjunctionCandidate] = None,
    relative_position_km: Optional[Sequence[float]] = None,
    relative_velocity_km_s: Optional[Sequence[float]] = None,
    combined_covariance_teme_km2: Optional[np.ndarray] = None,
    drag_activity_scalar: float = 1.0,
) -> PcComputationResult:
    """
    Compute collision probability (Pc) via the existing Foster 2D encounter-plane method.

    Accepts either:
    1. Direct relative position, velocity, and combined covariance in TEME frame.
    2. Primary & secondary catalog IDs with Time of Closest Approach (TCA).
    3. An existing ConjunctionCandidate object or conjunction_id.

    Args:
        primary_catalog_id: NORAD Catalog ID of primary object.
        secondary_catalog_id: NORAD Catalog ID of secondary object.
        tca: Time of Closest Approach (UTC).
        combined_hbr_m: Combined hard-body radius in meters (defaults to configured 20m).
        conjunction_id: Unique conjunction event identifier to load from database.
        candidate: Existing ConjunctionCandidate schema instance.
        relative_position_km: 3D relative position vector [dx, dy, dz] in km at TCA.
        relative_velocity_km_s: 3D relative velocity vector [vx, vy, vz] in km/s at TCA.
        combined_covariance_teme_km2: 3x3 combined covariance matrix in TEME frame.
        drag_activity_scalar: Multiplier (>=1.0) applied to LEO in-track covariance growth.

    Returns:
        PcComputationResult with probability_of_collision, error estimate, and metadata.

    Raises:
        ValueError: If insufficient parameters are provided.
        ObjectNotFoundError: If a referenced object cannot be found in database.
        ComputationError: If numerical integration or propagation fails.
    """
    hbr_m = combined_hbr_m or settings.COMBINED_HARD_BODY_RADIUS_M
    hbr_km = hbr_m / 1000.0

    # Path 1: Direct relative vectors and combined covariance provided
    if (
        relative_position_km is not None
        and relative_velocity_km_s is not None
        and combined_covariance_teme_km2 is not None
    ):
        try:
            pc, abserr = pc_physics.compute_pc(
                relative_position_km=relative_position_km,
                relative_velocity_km_s=relative_velocity_km_s,
                combined_covariance_teme_km2=combined_covariance_teme_km2,
                combined_hbr_km=hbr_km,
            )
            return PcComputationResult(
                probability_of_collision=pc,
                estimated_error=abserr,
                method=pc_physics.PC_METHOD,
                combined_hard_body_radius_m=hbr_m,
            )
        except Exception as e:
            raise ComputationError(f"Foster Pc quadrature failed: {e}") from e

    # Path 2: Resolve from ConjunctionCandidate or conjunction_id
    target_candidate = candidate
    if target_candidate is None and conjunction_id is not None:
        target_candidate = get_conjunction_from_db(conjunction_id)
        if not target_candidate:
            raise ConjunctionNotFoundError(f"Conjunction with ID '{conjunction_id}' not found.")

    if target_candidate is not None:
        primary_id = target_candidate.primary_object
        secondary_id = target_candidate.secondary_object
        tca_dt = target_candidate.tca
        if target_candidate.combined_hard_body_radius_m:
            hbr_m = target_candidate.combined_hard_body_radius_m
            hbr_km = hbr_m / 1000.0
    else:
        primary_id = primary_catalog_id
        secondary_id = secondary_catalog_id
        tca_dt = tca

    if not primary_id or not secondary_id or not tca_dt:
        raise ValueError(
            "Must provide either (relative_position, relative_velocity, combined_covariance), "
            "or (primary_catalog_id, secondary_catalog_id, tca), or a valid conjunction_id/candidate."
        )

    if tca_dt.tzinfo is None:
        tca_dt = tca_dt.replace(tzinfo=timezone.utc)

    # Fetch object records from repository to build SGP4 propagation and covariance
    session = SessionLocal()
    try:
        repo = DatabaseRepository(session)
        p_obj = repo.get_object_by_catalog_id(primary_id)
        s_obj = repo.get_object_by_catalog_id(secondary_id)
        if not p_obj:
            raise ObjectNotFoundError(f"Primary object '{primary_id}' not found in database.")
        if not s_obj:
            raise ObjectNotFoundError(f"Secondary object '{secondary_id}' not found in database.")

        p_tle1, p_tle2, p_name = p_obj.raw_tle_line1, p_obj.raw_tle_line2, p_obj.name
        s_tle1, s_tle2, s_name = s_obj.raw_tle_line1, s_obj.raw_tle_line2, s_obj.name
        p_epoch, p_type = p_obj.epoch, p_obj.object_type
        s_epoch, s_type = s_obj.epoch, s_obj.object_type
    finally:
        session.close()

    try:
        # Propagate both to TCA
        engine_p = SGP4PropagationEngine(p_tle1, p_tle2, p_name)
        engine_s = SGP4PropagationEngine(s_tle1, s_tle2, s_name)

        state_p = engine_p.propagate_state(tca_dt)
        state_s = engine_s.propagate_state(tca_dt)

        pos_p = np.array([state_p.position_km.x, state_p.position_km.y, state_p.position_km.z])
        vel_p = np.array([state_p.velocity_km_s.x, state_p.velocity_km_s.y, state_p.velocity_km_s.z])
        pos_s = np.array([state_s.position_km.x, state_s.position_km.y, state_s.position_km.z])
        vel_s = np.array([state_s.velocity_km_s.x, state_s.velocity_km_s.y, state_s.velocity_km_s.z])

        rel_pos = pos_p - pos_s
        rel_vel = vel_p - vel_s

        p_age_hours = abs((tca_dt - (p_epoch if p_epoch.tzinfo else p_epoch.replace(tzinfo=timezone.utc))).total_seconds()) / 3600.0
        s_age_hours = abs((tca_dt - (s_epoch if s_epoch.tzinfo else s_epoch.replace(tzinfo=timezone.utc))).total_seconds()) / 3600.0

        comb_cov = covariance_physics.combined_covariance_teme_km2(
            primary_position_km=pos_p,
            primary_velocity_km_s=vel_p,
            primary_age_hours=p_age_hours,
            primary_type=p_type,
            secondary_position_km=pos_s,
            secondary_velocity_km_s=vel_s,
            secondary_age_hours=s_age_hours,
            secondary_type=s_type,
            drag_activity_scalar=drag_activity_scalar,
        )

        pc, abserr = pc_physics.compute_pc(
            relative_position_km=rel_pos,
            relative_velocity_km_s=rel_vel,
            combined_covariance_teme_km2=comb_cov,
            combined_hbr_km=hbr_km,
        )

        return PcComputationResult(
            probability_of_collision=pc,
            estimated_error=abserr,
            method=pc_physics.PC_METHOD,
            combined_hard_body_radius_m=hbr_m,
        )
    except (ObjectNotFoundError, ConjunctionNotFoundError):
        raise
    except Exception as e:
        raise ComputationError(f"Foster Pc computation failed for {primary_id} x {secondary_id}: {e}") from e
