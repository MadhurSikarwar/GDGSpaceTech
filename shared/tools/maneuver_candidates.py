"""
Tool: generate_maneuver_candidates
Generates collision avoidance maneuver options using the existing CW linearized
relative motion model and SciPy SLSQP optimization solver.
Reuses services/maneuver/app/main.py and delta_v_optimizer.py implementations.
"""

from typing import Optional, List

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.tools.errors import ConjunctionNotFoundError, ComputationError
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions
from services.risk.app.scoring import assess_conjunction_risk, PC_TIER_HIGH
from services.maneuver.app.main import (
    _generate_optimizer_candidates,
    _generate_heuristic_candidates,
    save_maneuver_candidates_to_db,
)


def generate_maneuver_candidates(
    conjunction_id: Optional[str] = None,
    candidate: Optional[ConjunctionCandidate] = None,
    satellite_id: Optional[str] = None,
    allow_heuristic_fallback: bool = True,
    save_to_database: bool = False,
) -> ManeuverCandidates:
    """
    Generate fuel-optimal and directional collision avoidance maneuver candidates.

    Uses Clohessy-Wiltshire (CW) equations with SciPy SLSQP optimizer to find the
    minimum delta-V burn satisfying Pc < threshold and semi-major axis drift constraints.

    Args:
        conjunction_id: Unique conjunction candidate event ID.
        candidate: Direct ConjunctionCandidate instance.
        satellite_id: Primary satellite catalog ID override.
        allow_heuristic_fallback: If False, raises ComputationError if SLSQP optimizer cannot converge.
        save_to_database: If True, persists candidate options to database.

    Returns:
        ManeuverCandidates containing evaluated candidate options with burn vectors,
        predicted post-burn separations, and resulting risk levels.

    Raises:
        ValueError: If neither conjunction_id nor candidate is provided.
        ConjunctionNotFoundError: If conjunction candidate cannot be found.
        ComputationError: If optimizer fails and allow_heuristic_fallback is False.
    """
    target_candidate: Optional[ConjunctionCandidate] = candidate

    if target_candidate is None:
        if not conjunction_id:
            raise ValueError("Either conjunction_id or a ConjunctionCandidate must be provided.")

        target_candidate = get_conjunction_from_db(conjunction_id)

        if target_candidate is None:
            fixtures = load_fixture_conjunctions()
            for f in fixtures:
                if f.conjunction_id == conjunction_id:
                    target_candidate = f
                    break

        if target_candidate is None:
            raise ConjunctionNotFoundError(
                f"Conjunction candidate '{conjunction_id}' not found in database or fixtures."
            )

    target_sat_id = satellite_id or target_candidate.primary_object
    assessment = assess_conjunction_risk(target_candidate)

    # Risk gate: if risk is already LOW, no avoidance maneuver is required
    pc = assessment.collision_probability
    needs_maneuver = (pc >= PC_TIER_HIGH) if pc is not None else (assessment.risk_level != "LOW")
    if not needs_maneuver:
        return ManeuverCandidates(
            conjunction_id=target_candidate.conjunction_id,
            primary_object=target_sat_id,
            candidates=[]
        )

    # Run authoritative SLSQP physics optimizer
    candidates: Optional[List[ManeuverCandidate]] = None
    try:
        candidates = _generate_optimizer_candidates(target_candidate)
    except Exception as exc:
        if not allow_heuristic_fallback:
            raise ComputationError(f"SLSQP Delta-V optimizer execution failed: {exc}") from exc
        candidates = None

    if candidates is None:
        if not allow_heuristic_fallback:
            raise ComputationError(
                f"SLSQP Delta-V optimizer could not find a feasible solution for conjunction '{target_candidate.conjunction_id}'."
            )
        candidates = _generate_heuristic_candidates(
            assessment.factors.closest_approach_km,
            assessment.factors.time_to_tca_minutes,
        )

    result = ManeuverCandidates(
        conjunction_id=target_candidate.conjunction_id,
        primary_object=target_sat_id,
        candidates=candidates,
    )

    if save_to_database:
        save_maneuver_candidates_to_db(result)

    return result
