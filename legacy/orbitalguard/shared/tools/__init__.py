"""
OrbitalGuard Phase 1 Deterministic Tool Layer.

Clean, type-safe, independently callable interfaces to the platform's
authoritative astrodynamic, physical, and hazard analysis capabilities:

1. get_object_state: SGP4 propagation to target epoch with TEME state vector.
2. propagate_trajectory: Ephemeris generation over future time horizon.
3. compute_pc: Foster (1992) 2D probability of collision integration.
4. run_screening: 2-stage coarse altitude filter + SGP4 fine refinement.
5. assess_risk: Multi-factor composite hazard index & tier classification.
6. generate_maneuver_candidates: Clohessy-Wiltshire SLSQP fuel-optimal burns.
7. evaluate_maneuver_constraints: Multi-constraint validation (Δv, Pc, drift, ground stations).
8. check_ground_station_visibility: Polar station AOS/LOS visibility windows.
9. get_space_weather: Live NOAA SWPC indices & thermospheric drag scalar.
"""

from shared.tools.errors import (
    ToolError,
    ObjectNotFoundError,
    ConjunctionNotFoundError,
    ComputationError,
    ConstraintViolationError,
    DataSourceUnavailableError,
)
from shared.tools.models import (
    PcComputationResult,
    ManeuverConstraintEvaluation,
)
from shared.tools.object_state import get_object_state
from shared.tools.trajectory import propagate_trajectory
from shared.tools.collision_probability import compute_pc
from shared.tools.screening import run_screening
from shared.tools.risk_assessment import assess_risk
from shared.tools.maneuver_candidates import generate_maneuver_candidates
from shared.tools.maneuver_constraints import evaluate_maneuver_constraints
from shared.tools.ground_station import check_ground_station_visibility, list_ground_stations
from shared.tools.space_weather import get_space_weather

__all__ = [
    # Exceptions
    "ToolError",
    "ObjectNotFoundError",
    "ConjunctionNotFoundError",
    "ComputationError",
    "ConstraintViolationError",
    "DataSourceUnavailableError",
    # Models
    "PcComputationResult",
    "ManeuverConstraintEvaluation",
    # Tools (Phase 1)
    "get_object_state",
    "propagate_trajectory",
    "compute_pc",
    "run_screening",
    "assess_risk",
    "generate_maneuver_candidates",
    "evaluate_maneuver_constraints",
    "check_ground_station_visibility",
    "list_ground_stations",
    "get_space_weather",
]
