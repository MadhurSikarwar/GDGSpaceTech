import logging
from typing import List, Dict, Any, Optional
from services.propagation.app.screening.coarse_filter import CoarseFilter
from services.propagation.app.screening.fine_filter import FineFilter
from shared.schemas.conjunction import ConjunctionCandidate

logger = logging.getLogger(__name__)


class ScreeningPipeline:
    def __init__(self, threshold_km: Optional[float] = None, buffer_km: Optional[float] = None):
        self.coarse_filter = CoarseFilter(buffer_km=buffer_km)
        self.fine_filter = FineFilter(threshold_km=threshold_km)

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
        conjunctions: List[ConjunctionCandidate] = []
        for p, s in candidate_pairs:
            candidate = self.fine_filter.compute_conjunction_candidate(
                p, s, horizon_minutes=horizon_minutes
            )
            if candidate:
                conjunctions.append(candidate)

        logger.info(f"Stage 2 Fine Screening: Identified {len(conjunctions)} potential close approach candidates.")
        return conjunctions
