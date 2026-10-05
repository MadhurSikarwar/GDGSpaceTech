"""
Tool: assess_risk
Evaluates conjunction encounter hazard and computes deterministic risk score and risk tier.
Reuses existing services/risk/app/scoring.py assess_conjunction_risk implementation.
"""

from datetime import datetime
from typing import Optional

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.risk import RiskAssessment
from shared.tools.errors import ConjunctionNotFoundError, ComputationError
from services.risk.app.scoring import assess_conjunction_risk
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions, save_risk_assessment_to_db


def assess_risk(
    conjunction_id: Optional[str] = None,
    candidate: Optional[ConjunctionCandidate] = None,
    ref_time: Optional[datetime] = None,
    save_to_database: bool = False,
) -> RiskAssessment:
    """
    Assess hazard risk for a space object conjunction candidate.

    Evaluates distance, time urgency, relative velocity, and Foster Pc collision probability
    using the authoritative deterministic multi-factor hazard scoring model.

    Args:
        conjunction_id: Unique conjunction event identifier (retrieves record from DB or fixtures).
        candidate: Direct ConjunctionCandidate instance.
        ref_time: Reference evaluation time (UTC). Defaults to current UTC time.
        save_to_database: If True, persists assessment outcome to database if reachable.

    Returns:
        RiskAssessment containing risk_score (0-100), risk_level (CRITICAL/HIGH/MEDIUM/LOW),
        hazard factors, and diagnostic notes.

    Raises:
        ValueError: If neither conjunction_id nor candidate is provided.
        ConjunctionNotFoundError: If conjunction_id cannot be found in database or fixtures.
        ComputationError: If risk assessment scoring fails.
    """
    target_candidate: Optional[ConjunctionCandidate] = candidate

    if target_candidate is None:
        if not conjunction_id:
            raise ValueError("Either conjunction_id or a candidate ConjunctionCandidate must be provided.")

        # 1. Try local database
        target_candidate = get_conjunction_from_db(conjunction_id)

        # 2. Try fixtures fallback
        if target_candidate is None:
            fixtures = load_fixture_conjunctions()
            for f in fixtures:
                if f.conjunction_id == conjunction_id:
                    target_candidate = f
                    break

        if target_candidate is None:
            raise ConjunctionNotFoundError(
                f"Conjunction candidate '{conjunction_id}' could not be found in database or fixtures."
            )

    try:
        assessment = assess_conjunction_risk(target_candidate, ref_time=ref_time)

        if save_to_database:
            save_risk_assessment_to_db(assessment)

        return assessment
    except Exception as e:
        raise ComputationError(f"Risk assessment calculation failed: {e}") from e
