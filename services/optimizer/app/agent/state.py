"""
Decision Agent Context and State Schemas.
Represents the explicit decision context for a single run of the agent.
Phase 3 extensions: adaptive workflow state, iterative candidate tracking, failure states.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.risk import RiskAssessment
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.space_weather import SpaceWeatherSnapshot
from shared.schemas.ground_station import PassWindow
from shared.tools.models import PcComputationResult, ManeuverConstraintEvaluation


class ToolCallRecord(BaseModel):
    """Trace of an individual tool invocation during the agent loop."""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    summary: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: Optional[str] = None


class DecisionContext(BaseModel):
    """
    Explicit state context for a single Decision Agent orchestration session.
    Carries strictly verified factual evidence gathered from Phase 1 tools.
    """
    conjunction_id: str
    primary_object_id: str
    secondary_object_id: Optional[str] = None
    
    # Execution lifecycle status
    # INITIALIZED -> ANALYZING -> RECOMMENDATION_GENERATED -> AWAITING_HUMAN_APPROVAL
    # or NO_FEASIBLE_MANEUVER / INCOMPLETE_ANALYSIS / FAILED
    status: str = Field(default="INITIALIZED")
    current_stage: str = Field(default="ASSESSMENT")
    iteration_count: int = 0
    max_iterations: int = 8

    # Phase 3: Adaptive workflow state tracking
    risk_tier: Optional[str] = Field(
        default=None,
        description="Resolved risk tier (LOW/MEDIUM/HIGH/CRITICAL) from deterministic assessment."
    )
    workflow_state: str = Field(
        default="INITIALIZED",
        description="Current Phase 3 adaptive workflow state (e.g. MONITOR_ONLY, GENERATING_CANDIDATES, NO_FEASIBLE_MANEUVER)."
    )
    # Phase 3: Iterative maneuver loop tracking
    maneuver_retry_count: int = Field(
        default=0,
        description="Number of candidate evaluation retries performed in this workflow run."
    )
    max_maneuver_retries: int = Field(
        default=6,
        description="Maximum allowed maneuver candidate evaluation retries before NO_FEASIBLE_MANEUVER."
    )
    candidate_generation_attempts: int = Field(
        default=0,
        description="Number of times candidate generation has been attempted."
    )
    # Phase 3: Explicit failure state
    failure_state: Optional[str] = Field(
        default=None,
        description=(
            "Explicit deterministic failure state when no feasible maneuver can be found: "
            "PC_STILL_TOO_HIGH | DELTA_V_TOO_HIGH | DRIFT_CONSTRAINT_VIOLATED | "
            "GROUND_STATION_CONSTRAINT_VIOLATED | SLOT_CONSTRAINT_VIOLATED | "
            "SIMULATION_FAILED | NO_FEASIBLE_MANEUVER | MAX_ITERATIONS_REACHED | REQUIRED_DATA_UNAVAILABLE"
        )
    )
    # Phase 3: Per-candidate rejection reasons (with structured failure code)
    candidate_failure_reasons: Dict[str, str] = Field(
        default_factory=dict,
        description="Maps candidate_id -> CandidateFailureReason enum value (explicit, not from LLM)."
    )
    
    # Audit trail of tool invocations
    tool_history: List[ToolCallRecord] = Field(default_factory=list)
    
    # Deterministic evidence gathered from Phase 1 tools
    conjunction_candidate: Optional[ConjunctionCandidate] = None
    risk_assessment: Optional[RiskAssessment] = None
    pc_result: Optional[PcComputationResult] = None
    space_weather: Optional[SpaceWeatherSnapshot] = None
    ground_station_passes: List[PassWindow] = Field(default_factory=list)
    maneuver_candidates: Optional[ManeuverCandidates] = None
    constraint_evaluations: Dict[str, ManeuverConstraintEvaluation] = Field(default_factory=dict)
    
    # Candidate comparison and selection
    feasible_candidate_ids: List[str] = Field(default_factory=list)
    infeasible_candidate_ids: List[str] = Field(default_factory=list)
    rejection_reasons: Dict[str, str] = Field(default_factory=dict)
    selected_candidate: Optional[ManeuverCandidate] = None
    selected_maneuver_id: Optional[str] = None
    
    # Human approval gate
    human_approval_required: bool = True
    approval_status: str = Field(default="PENDING")
    historical_feedback: List[Dict[str, Any]] = Field(default_factory=list, description="Historical human approval/rejection feedback for candidates associated with this conjunction.")
    
    # Final verified natural language justification
    explanation: Optional[str] = None
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
