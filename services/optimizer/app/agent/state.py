"""
Decision Agent Context and State Schemas.
Represents the explicit decision context for a single run of the agent.
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
    
    # Final verified natural language justification
    explanation: Optional[str] = None
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
