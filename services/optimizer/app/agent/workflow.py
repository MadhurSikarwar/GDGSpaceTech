"""
OrbitalGuard Phase 3 — Adaptive Workflow Manager.

Implements risk-tier-driven conditional branching and the iterative maneuver
generate→simulate→evaluate→reject/retry loop.

ARCHITECTURAL RULES:
- Reuses existing Phase 1 tools (no physics reimplementation).
- Reuses existing Phase 2 DecisionContext and ToolRegistry.
- Risk tiers (LOW/MEDIUM/HIGH/CRITICAL) come exclusively from the deterministic scoring.
- LLM only orchestrates; all feasibility decisions are authoritative deterministic results.
- No persistent memory (Phase 4 concern).
- No Phase 5 UI logic.
"""

import logging
from enum import Enum
from typing import Optional, List, Dict, Any

from services.optimizer.app.agent.state import DecisionContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Workflow states — maps to Phase 3 iteration lifecycle
# ---------------------------------------------------------------------------

class WorkflowState(str, Enum):
    """Explicit lifecycle states for the adaptive workflow."""
    INITIALIZED = "INITIALIZED"

    # Assessment branch
    ASSESSING_RISK = "ASSESSING_RISK"
    RISK_ASSESSED = "RISK_ASSESSED"

    # LOW branch
    MONITOR_ONLY = "MONITOR_ONLY"

    # MEDIUM branch
    DEEPER_REASSESSMENT = "DEEPER_REASSESSMENT"
    REASSESSMENT_COMPLETE = "REASSESSMENT_COMPLETE"

    # HIGH / CRITICAL branch
    GENERATING_CANDIDATES = "GENERATING_CANDIDATES"
    EVALUATING_CANDIDATES = "EVALUATING_CANDIDATES"
    ITERATING_RETRY = "ITERATING_RETRY"
    COMPARING_CANDIDATES = "COMPARING_CANDIDATES"

    # Terminals
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    NO_FEASIBLE_MANEUVER = "NO_FEASIBLE_MANEUVER"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    REQUIRED_DATA_UNAVAILABLE = "REQUIRED_DATA_UNAVAILABLE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Candidate failure reasons — explicit, never faked
# ---------------------------------------------------------------------------

class CandidateFailureReason(str, Enum):
    """Explicit deterministic failure reason for a rejected maneuver candidate."""
    PC_STILL_TOO_HIGH = "PC_STILL_TOO_HIGH"
    DELTA_V_TOO_HIGH = "DELTA_V_TOO_HIGH"
    DRIFT_CONSTRAINT_VIOLATED = "DRIFT_CONSTRAINT_VIOLATED"
    GROUND_STATION_CONSTRAINT_VIOLATED = "GROUND_STATION_CONSTRAINT_VIOLATED"
    SLOT_CONSTRAINT_VIOLATED = "SLOT_CONSTRAINT_VIOLATED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    NO_FEASIBLE_MANEUVER = "NO_FEASIBLE_MANEUVER"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    REQUIRED_DATA_UNAVAILABLE = "REQUIRED_DATA_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Risk-tier allowed actions — guide (not dictate) the orchestrator
# ---------------------------------------------------------------------------

RISK_TIER_ALLOWED_TOOLS: Dict[str, List[str]] = {
    "LOW": [
        "get_object_state",
        "propagate_trajectory",
        "compute_pc",
        "assess_risk",
        "get_space_weather",
        "check_ground_station_visibility",
        "run_screening",
    ],
    "MEDIUM": [
        "get_object_state",
        "propagate_trajectory",
        "compute_pc",
        "assess_risk",
        "get_space_weather",
        "check_ground_station_visibility",
        "run_screening",
        "generate_maneuver_candidates",
        "evaluate_maneuver_constraints",
    ],
    "HIGH": [
        "get_object_state",
        "propagate_trajectory",
        "compute_pc",
        "assess_risk",
        "get_space_weather",
        "check_ground_station_visibility",
        "run_screening",
        "generate_maneuver_candidates",
        "evaluate_maneuver_constraints",
    ],
    "CRITICAL": [
        "get_object_state",
        "propagate_trajectory",
        "compute_pc",
        "assess_risk",
        "get_space_weather",
        "check_ground_station_visibility",
        "run_screening",
        "generate_maneuver_candidates",
        "evaluate_maneuver_constraints",
    ],
}


