import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from services.propagation.app.screening.coarse_filter import CoarseFilter
from services.propagation.app.screening.fine_filter import FineFilter
from shared.schemas.conjunction import ConjunctionCandidate

logger = logging.getLogger(__name__)


class ScreeningPipeline:
    def __init__(
        self,
        threshold_km: Optional[float] = None,
        buffer_km: Optional[float] = None,
        combined_hbr_km: Optional[float] = None,
        drag_activity_scalar: float = 1.0,
    ):
        self.coarse_filter = CoarseFilter(buffer_km=buffer_km)
        self.fine_filter = FineFilter(
            threshold_km=threshold_km,
            combined_hbr_km=combined_hbr_km,
            drag_activity_scalar=drag_activity_scalar,
        )

    def run_screening(
        self,
        objects: List[Dict[str, Any]],
        horizon_minutes: int = 90
    ) -> List[ConjunctionCandidate]:
        """
        Run complete two-stage screening pipeline across tracked objects.
        1. Categorize objects into Primaries (Satellites) and Secondaries (Debris / Rocket Bodies).
        2. Coarse filter altitude band overlaps.
        3. Fine filter pairwise propagation and local TCA distance refinement.
        """
        primaries = [o for o in objects if o.get("object_type") in ("SATELLITE", "PRIMARY")]
        secondaries = [o for o in objects if o.get("object_type") in ("DEBRIS", "ROCKET_BODY", "SYNTHETIC_DEBRIS", "UNKNOWN")]

        # If no explicit classification, screen all pairs
        if not primaries or not secondaries:
            primaries = objects
            secondaries = objects

        logger.info(f"Screening Pipeline: {len(primaries)} primaries vs {len(secondaries)} secondaries.")

        # Stage 1: Coarse Filtering
        candidate_pairs = self.coarse_filter.filter_pairs(primaries, secondaries)
        logger.info(f"Stage 1 Coarse Filter: {len(candidate_pairs)} candidate pairs survived.")

        # Stage 2: Fine Screening & Local Refinement
        # One shared snapshot time + engine cache for the whole pass: every
        # pair is screened as of the same instant (rather than each
        # independently drifting datetime.now() call), and each object's SGP4
        # engine + coarse propagation is built once no matter how many pairs
        # it appears in -- see FineFilter._coarse_state for why that matters.
        conjunctions: List[ConjunctionCandidate] = []
        start_dt = datetime.now(timezone.utc)
        engine_cache: Dict[str, Any] = {}
        for p, s in candidate_pairs:
            candidate = self.fine_filter.compute_conjunction_candidate(
                p, s, start_dt=start_dt, horizon_minutes=horizon_minutes, engine_cache=engine_cache
            )
            if candidate:
                conjunctions.append(candidate)

        logger.info(f"Stage 2 Fine Screening: Identified {len(conjunctions)} potential close approach candidates.")
        return conjunctions
