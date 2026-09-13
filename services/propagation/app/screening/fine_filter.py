import logging
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from skyfield.api import load

from services.propagation.app.config import settings
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine, ts
from services.propagation.app.physics import covariance as covariance_physics
from services.propagation.app.physics import probability_of_collision as pc_physics
from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance
from shared.schemas.state import Vector3

logger = logging.getLogger(__name__)


class FineFilter:
    def __init__(
        self,
        threshold_km: Optional[float] = None,
        combined_hbr_km: Optional[float] = None,
        drag_activity_scalar: float = 1.0,
    ):
        self.threshold_km = threshold_km or settings.SCREENING_THRESHOLD_KM
        # Combined (primary + secondary) hard-body radius for the Pc disk
        # integral, and the live NOAA-derived drag-activity multiplier applied
        # to along-track covariance growth (see physics/covariance.py). Both
        # default to quiet-baseline settings so Pc is still computed even when
        # the caller doesn't have a live space-weather reading on hand.
        self.combined_hbr_km = combined_hbr_km or (settings.COMBINED_HARD_BODY_RADIUS_M / 1000.0)
        self.drag_activity_scalar = drag_activity_scalar

    def _coarse_state(self, obj: Dict[str, Any], t_c, engine_cache: Optional[Dict[str, Any]]):
        """SGP4 engine + position at the shared coarse time grid for one object.

        When engine_cache is given (run_screening's bulk pass shares one dict
        across every pair in a run), each object's engine construction and
        91-point coarse propagation happens once no matter how many pairs it
        appears in. Previously this was rebuilt from scratch -- fresh
        Skyfield EarthSatellite + fresh propagation -- on every single
        pairing; against the live catalog's ~1M coarse-surviving pairs that
        redundant reconstruction, not the physics itself, was nearly the
        entire cost of a /screen call. Standalone callers (tests calling
        compute_conjunction_candidate directly for one pair) pass no cache
        and get the exact previous behaviour.
        """
        cat_id = obj.get("catalog_id")
        if engine_cache is not None and cat_id in engine_cache:
            return engine_cache[cat_id]
        engine = SGP4PropagationEngine(obj["tle_line_1"], obj["tle_line_2"], obj.get("name", "OBJECT"))
        pos = engine.satellite.at(t_c).position.km
        result = (engine, pos)
        if engine_cache is not None:
            engine_cache[cat_id] = result
        return result

    def compute_conjunction_candidate(
        self,
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        start_dt: Optional[datetime] = None,
        horizon_minutes: int = 90,
        initial_step_minutes: float = 1.0,
        refinement_window_seconds: float = 120.0,
        refinement_step_seconds: float = 5.0,
        engine_cache: Optional[Dict[str, Any]] = None,
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

        # 1. Vectorized Coarse Scan
        # The time grid depends only on (start_dt, horizon_minutes,
        # initial_step_minutes) -- identical for every pair in one
        # run_screening() pass -- so ts.from_datetimes() (a Skyfield
        # timescale conversion, not a free operation) gets cached the same
        # way per-object propagation does, instead of rebuilding the
        # identical 91-point grid on every one of ~1M pairs.
        grid_key = ("__grid__", start_dt, horizon_minutes, initial_step_minutes)
        if engine_cache is not None and grid_key in engine_cache:
            times_coarse, t_c = engine_cache[grid_key]
        else:
            num_coarse_steps = int(horizon_minutes / initial_step_minutes) + 1
            times_coarse = [start_dt + timedelta(minutes=i * initial_step_minutes) for i in range(num_coarse_steps)]
            t_c = ts.from_datetimes(times_coarse)
            if engine_cache is not None:
                engine_cache[grid_key] = (times_coarse, t_c)

        engine_p, pos_p = self._coarse_state(primary, t_c, engine_cache)
        engine_s, pos_s = self._coarse_state(secondary, t_c, engine_cache)

        dx = pos_p[0] - pos_s[0]
        dy = pos_p[1] - pos_s[1]
        dz = pos_p[2] - pos_s[2]
        dists = np.sqrt(dx*dx + dy*dy + dz*dz)

        min_idx = int(np.argmin(dists))
        min_dist_km = float(dists[min_idx])
        min_tca_dt = times_coarse[min_idx]

        # Early-exit: coarse samples are initial_step_minutes apart, so the
        # true continuous minimum near the sampled minimum can only differ
        # from it by roughly (relative velocity x half the coarse step) --
        # even at a worst-case ~15 km/s LEO-LEO closing speed and a 1-minute
        # step, that's ~450 km. A generous 750 km margin past threshold_km
        # means a pair that could ever refine to <= threshold_km is never
        # skipped, while every pair whose coarse pass shows it nowhere near
        # threshold skips the expensive refined propagation + Pc computation
        # (scipy dblquad) entirely. This is what makes screening the live
        # catalog's ~1M coarse-surviving pairs tractable.
        if min_dist_km > self.threshold_km + 750.0:
            return None

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

            # Relative position/velocity VECTORS at the refined TCA sample --
            # not just their magnitudes -- are the required input for Foster's
            # encounter-plane Pc projection below. Preserve them on the
            # returned candidate too (Optional fields) so any future consumer
            # has them without a second propagation.
            rel_pos_vec = (float(dxr[min_r_idx]), float(dyr[min_r_idx]), float(dzr[min_r_idx]))
            rel_vel_vec = (dvx, dvy, dvz)

            probability_of_collision, pc_method, combined_hbr_m = self._try_compute_pc(
                primary=primary,
                secondary=secondary,
                tca_dt=min_tca_dt,
                primary_pos_km=pos_pr[:, min_r_idx],
                primary_vel_km_s=vel_pr[:, min_r_idx],
                secondary_pos_km=pos_sr[:, min_r_idx],
                secondary_vel_km_s=vel_sr[:, min_r_idx],
                rel_pos_vec=rel_pos_vec,
                rel_vel_vec=rel_vel_vec,
            )

            return ConjunctionCandidate(
                conjunction_id=conj_id,
                primary_object=primary["catalog_id"],
                secondary_object=secondary["catalog_id"],
                primary_object_name=primary.get("name", "PRIMARY"),
                secondary_object_name=secondary.get("name", "SECONDARY"),
                tca=min_tca_dt,
                closest_approach=ClosestApproach(
                    distance_km=round(min_dist_km, 3),
                    relative_velocity_km_s=round(min_rel_vel_kms, 3),
                    relative_position_km=Vector3(x=rel_pos_vec[0], y=rel_pos_vec[1], z=rel_pos_vec[2]),
                    relative_velocity_vector_km_s=Vector3(x=rel_vel_vec[0], y=rel_vel_vec[1], z=rel_vel_vec[2]),
                ),
                screening=ScreeningInfo(
                    threshold_km=self.threshold_km,
                    method="SGP4_TWO_STAGE_FINE_REFINED_SCAN"
                ),
                data_provenance=DataProvenance(
                    primary_source=primary.get("source", "CelesTrak"),
                    propagator="SGP4"
                ),
                created_at=datetime.now(timezone.utc),
                probability_of_collision=probability_of_collision,
                pc_method=pc_method,
                combined_hard_body_radius_m=combined_hbr_m,
            )

        return None

    def _try_compute_pc(
        self,
        primary: Dict[str, Any],
        secondary: Dict[str, Any],
        tca_dt: datetime,
        primary_pos_km: np.ndarray,
        primary_vel_km_s: np.ndarray,
        secondary_pos_km: np.ndarray,
        secondary_vel_km_s: np.ndarray,
        rel_pos_vec: tuple,
        rel_vel_vec: tuple,
    ):
        """
        Best-effort Pc computation: needs each object's TLE epoch (to derive
        data age at TCA) which older callers may not supply. Missing epoch or
        any numerical edge case (e.g. a near-zero relative velocity, which
        would leave the encounter plane undefined) degrades to (None, None,
        None) rather than failing the whole screening pass -- a conjunction
        candidate is still meaningful by miss-distance alone even without Pc.
        """
        primary_epoch = primary.get("epoch")
        secondary_epoch = secondary.get("epoch")
        if primary_epoch is None or secondary_epoch is None:
            return None, None, None

        try:
            if tca_dt.tzinfo is None:
                tca_dt = tca_dt.replace(tzinfo=timezone.utc)
            if primary_epoch.tzinfo is None:
                primary_epoch = primary_epoch.replace(tzinfo=timezone.utc)
            if secondary_epoch.tzinfo is None:
                secondary_epoch = secondary_epoch.replace(tzinfo=timezone.utc)

            primary_age_hours = abs((tca_dt - primary_epoch).total_seconds()) / 3600.0
            secondary_age_hours = abs((tca_dt - secondary_epoch).total_seconds()) / 3600.0

            combined_cov = covariance_physics.combined_covariance_teme_km2(
                primary_position_km=primary_pos_km,
                primary_velocity_km_s=primary_vel_km_s,
                primary_age_hours=primary_age_hours,
                primary_type=primary.get("object_type", "SATELLITE"),
                secondary_position_km=secondary_pos_km,
                secondary_velocity_km_s=secondary_vel_km_s,
                secondary_age_hours=secondary_age_hours,
                secondary_type=secondary.get("object_type", "DEBRIS"),
                drag_activity_scalar=self.drag_activity_scalar,
            )
            pc, _abserr = pc_physics.compute_pc(
                relative_position_km=rel_pos_vec,
                relative_velocity_km_s=rel_vel_vec,
                combined_covariance_teme_km2=combined_cov,
                combined_hbr_km=self.combined_hbr_km,
            )
            return pc, pc_physics.PC_METHOD, self.combined_hbr_km * 1000.0
        except Exception as exc:
            logger.warning(
                f"Pc computation skipped for {primary.get('catalog_id')}x{secondary.get('catalog_id')}: {exc}"
            )
            return None, None, None