# ---------------------------------------------------------------------------
# Candidate status tracker — per candidate within a workflow run
# ---------------------------------------------------------------------------

class CandidateEvaluationRecord:
    """Lightweight record of a single candidate's evaluation outcome."""

    def __init__(self, candidate_id: str):
        self.candidate_id = candidate_id
        self.evaluated: bool = False
        self.feasible: Optional[bool] = None
        self.failure_reason: Optional[CandidateFailureReason] = None
        self.failure_detail: str = ""

    def mark_feasible(self):
        self.evaluated = True
        self.feasible = True

    def mark_rejected(self, reason: CandidateFailureReason, detail: str = ""):
        self.evaluated = True
        self.feasible = False
        self.failure_reason = reason
        self.failure_detail = detail


# ---------------------------------------------------------------------------
# Adaptive Workflow Manager
# ---------------------------------------------------------------------------

class AdaptiveWorkflowManager:
    """
    Manages the Phase 3 adaptive, risk-tier-conditioned workflow.

    Responsibilities:
    1. Determine the initial workflow envelope from the risk tier.
    2. Enforce the generate -> simulate -> evaluate -> reject/retry loop.
    3. Track candidate evaluation status within the current decision context.
    4. Enforce loop safety limits (max retries, max iterations).
    5. Return explicit failure states — never fabricate success.
    """

    def __init__(
        self,
        max_maneuver_retries: int = 6,
        max_workflow_iterations: int = 12,
    ):
        self.max_maneuver_retries = max_maneuver_retries
        self.max_workflow_iterations = max_workflow_iterations

        # Per-run candidate tracking (not persisted — Phase 4 concern)
        self._candidate_records: Dict[str, CandidateEvaluationRecord] = {}
        self._maneuver_retry_count: int = 0
        self._workflow_state: WorkflowState = WorkflowState.INITIALIZED
        self._evaluated_candidate_ids: List[str] = []
        self._generation_attempts: int = 0
        self._max_generation_attempts: int = 3

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def workflow_state(self) -> WorkflowState:
        return self._workflow_state

    @property
    def maneuver_retry_count(self) -> int:
        return self._maneuver_retry_count

    def get_candidate_record(self, candidate_id: str) -> Optional[CandidateEvaluationRecord]:
        return self._candidate_records.get(candidate_id)

    def get_all_candidate_records(self) -> Dict[str, CandidateEvaluationRecord]:
        return dict(self._candidate_records)

    # ------------------------------------------------------------------
    # Risk-tier workflow guidance
    # ------------------------------------------------------------------

    def get_risk_tier(self, context: DecisionContext) -> Optional[str]:
        """Return the risk tier from context if risk has been assessed."""
        if context.risk_assessment:
            return context.risk_assessment.risk_level
        return None

    def get_allowed_tools_for_tier(self, tier: Optional[str]) -> List[str]:
        """Return the set of tools appropriate for the given risk tier."""
        if tier is None:
            return ["assess_risk", "compute_pc", "get_space_weather",
                    "get_object_state", "propagate_trajectory", "run_screening",
                    "check_ground_station_visibility"]
        return RISK_TIER_ALLOWED_TOOLS.get(tier, list(RISK_TIER_ALLOWED_TOOLS["CRITICAL"]))

    def is_tool_allowed_for_context(self, tool_name: str, context: DecisionContext) -> bool:
        """Check whether a tool call is appropriate for the current risk tier."""
        tier = self.get_risk_tier(context)
        return tool_name in self.get_allowed_tools_for_tier(tier)

    def requires_maneuver_generation(self, context: DecisionContext) -> bool:
        """Return True if risk tier warrants maneuver generation (HIGH or CRITICAL)."""
        tier = self.get_risk_tier(context)
        if tier is None:
            return False
        return tier in ("HIGH", "CRITICAL")

    def requires_deeper_reassessment(self, context: DecisionContext) -> bool:
        """Return True if MEDIUM risk calls for additional analysis tools."""
        tier = self.get_risk_tier(context)
        return tier == "MEDIUM"

    def is_monitor_only(self, context: DecisionContext) -> bool:
        """Return True if risk is LOW and no maneuver workflow is needed."""
        tier = self.get_risk_tier(context)
        if tier != "LOW":
            return False
        if context.risk_assessment and context.risk_assessment.collision_probability is not None:
            from services.risk.app.scoring import PC_TIER_HIGH
            if context.risk_assessment.collision_probability >= PC_TIER_HIGH:
                return False
        return True

    # ------------------------------------------------------------------
    # Workflow state management
    # ------------------------------------------------------------------

    def transition_to(self, new_state: WorkflowState):
        """Transition the workflow FSM to a new state."""
        logger.info(f"Workflow transition: {self._workflow_state} -> {new_state}")
        self._workflow_state = new_state

    def determine_initial_workflow_state(self, context: DecisionContext) -> WorkflowState:
        """Determine the appropriate initial workflow branch based on risk tier."""
        tier = self.get_risk_tier(context)
        if tier is None:
            self.transition_to(WorkflowState.ASSESSING_RISK)
        elif tier == "LOW":
            self.transition_to(WorkflowState.MONITOR_ONLY)
        elif tier == "MEDIUM":
            self.transition_to(WorkflowState.DEEPER_REASSESSMENT)
        elif tier in ("HIGH", "CRITICAL"):
            self.transition_to(WorkflowState.GENERATING_CANDIDATES)
        else:
            self.transition_to(WorkflowState.ASSESSING_RISK)
        return self._workflow_state

    # ------------------------------------------------------------------
    # Iterative candidate evaluation loop helpers
    # ------------------------------------------------------------------

    def record_candidate_result(
        self,
        candidate_id: str,
        feasible: bool,
        evaluation,
        context: DecisionContext,
    ) -> CandidateEvaluationRecord:
        """
        Record the deterministic evaluation result for a single candidate.
        Derives the explicit failure reason from the authoritative evaluation fields.
        Never fabricates success.
        """
        record = self._candidate_records.get(candidate_id)
        if record is None:
            record = CandidateEvaluationRecord(candidate_id)
            self._candidate_records[candidate_id] = record

        if feasible:
            record.mark_feasible()
            if candidate_id not in self._evaluated_candidate_ids:
                self._evaluated_candidate_ids.append(candidate_id)
            logger.info(f"Candidate {candidate_id}: FEASIBLE")
        else:
            reason, detail = self._derive_failure_reason(evaluation)
            record.mark_rejected(reason, detail)
            if candidate_id not in self._evaluated_candidate_ids:
                self._evaluated_candidate_ids.append(candidate_id)
            logger.info(f"Candidate {candidate_id}: REJECTED - {reason}: {detail}")

        return record

    def _derive_failure_reason(self, evaluation) -> tuple:
        """
        Extract the primary deterministic failure reason from a ManeuverConstraintEvaluation.
        Precedence: Pc > Delta-V > drift/slot > ground-station > unknown.
        """
        if evaluation is None:
            return CandidateFailureReason.SIMULATION_FAILED, "Evaluation result unavailable."

        if evaluation.pc_satisfied is False:
            pc_val = evaluation.predicted_pc
            threshold = evaluation.pc_threshold
            detail = f"Pc={pc_val:.2e} exceeds threshold {threshold:.2e}" if pc_val is not None else "Pc check failed."
            return CandidateFailureReason.PC_STILL_TOO_HIGH, detail

        if not evaluation.delta_v_satisfied:
            detail = f"Delta-v={evaluation.delta_v_m_s:.2f} m/s exceeds cap {evaluation.max_delta_v_m_s:.2f} m/s"
            return CandidateFailureReason.DELTA_V_TOO_HIGH, detail

        if evaluation.slot_drift_satisfied is False:
            drift = evaluation.sma_drift_km
            max_d = evaluation.max_sma_drift_km
            detail = f"SMA drift={drift:.2f} km exceeds limit {max_d:.2f} km"
            return CandidateFailureReason.DRIFT_CONSTRAINT_VIOLATED, detail

        if evaluation.ground_station_visibility_checked and evaluation.ground_station_satisfied is False:
            detail = "No ground station pass windows found during evaluation horizon."
            return CandidateFailureReason.GROUND_STATION_CONSTRAINT_VIOLATED, detail

        return CandidateFailureReason.SLOT_CONSTRAINT_VIOLATED, f"Overall status: {evaluation.overall_status}"

    def should_retry_maneuver(self, context: DecisionContext) -> bool:
        """
        Return True if the agent should attempt to evaluate another candidate
        (or re-generate) after a rejection.
        """
        if self._maneuver_retry_count >= self.max_maneuver_retries:
            logger.warning("Maximum maneuver retry limit reached.")
            return False

        if context.maneuver_candidates and context.maneuver_candidates.candidates:
            unevaluated = [
                c for c in context.maneuver_candidates.candidates
                if c.maneuver_id not in self._evaluated_candidate_ids
            ]
            if unevaluated:
                return True

        if self._generation_attempts < self._max_generation_attempts:
            return True

        return False

    def increment_retry(self):
        """Increment the maneuver retry counter."""
        self._maneuver_retry_count += 1
        logger.info(f"Maneuver retry attempt {self._maneuver_retry_count}/{self.max_maneuver_retries}")

    def increment_generation_attempt(self):
        """Increment the candidate generation attempt counter."""
        self._generation_attempts += 1
        logger.info(f"Candidate generation attempt {self._generation_attempts}/{self._max_generation_attempts}")

    def get_next_unevaluated_candidate_id(self, context: DecisionContext) -> Optional[str]:
        """Return the ID of the next candidate that hasn't been evaluated yet."""
        if not context.maneuver_candidates or not context.maneuver_candidates.candidates:
            return None
        for c in context.maneuver_candidates.candidates:
            if c.maneuver_id not in self._evaluated_candidate_ids:
                return c.maneuver_id
        return None

    def all_candidates_exhausted(self, context: DecisionContext) -> bool:
        """Return True if all known candidates have been evaluated."""
        if not context.maneuver_candidates or not context.maneuver_candidates.candidates:
            return True
        return all(
            c.maneuver_id in self._evaluated_candidate_ids
            for c in context.maneuver_candidates.candidates
        )

    def has_feasible_candidate(self, context: DecisionContext) -> bool:
        """Return True if at least one feasible candidate has been identified."""
        return bool(context.feasible_candidate_ids)

    # ------------------------------------------------------------------
    # Deterministic workflow step — fallback when LLM is unavailable
    # ------------------------------------------------------------------

    def next_deterministic_action(self, context: DecisionContext) -> Dict[str, Any]:
        """
        Produce the next required deterministic action based on context state.
        Used when the LLM is unavailable or to guide the agent.

        Returns a dict compatible with the LLM action format.
        """
        tier = self.get_risk_tier(context)

        # Step 0: Always ensure risk is assessed first
        if context.risk_assessment is None:
            self.transition_to(WorkflowState.ASSESSING_RISK)
            return {
                "action": "call_tool",
                "tool_name": "assess_risk",
                "tool_args": {"conjunction_id": context.conjunction_id},
                "reasoning": "Risk assessment is required before workflow branch selection.",
            }

        # LOW branch: monitor only, no maneuver
        if self.is_monitor_only(context):
            self.transition_to(WorkflowState.MONITOR_ONLY)
            return {
                "action": "make_decision",
                "selected_maneuver_id": "NONE",
                "explanation": (
                    f"Risk tier is LOW (score={context.risk_assessment.risk_score}/100). "
                    "No avoidance maneuver required. Continued routine monitoring recommended."
                ),
            }

        # MEDIUM branch: deeper analysis
        if tier == "MEDIUM":
            self.transition_to(WorkflowState.DEEPER_REASSESSMENT)
            if context.space_weather is None:
                return {
                    "action": "call_tool",
                    "tool_name": "get_space_weather",
                    "tool_args": {},
                    "reasoning": "MEDIUM risk: fetch thermospheric drag conditions for deeper assessment.",
                }
            if context.pc_result is None and context.conjunction_candidate:
                return {
                    "action": "call_tool",
                    "tool_name": "compute_pc",
                    "tool_args": {
                        "conjunction_id": context.conjunction_id,
                        "primary_catalog_id": context.primary_object_id,
                        "secondary_catalog_id": context.secondary_object_id or "",
                    },
                    "reasoning": "MEDIUM risk: compute precise Foster Pc for updated assessment.",
                }
            if not context.ground_station_passes:
                return {
                    "action": "call_tool",
                    "tool_name": "check_ground_station_visibility",
                    "tool_args": {"catalog_id": context.primary_object_id},
                    "reasoning": "MEDIUM risk: verify ground station contact windows.",
                }
            self.transition_to(WorkflowState.REASSESSMENT_COMPLETE)
            return {
                "action": "make_decision",
                "selected_maneuver_id": "NONE",
                "explanation": (
                    f"MEDIUM risk - deeper reassessment complete. "
                    f"Risk score: {context.risk_assessment.risk_score}/100. "
                    f"Space weather Kp={context.space_weather.kp_index if context.space_weather else 'N/A'}. "
                    "Deterministic tools confirm no immediate maneuver warranted; monitoring recommended."
                ),
            }

        # HIGH / CRITICAL branch
        if tier in ("HIGH", "CRITICAL"):
            if context.space_weather is None:
                return {
                    "action": "call_tool",
                    "tool_name": "get_space_weather",
                    "tool_args": {},
                    "reasoning": f"{tier} risk: fetch space weather before maneuver generation.",
                }

            if not context.maneuver_candidates or not context.maneuver_candidates.candidates:
                self.transition_to(WorkflowState.GENERATING_CANDIDATES)
                self.increment_generation_attempt()
                return {
                    "action": "call_tool",
                    "tool_name": "generate_maneuver_candidates",
                    "tool_args": {
                        "conjunction_id": context.conjunction_id,
                        "satellite_id": context.primary_object_id,
                    },
                    "reasoning": f"{tier} risk: generate avoidance maneuver candidates via CW/SLSQP.",
                }

            if not context.constraint_evaluations:
                self.transition_to(WorkflowState.EVALUATING_CANDIDATES)
                return {
                    "action": "call_tool",
                    "tool_name": "evaluate_maneuver_constraints",
                    "tool_args": {"conjunction_id": context.conjunction_id},
                    "reasoning": "Evaluate all candidates against delta-v, Pc, drift, and ground-station constraints.",
                }

            feasible = context.feasible_candidate_ids
            infeasible = context.infeasible_candidate_ids

            if feasible:
                self.transition_to(WorkflowState.COMPARING_CANDIDATES)
                all_candidates = context.maneuver_candidates.candidates
                feasible_objs = [c for c in all_candidates if c.maneuver_id in feasible]
                low_risk = [c for c in feasible_objs if c.resulting_risk == "LOW"]
                chosen = (
                    min(low_risk, key=lambda c: c.delta_v_m_s)
                    if low_risk
                    else min(feasible_objs, key=lambda c: c.delta_v_m_s)
                )
                pc_str = f"Pc={chosen.predicted_pc:.2e}" if chosen.predicted_pc is not None else f"risk={chosen.resulting_risk}"
                explanation = (
                    f"Selected {chosen.maneuver_id} ({chosen.burn_direction} {chosen.delta_v_m_s:.2f} m/s) "
                    f"-> {chosen.new_separation_km:.1f} km separation ({pc_str}). "
                    f"Feasible: {len(feasible)}/{len(feasible)+len(infeasible)} candidates satisfied constraints. "
                    "Escalated for human approval."
                )
                self.transition_to(WorkflowState.RECOMMENDATION_READY)
                return {
                    "action": "make_decision",
                    "selected_maneuver_id": chosen.maneuver_id,
                    "explanation": explanation,
                }
            else:
                reasons = "; ".join([f"{m}: {r}" for m, r in context.rejection_reasons.items()])
                self.transition_to(WorkflowState.NO_FEASIBLE_MANEUVER)
                return {
                    "action": "make_decision",
                    "selected_maneuver_id": "NO_FEASIBLE_MANEUVER",
                    "explanation": (
                        f"NO_FEASIBLE_MANEUVER: All {len(infeasible)} candidate(s) violated deterministic constraints. "
                        f"Violations: {reasons}. Human flight director intervention required."
                    ),
                }

        # Fallback
        return {
            "action": "call_tool",
            "tool_name": "assess_risk",
            "tool_args": {"conjunction_id": context.conjunction_id},
            "reasoning": "Unknown state - falling back to risk assessment.",
        }

    # ------------------------------------------------------------------
    # Context annotation helpers
    # ------------------------------------------------------------------

    def annotate_context_with_workflow_state(self, context: DecisionContext):
        """Write current workflow state into the evidence_summary of the context."""
        context.evidence_summary["workflow_state"] = self._workflow_state.value
        context.evidence_summary["maneuver_retry_count"] = self._maneuver_retry_count
        context.evidence_summary["generation_attempts"] = self._generation_attempts
        context.evidence_summary["evaluated_candidate_ids"] = list(self._evaluated_candidate_ids)
        context.evidence_summary["candidate_results"] = {
            cid: {
                "feasible": r.feasible,
                "failure_reason": r.failure_reason.value if r.failure_reason else None,
                "failure_detail": r.failure_detail,
            }
            for cid, r in self._candidate_records.items()
        }

    def build_workflow_context_hint(self, context: DecisionContext) -> str:
        """
        Build a text snippet describing the current workflow state for inclusion
        in the LLM context summary.
        """
        tier = self.get_risk_tier(context)
        lines = [
            "[PHASE 3 ADAPTIVE WORKFLOW]",
            f"Risk Tier: {tier or 'NOT YET ASSESSED'}",
            f"Workflow State: {self._workflow_state.value}",
            f"Maneuver Retries Used: {self._maneuver_retry_count}/{self.max_maneuver_retries}",
            f"Generation Attempts: {self._generation_attempts}/{self._max_generation_attempts}",
        ]

        if tier == "LOW":
            lines.append("Guidance: LOW risk - do NOT generate maneuver candidates. Return MONITOR-ONLY recommendation.")
        elif tier == "MEDIUM":
            lines.append(
                "Guidance: MEDIUM risk - perform deeper analysis (compute_pc, get_space_weather, "
                "check_ground_station_visibility) before deciding whether a maneuver is needed."
            )
        elif tier in ("HIGH", "CRITICAL"):
            lines.append(f"Guidance: {tier} risk - generate and evaluate maneuver candidates.")
            if self._evaluated_candidate_ids:
                lines.append(f"Already evaluated: {self._evaluated_candidate_ids}")
            unevaluated = self.get_next_unevaluated_candidate_id(context)
            if unevaluated:
                lines.append(f"Next unevaluated candidate: {unevaluated}")

        if self._candidate_records:
            rejection_summary = [
                f"{cid}: {r.failure_reason.value if r.failure_reason else 'FEASIBLE'}"
                for cid, r in self._candidate_records.items()
            ]
            lines.append(f"Candidate outcomes: {', '.join(rejection_summary)}")

        return "\n".join(lines)
