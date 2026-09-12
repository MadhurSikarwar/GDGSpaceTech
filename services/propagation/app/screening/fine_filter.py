import math
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

from services.propagation.app.config import settings
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine
from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance


class FineFilter:
    def __init__(self, threshold_km: Optional[float] = None):
        self.threshold_km = threshold_km or settings.SCREENING_THRESHOLD_KM

    def compute_conjunction_candidate(
        self,
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        start_dt: Optional[datetime] = None,
        horizon_minutes: int = 90,
        initial_step_minutes: float = 1.0,
        refinement_window_seconds: float = 120.0,
        refinement_step_seconds: float = 5.0
    ) -> Optional[ConjunctionCandidate]:
        """
        Stage 2 Fine Screening:
        Propagates candidate pair over horizon, evaluates distance at exact matching UTC timestamps in TEME frame.
        Refines minimum distance around the closest sampled interval.
        Returns a ConjunctionCandidate if refined minimum separation <= threshold_km.
        """
        if start_dt is None:
            start_dt = datetime.now(timezone.utc)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        engine_p = SGP4PropagationEngine(
            primary["tle_line_1"], primary["tle_line_2"], primary.get("name", "PRIMARY")
        )
        engine_s = SGP4PropagationEngine(
            secondary["tle_line_1"], secondary["tle_line_2"], secondary.get("name", "SECONDARY")
        )

        min_dist_km = float("inf")
        min_tca_dt = start_dt
        min_rel_vel_kms = 0.0

        current_dt = start_dt
        end_dt = start_dt + timedelta(minutes=horizon_minutes)

        # Initial 1-minute coarse scan
        while current_dt <= end_dt:
            state_p = engine_p.propagate_state(current_dt)
            state_s = engine_s.propagate_state(current_dt)

            # Frame & Timestamp aligned Euclidean distance calculation (TEME)
            dx = state_p.position_km.x - state_s.position_km.x
            dy = state_p.position_km.y - state_s.position_km.y
            dz = state_p.position_km.z - state_s.position_km.z
            dist = math.sqrt(dx*dx + dy*dy + dz*dz)

            if dist < min_dist_km:
                min_dist_km = dist
                min_tca_dt = current_dt

            current_dt += timedelta(minutes=initial_step_minutes)

        # Local time refinement scan around minimum candidate interval
        refined_start = min_tca_dt - timedelta(seconds=refinement_window_seconds / 2.0)
        refined_end = min_tca_dt + timedelta(seconds=refinement_window_seconds / 2.0)
        refined_dt = refined_start

        while refined_dt <= refined_end:
            state_p = engine_p.propagate_state(refined_dt)
            state_s = engine_s.propagate_state(refined_dt)

            dx = state_p.position_km.x - state_s.position_km.x
            dy = state_p.position_km.y - state_s.position_km.y
            dz = state_p.position_km.z - state_s.position_km.z
            dist = math.sqrt(dx*dx + dy*dy + dz*dz)

            if dist < min_dist_km:
                min_dist_km = dist
                min_tca_dt = refined_dt
                
                # Relative velocity vector magnitude
                dvx = state_p.velocity_km_s.x - state_s.velocity_km_s.x
                dvy = state_p.velocity_km_s.y - state_s.velocity_km_s.y
                dvz = state_p.velocity_km_s.z - state_s.velocity_km_s.z
                min_rel_vel_kms = math.sqrt(dvx*dvx + dvy*dvy + dvz*dvz)

            refined_dt += timedelta(seconds=refinement_step_seconds)

        # Check against configured screening threshold
        if min_dist_km <= self.threshold_km:
            conj_id = f"CONJ-{primary['catalog_id']}-{secondary['catalog_id']}-{min_tca_dt.strftime('%Y%m%d%H%M')}"
            return ConjunctionCandidate(
                conjunction_id=conj_id,
                primary_object=primary["catalog_id"],
                secondary_object=secondary["catalog_id"],
                primary_object_name=primary.get("name", "PRIMARY"),
                secondary_object_name=secondary.get("name", "SECONDARY"),
                tca=min_tca_dt,
                closest_approach=ClosestApproach(
                    distance_km=round(min_dist_km, 3),
                    relative_velocity_km_s=round(min_rel_vel_kms, 3)
                ),
                screening=ScreeningInfo(
                    threshold_km=self.threshold_km,
                    method="SGP4_TWO_STAGE_FINE_REFINED_SCAN"
                ),
                data_provenance=DataProvenance(
                    primary_source=primary.get("source", "CelesTrak"),
                    propagator="SGP4"
                ),
                created_at=datetime.now(timezone.utc)
            )

        return None
