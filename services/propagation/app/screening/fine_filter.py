import math
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from skyfield.api import load

from services.propagation.app.config import settings
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine, ts
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
        Vectorized high-performance SGP4 propagation over horizon in TEME frame.
        Evaluates distances and refines local minimum around closest sampled interval.
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

        # 1. Vectorized Coarse Scan
        num_coarse_steps = int(horizon_minutes / initial_step_minutes) + 1
        times_coarse = [start_dt + timedelta(minutes=i * initial_step_minutes) for i in range(num_coarse_steps)]
        t_c = ts.from_datetimes(times_coarse)

        pos_p = engine_p.satellite.at(t_c).position.km
        pos_s = engine_s.satellite.at(t_c).position.km

        dx = pos_p[0] - pos_s[0]
        dy = pos_p[1] - pos_s[1]
        dz = pos_p[2] - pos_s[2]
        dists = np.sqrt(dx*dx + dy*dy + dz*dz)

        min_idx = int(np.argmin(dists))
        min_dist_km = float(dists[min_idx])
        min_tca_dt = times_coarse[min_idx]

        # 2. Vectorized Local Time Refinement around minimum interval
        refined_start = min_tca_dt - timedelta(seconds=refinement_window_seconds / 2.0)
        num_refine = int(refinement_window_seconds / refinement_step_seconds) + 1
        refined_times = [refined_start + timedelta(seconds=i * refinement_step_seconds) for i in range(num_refine)]
        t_r = ts.from_datetimes(refined_times)

        geo_p = engine_p.satellite.at(t_r)
        geo_s = engine_s.satellite.at(t_r)
        pos_pr = geo_p.position.km
        pos_sr = geo_s.position.km
        vel_pr = geo_p.velocity.km_per_s
        vel_sr = geo_s.velocity.km_per_s

        dxr = pos_pr[0] - pos_sr[0]
        dyr = pos_pr[1] - pos_sr[1]
        dzr = pos_pr[2] - pos_sr[2]
        dists_r = np.sqrt(dxr*dxr + dyr*dyr + dzr*dzr)

        min_r_idx = int(np.argmin(dists_r))
        min_dist_km = float(dists_r[min_r_idx])
        min_tca_dt = refined_times[min_r_idx]

        dvx = float(vel_pr[0][min_r_idx] - vel_sr[0][min_r_idx])
        dvy = float(vel_pr[1][min_r_idx] - vel_sr[1][min_r_idx])
        dvz = float(vel_pr[2][min_r_idx] - vel_sr[2][min_r_idx])
        min_rel_vel_kms = float(math.sqrt(dvx*dvx + dvy*dvy + dvz*dvz))

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
