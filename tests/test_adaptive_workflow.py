"""
Tests for OrbitalGuard Phase 3 — Adaptive Workflow + Iterative Maneuver Loop.

Test coverage:
 1.  LOW risk -> monitor-only, no maneuver generation.
 2.  MEDIUM risk -> triggers deeper reassessment tools.
 3.  HIGH risk -> triggers maneuver generation and evaluation.
 4.  CRITICAL risk -> generates/evaluates multiple candidates.
 5.  Feasible candidate is accepted.
 6.  Infeasible candidate is rejected.
 7.  Pc remains too high -> candidate rejected with PC_STILL_TOO_HIGH.
 8.  Excessive delta-V -> candidate rejected with DELTA_V_TOO_HIGH.
 9.  Drift violation -> candidate rejected with DRIFT_CONSTRAINT_VIOLATED.
10.  Ground-station violation -> candidate rejected with GROUND_STATION_CONSTRAINT_VIOLATED.
11.  Slot violation -> candidate rejected.
12.  Simulation failure -> candidate rejected/failed appropriately.
13.  Agent retries after candidate rejection.
14.  Multiple candidates compared correctly; safest feasible selected.
15.  No feasible candidates -> NO_FEASIBLE_MANEUVER (explicit state).
16.  Maximum iteration limit respected.
17.  Existing risk thresholds unchanged (PC_TIER_CRITICAL, HIGH, MEDIUM from scoring.py).
18.  Deterministic results cannot be overridden by LLM.
19.  Existing Phase 1 and Phase 2 tests still pass (smoke test imports).
20.  Workflow state annotations are present in context evidence_summary.
21.  LOW risk disallows maneuver generation (tool restriction).
22.  Workflow transitions correctly through FSM states.
23.  AdaptiveWorkflowManager retry counter increments correctly.
24.  Multiple rejection reasons are all recorded.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.risk import RiskAssessment, RiskFactors, UncertaintyInfo
from shared.tools.models import ManeuverConstraintEvaluation
from services.optimizer.app.agent import (
    DecisionAgentOrchestrator,
    DecisionContext,
    ToolRegistry,
    AdaptiveWorkflowManager,
    WorkflowState,
    CandidateFailureReason,
)


# ============================================================================
# Shared fixtures
# ============================================================================

def _make_conjunction(pc: float, distance_km: float = 3.2, tca_offset_min: float = 45.0) -> ConjunctionCandidate:
    return ConjunctionCandidate(
        conjunction_id=f"CONJ-P3-{pc:.0e}",
        primary_object="25544",
        secondary_object="DEB-99999",
        primary_object_name="ISS",
        secondary_object_name="DEBRIS",
        tca=datetime.now(timezone.utc) + timedelta(minutes=tca_offset_min),
        closest_approach=ClosestApproach(distance_km=distance_km, relative_velocity_km_s=9.8),
        screening=ScreeningInfo(threshold_km=50.0),
        data_provenance=DataProvenance(),
        probability_of_collision=pc,
    )


def _make_risk_assessment(risk_level: str, pc: float) -> RiskAssessment:
    return RiskAssessment(
        conjunction_id="CONJ-P3",
        risk_score={"LOW": 10.0, "MEDIUM": 50.0, "HIGH": 75.0, "CRITICAL": 92.0}[risk_level],
        risk_level=risk_level,
        factors=RiskFactors(closest_approach_km=3.2, time_to_tca_minutes=45.0, relative_velocity_km_s=9.8),
        uncertainty=UncertaintyInfo(),
        collision_probability=pc,
    )


def _make_candidates(*specs) -> ManeuverCandidates:
    """Build ManeuverCandidates from (id, delta_v, predicted_pc, resulting_risk) tuples."""
    candidates = []
    for (mid, dv, pc, risk) in specs:
        candidates.append(
            ManeuverCandidate(
                maneuver_id=mid,
                delta_v_m_s=dv,
                burn_direction="POSIGRADE",
                new_separation_km=20.0 + dv * 5,
                resulting_risk=risk,
                predicted_pc=pc,
            )
        )
    return ManeuverCandidates(
        conjunction_id="CONJ-P3",
        primary_object="25544",
        candidates=candidates,
    )


def _make_feasible_evaluation(maneuver_id: str, delta_v: float, pc: float = 1e-6) -> ManeuverConstraintEvaluation:
    return ManeuverConstraintEvaluation(
        maneuver_id=maneuver_id,
        is_feasible=True,
        overall_status="SATISFIED",
        delta_v_m_s=delta_v,
        max_delta_v_m_s=20.0,
        delta_v_satisfied=True,
        predicted_pc=pc,
        pc_threshold=1e-4,
        pc_satisfied=True,
        sma_drift_km=0.5,
        max_sma_drift_km=5.0,
        slot_drift_satisfied=True,
        ground_station_visibility_checked=False,
        ground_station_satisfied=None,
    )


def _make_infeasible_dv(maneuver_id: str, delta_v: float = 25.0) -> ManeuverConstraintEvaluation:
    return ManeuverConstraintEvaluation(
        maneuver_id=maneuver_id,
        is_feasible=False,
        overall_status="VIOLATED",
        delta_v_m_s=delta_v,
        max_delta_v_m_s=20.0,
        delta_v_satisfied=False,
        predicted_pc=1e-6,
        pc_threshold=1e-4,
        pc_satisfied=True,
        sma_drift_km=0.5,
        max_sma_drift_km=5.0,
        slot_drift_satisfied=True,
        ground_station_visibility_checked=False,
        ground_station_satisfied=None,
    )


def _make_infeasible_pc(maneuver_id: str, pc: float = 5e-4) -> ManeuverConstraintEvaluation:
    return ManeuverConstraintEvaluation(
        maneuver_id=maneuver_id,
        is_feasible=False,
        overall_status="VIOLATED",
        delta_v_m_s=1.5,
        max_delta_v_m_s=20.0,
        delta_v_satisfied=True,
        predicted_pc=pc,
        pc_threshold=1e-4,
        pc_satisfied=False,
        sma_drift_km=0.5,
        max_sma_drift_km=5.0,
        slot_drift_satisfied=True,
        ground_station_visibility_checked=False,
        ground_station_satisfied=None,
    )


def _make_infeasible_drift(maneuver_id: str, drift: float = 8.0) -> ManeuverConstraintEvaluation:
    return ManeuverConstraintEvaluation(
        maneuver_id=maneuver_id,
        is_feasible=False,
        overall_status="VIOLATED",
        delta_v_m_s=1.5,
        max_delta_v_m_s=20.0,
        delta_v_satisfied=True,
        predicted_pc=1e-6,
        pc_threshold=1e-4,
        pc_satisfied=True,
        sma_drift_km=drift,
        max_sma_drift_km=5.0,
        slot_drift_satisfied=False,
        ground_station_visibility_checked=False,
        ground_station_satisfied=None,
    )


def _make_infeasible_ground_station(maneuver_id: str) -> ManeuverConstraintEvaluation:
    return ManeuverConstraintEvaluation(
        maneuver_id=maneuver_id,
        is_feasible=False,
        overall_status="VIOLATED",
        delta_v_m_s=1.5,
        max_delta_v_m_s=20.0,
        delta_v_satisfied=True,
        predicted_pc=1e-6,
        pc_threshold=1e-4,
        pc_satisfied=True,
        sma_drift_km=0.5,
        max_sma_drift_km=5.0,
        slot_drift_satisfied=True,
        ground_station_visibility_checked=True,
        ground_station_satisfied=False,
    )


# ============================================================================
# Helper: build orchestrator with mocked tools for a specific risk scenario
# ============================================================================

def _build_orchestrator_with_mocked_tools(
    risk_level: str,
    pc: float,
    candidates: ManeuverCandidates = None,
    evaluations: dict = None,
) -> DecisionAgentOrchestrator:
    """
    Returns a DecisionAgentOrchestrator whose ToolRegistry is patched
    so that assess_risk returns a fixed RiskAssessment and
    evaluate_maneuver_constraints returns controlled evaluations.
    """
    orchestrator = DecisionAgentOrchestrator(max_iterations=10)

    risk_result = _make_risk_assessment(risk_level, pc)

    # Patch assess_risk
    orig_execute = orchestrator.registry.execute

    def patched_execute(name, args, context):
        if name == "assess_risk":
            context.risk_assessment = risk_result
            from services.optimizer.app.agent.state import ToolCallRecord
            record = ToolCallRecord(
                tool_name=name,
                arguments=args,
                success=True,
                summary=f"Risk: {risk_level} (Pc={pc:.2e})",
            )
            context.tool_history.append(record)
            return True, risk_result, record.summary

        if name == "generate_maneuver_candidates" and candidates is not None:
            context.maneuver_candidates = candidates
            from services.optimizer.app.agent.state import ToolCallRecord
            record = ToolCallRecord(
                tool_name=name,
                arguments=args,
                success=True,
                summary=f"Generated {len(candidates.candidates)} candidates",
            )
            context.tool_history.append(record)
            return True, candidates, record.summary

        if name == "evaluate_maneuver_constraints" and evaluations is not None:
            for mid, ev in evaluations.items():
                context.constraint_evaluations[mid] = ev
            feasible = [m for m, ev in evaluations.items() if ev.is_feasible]
            infeasible = [m for m, ev in evaluations.items() if not ev.is_feasible]
            context.feasible_candidate_ids = feasible
            context.infeasible_candidate_ids = infeasible
            for m_id, ev in evaluations.items():
                if not ev.is_feasible:
                    reasons = []
                    if not ev.delta_v_satisfied:
                        reasons.append(f"Dv {ev.delta_v_m_s}m/s > {ev.max_delta_v_m_s}m/s")
                    if ev.pc_satisfied is False:
                        reasons.append(f"Pc {ev.predicted_pc} > {ev.pc_threshold}")
                    if ev.slot_drift_satisfied is False:
                        reasons.append(f"drift {ev.sma_drift_km}km > {ev.max_sma_drift_km}km")
                    if ev.ground_station_visibility_checked and ev.ground_station_satisfied is False:
                        reasons.append("no ground station passes")
                    context.rejection_reasons[m_id] = ", ".join(reasons)
            summary = f"Evaluated {len(evaluations)}: {len(feasible)} feasible, {len(infeasible)} infeasible"
            from services.optimizer.app.agent.state import ToolCallRecord
            record = ToolCallRecord(
                tool_name=name,
                arguments=args,
                success=True,
                summary=summary,
            )
            context.tool_history.append(record)
            return True, evaluations, summary

        return orig_execute(name, args, context)

    orchestrator.registry.execute = patched_execute
    return orchestrator


# ============================================================================
# Test 1: LOW risk -> monitor-only
# ============================================================================

def test_low_risk_monitor_only():
    """LOW risk must produce a MONITOR-ONLY recommendation without generating maneuvers."""
    pc = 1e-8  # well below MEDIUM tier threshold
    conj = _make_conjunction(pc=pc, distance_km=25.0, tca_offset_min=120.0)
    orchestrator = _build_orchestrator_with_mocked_tools("LOW", pc)

    # LLM always triggers deterministic fallback
    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.risk_assessment is not None
    assert context.risk_assessment.risk_level == "LOW"
    assert context.selected_maneuver_id == "NONE"
    assert context.status in ("RECOMMENDATION_GENERATED",)
    assert context.human_approval_required is False
    # Must NOT have generated maneuver candidates
    tool_names = [t.tool_name for t in context.tool_history]
    assert "generate_maneuver_candidates" not in tool_names, (
        "LOW risk must not generate maneuver candidates"
    )


# ============================================================================
# Test 2: MEDIUM risk -> deeper reassessment tools
# ============================================================================

def test_medium_risk_deeper_reassessment():
    """MEDIUM risk must trigger deeper analysis tools before deciding."""
    pc = 5e-7  # MEDIUM tier (>= 1e-6 is HIGH, so just below)
    conj = _make_conjunction(pc=pc, distance_km=15.0)
    orchestrator = _build_orchestrator_with_mocked_tools("MEDIUM", pc)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.risk_assessment is not None
    assert context.risk_assessment.risk_level == "MEDIUM"
    tool_names = [t.tool_name for t in context.tool_history]
    # At minimum, space weather or compute_pc should have been requested for deeper analysis
    has_deeper_analysis = any(
        t in tool_names for t in ("get_space_weather", "compute_pc", "check_ground_station_visibility")
    )
    assert has_deeper_analysis, (
        f"MEDIUM risk must invoke at least one deeper-analysis tool. Got: {tool_names}"
    )


# ============================================================================
# Test 3: HIGH risk -> triggers maneuver generation and evaluation
# ============================================================================

def test_high_risk_generates_and_evaluates_maneuvers():
    """HIGH risk must generate and evaluate maneuver candidates."""
    pc = 5e-5  # >= PC_TIER_HIGH (1e-5)
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M1", 1.5, 1e-6, "LOW"), ("M2", 2.0, 2e-6, "LOW"))
    evaluations = {
        "M1": _make_feasible_evaluation("M1", 1.5),
        "M2": _make_feasible_evaluation("M2", 2.0),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("HIGH", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.risk_assessment.risk_level == "HIGH"
    tool_names = [t.tool_name for t in context.tool_history]
    assert "generate_maneuver_candidates" in tool_names
    assert "evaluate_maneuver_constraints" in tool_names
    assert context.selected_maneuver_id not in ("NONE", "NO_FEASIBLE_MANEUVER", None)


# ============================================================================
# Test 4: CRITICAL risk -> multiple candidates, compare, recommend safest
# ============================================================================

def test_critical_risk_multiple_candidates_compared():
    """CRITICAL risk must generate multiple candidates and select the safest feasible."""
    pc = 5e-4  # >= PC_TIER_CRITICAL (1e-4)
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M1", 3.5, 2e-6, "LOW"),
        ("M2", 1.2, 3e-6, "LOW"),
        ("M3", 5.0, 4e-6, "LOW"),
    )
    evaluations = {
        "M1": _make_feasible_evaluation("M1", 3.5),
        "M2": _make_feasible_evaluation("M2", 1.2),
        "M3": _make_feasible_evaluation("M3", 5.0),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.risk_assessment.risk_level == "CRITICAL"
    # Must have compared candidates and selected minimum delta-V feasible
    assert context.selected_maneuver_id == "M2"  # lowest delta-V feasible
    assert context.status == "AWAITING_HUMAN_APPROVAL"
    assert context.human_approval_required is True


# ============================================================================
# Test 5: Feasible candidate is accepted
# ============================================================================

def test_feasible_candidate_accepted():
    """A candidate satisfying all constraints must be accepted."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M_OK", 1.0, 1e-6, "LOW"))
    evaluations = {"M_OK": _make_feasible_evaluation("M_OK", 1.0)}
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "M_OK"
    assert "M_OK" in context.feasible_candidate_ids
    assert context.status == "AWAITING_HUMAN_APPROVAL"


# ============================================================================
# Test 6: Infeasible candidate is rejected
# ============================================================================

def test_infeasible_candidate_rejected():
    """A candidate violating delta-V must be rejected; feasible fallback selected."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M_BAD", 30.0, 1e-6, "LOW"),
        ("M_GOOD", 1.5, 1e-6, "LOW"),
    )
    evaluations = {
        "M_BAD": _make_infeasible_dv("M_BAD"),
        "M_GOOD": _make_feasible_evaluation("M_GOOD", 1.5),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert "M_BAD" in context.infeasible_candidate_ids
    assert context.selected_maneuver_id == "M_GOOD"


# ============================================================================
# Test 7: Pc remains too high -> candidate rejected with PC_STILL_TOO_HIGH
# ============================================================================

def test_pc_too_high_rejection():
    """Candidate with post-burn Pc above threshold must be rejected with PC_STILL_TOO_HIGH."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M_HIGHPC", 1.5, 5e-4, "CRITICAL"))
    evaluations = {"M_HIGHPC": _make_infeasible_pc("M_HIGHPC", pc=5e-4)}
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert "M_HIGHPC" in context.infeasible_candidate_ids
    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.status == "NO_FEASIBLE_MANEUVER"
    # Verify the workflow tracked this specific failure reason
    assert context.candidate_failure_reasons.get("M_HIGHPC") == CandidateFailureReason.PC_STILL_TOO_HIGH.value


# ============================================================================
# Test 8: Excessive delta-V -> candidate rejected with DELTA_V_TOO_HIGH
# ============================================================================

def test_delta_v_too_high_rejection():
    """Candidate exceeding delta-V budget must be rejected with DELTA_V_TOO_HIGH."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M_EXPNS", 25.0, 1e-6, "LOW"))
    evaluations = {"M_EXPNS": _make_infeasible_dv("M_EXPNS", delta_v=25.0)}
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.candidate_failure_reasons.get("M_EXPNS") == CandidateFailureReason.DELTA_V_TOO_HIGH.value


# ============================================================================
# Test 9: Drift violation -> candidate rejected with DRIFT_CONSTRAINT_VIOLATED
# ============================================================================

def test_drift_constraint_violation_rejection():
    """Candidate with excessive orbital drift must be rejected."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M_DRIFT", 2.0, 1e-6, "LOW"))
    evaluations = {"M_DRIFT": _make_infeasible_drift("M_DRIFT", drift=8.0)}
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.candidate_failure_reasons.get("M_DRIFT") == CandidateFailureReason.DRIFT_CONSTRAINT_VIOLATED.value


# ============================================================================
# Test 10: Ground-station constraint violated
# ============================================================================

def test_ground_station_constraint_violation():
    """Candidate violating ground-station visibility must be rejected."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(("M_GS", 1.5, 1e-6, "LOW"))
    evaluations = {"M_GS": _make_infeasible_ground_station("M_GS")}
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.candidate_failure_reasons.get("M_GS") == CandidateFailureReason.GROUND_STATION_CONSTRAINT_VIOLATED.value


# ============================================================================
# Test 11: Slot constraint violation
# ============================================================================

def test_slot_constraint_violation():
    """A candidate with drift barely exceeding limit must be rejected."""
    wfm = AdaptiveWorkflowManager()
    ev = _make_infeasible_drift("M_SLOT", drift=5.1)
    reason, detail = wfm._derive_failure_reason(ev)
    assert reason == CandidateFailureReason.DRIFT_CONSTRAINT_VIOLATED


# ============================================================================
# Test 12: Simulation failure -> handled explicitly
# ============================================================================

def test_simulation_failure_handled():
    """When evaluation result is None, SIMULATION_FAILED reason must be used."""
    wfm = AdaptiveWorkflowManager()
    reason, detail = wfm._derive_failure_reason(None)
    assert reason == CandidateFailureReason.SIMULATION_FAILED
    assert "unavailable" in detail.lower()


# ============================================================================
# Test 13: Agent retries after candidate rejection
# ============================================================================

def test_agent_retries_after_rejection():
    """When first candidate is rejected, agent must try the next one."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M_FIRST", 25.0, 1e-6, "LOW"),   # will fail delta-V
        ("M_SECOND", 1.5, 1e-6, "LOW"),    # feasible
    )
    evaluations = {
        "M_FIRST": _make_infeasible_dv("M_FIRST"),
        "M_SECOND": _make_feasible_evaluation("M_SECOND", 1.5),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    # M_FIRST rejected, M_SECOND chosen
    assert "M_FIRST" in context.infeasible_candidate_ids
    assert context.selected_maneuver_id == "M_SECOND"
    assert context.candidate_failure_reasons.get("M_FIRST") == CandidateFailureReason.DELTA_V_TOO_HIGH.value


# ============================================================================
# Test 14: Multiple candidates compared; minimum delta-V feasible selected
# ============================================================================

def test_multiple_candidates_minimum_dv_selected():
    """Among multiple feasible candidates, the one with minimum delta-V should be selected."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M_BIG", 10.0, 1e-6, "LOW"),
        ("M_MED", 5.0, 1e-6, "LOW"),
        ("M_SMALL", 1.1, 1e-6, "LOW"),
    )
    evaluations = {
        "M_BIG": _make_feasible_evaluation("M_BIG", 10.0),
        "M_MED": _make_feasible_evaluation("M_MED", 5.0),
        "M_SMALL": _make_feasible_evaluation("M_SMALL", 1.1),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "M_SMALL"


# ============================================================================
# Test 15: No feasible candidates -> NO_FEASIBLE_MANEUVER
# ============================================================================

def test_no_feasible_maneuver_explicit_state():
    """When all candidates fail, status must be NO_FEASIBLE_MANEUVER — never faked."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M_A", 25.0, 1e-6, "LOW"),
        ("M_B", 30.0, 1e-6, "LOW"),
    )
    evaluations = {
        "M_A": _make_infeasible_dv("M_A", 25.0),
        "M_B": _make_infeasible_dv("M_B", 30.0),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.status == "NO_FEASIBLE_MANEUVER"
    assert context.failure_state == CandidateFailureReason.NO_FEASIBLE_MANEUVER.value
    # The explanation may use either "NO FEASIBLE MANEUVER" or "NO_FEASIBLE_MANEUVER"
    expl_upper = (context.explanation or "").upper()
    assert "NO" in expl_upper and "FEASIBLE" in expl_upper and "MANEUVER" in expl_upper
    assert context.human_approval_required is True


# ============================================================================
# Test 16: Maximum iteration limit is respected
# ============================================================================

def test_max_iteration_limit_respected():
    """Agent must stop at max_iterations and not enter an infinite loop."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)

    # LLM always requests space weather (loop forever)
    def looping_llm(summary, tools, history):
        return {"action": "call_tool", "tool_name": "get_space_weather", "tool_args": {"force_refresh": True}}

    orchestrator = DecisionAgentOrchestrator(max_iterations=4)
    context = orchestrator.run(candidate_payload=conj, llm_override=looping_llm)

    assert context.iteration_count <= 4
    assert context.status in (
        "AWAITING_HUMAN_APPROVAL", "RECOMMENDATION_GENERATED",
        "ANALYZING", "INCOMPLETE_ANALYSIS", "NO_FEASIBLE_MANEUVER"
    )


# ============================================================================
# Test 17: Existing risk thresholds unchanged
# ============================================================================

def test_existing_risk_thresholds_unchanged():
    """PC_TIER_CRITICAL, PC_TIER_HIGH, PC_TIER_MEDIUM must match original values from scoring.py."""
    from services.risk.app.scoring import PC_TIER_CRITICAL, PC_TIER_HIGH, PC_TIER_MEDIUM
    assert PC_TIER_CRITICAL == 1.0e-4, "CRITICAL threshold must remain 1e-4"
    assert PC_TIER_HIGH == 1.0e-5, "HIGH threshold must remain 1e-5"
    assert PC_TIER_MEDIUM == 1.0e-6, "MEDIUM threshold must remain 1e-6"


# ============================================================================
# Test 18: Deterministic results cannot be overridden by LLM
# ============================================================================

def test_llm_cannot_override_infeasible_deterministic_result():
    """
    Even if the LLM stubbornly proposes an infeasible candidate,
    the deterministic evaluator's result must be authoritative.
    """
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = ManeuverCandidates(
        conjunction_id=conj.conjunction_id,
        primary_object="25544",
        candidates=[
            ManeuverCandidate(
                maneuver_id="M_INFEASIBLE",
                delta_v_m_s=30.0,
                burn_direction="POSIGRADE",
                new_separation_km=100.0,
                resulting_risk="LOW",
                predicted_pc=1e-7,
            ),
            ManeuverCandidate(
                maneuver_id="M_FEASIBLE",
                delta_v_m_s=1.2,
                burn_direction="POSIGRADE",
                new_separation_km=25.0,
                resulting_risk="LOW",
                predicted_pc=2e-6,
            ),
        ]
    )

    # LLM stubbornly picks infeasible candidate
    def bad_llm(summary, tools, history):
        return {"action": "make_decision", "selected_maneuver_id": "M_INFEASIBLE",
                "explanation": "I arbitrarily prefer the expensive one."}

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=conj,
        preloaded_candidates=candidates,
        llm_override=bad_llm,
    )

    assert context.selected_maneuver_id != "M_INFEASIBLE"
    assert context.selected_maneuver_id == "M_FEASIBLE"
    assert "M_INFEASIBLE" in context.infeasible_candidate_ids


# ============================================================================
# Test 19: Phase 1 and Phase 2 test imports still work (smoke test)
# ============================================================================

def test_phase1_and_phase2_modules_importable():
    """Verify that Phase 1 tool layer and Phase 2 agent modules are still importable."""
    from shared.tools import (
        get_object_state, propagate_trajectory, compute_pc, run_screening,
        assess_risk, generate_maneuver_candidates, evaluate_maneuver_constraints,
        check_ground_station_visibility, get_space_weather,
    )
    from services.optimizer.app.agent import (
        DecisionAgentOrchestrator, DecisionContext, ToolRegistry, LLMClient,
        AdaptiveWorkflowManager, WorkflowState, CandidateFailureReason,
    )
    assert callable(assess_risk)
    assert callable(generate_maneuver_candidates)
    assert DecisionAgentOrchestrator is not None
    assert AdaptiveWorkflowManager is not None


# ============================================================================
# Test 20: Workflow state annotations are present in evidence_summary
# ============================================================================

def test_workflow_state_annotated_in_context():
    """context.evidence_summary must contain Phase 3 workflow state fields."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert "workflow_state" in context.evidence_summary
    assert "maneuver_retry_count" in context.evidence_summary
    assert "evaluated_candidate_ids" in context.evidence_summary


# ============================================================================
# Test 21: LOW risk disallows maneuver-generation tools per AdaptiveWorkflowManager
# ============================================================================

def test_low_risk_tool_restriction():
    """AdaptiveWorkflowManager must exclude generate_maneuver_candidates for LOW risk."""
    wfm = AdaptiveWorkflowManager()
    # Build a minimal DecisionContext with LOW risk
    ctx = DecisionContext(
        conjunction_id="TEST",
        primary_object_id="25544",
        risk_assessment=_make_risk_assessment("LOW", 1e-8),
    )
    allowed = wfm.get_allowed_tools_for_tier("LOW")
    assert "generate_maneuver_candidates" not in allowed
    assert "evaluate_maneuver_constraints" not in allowed
    assert "assess_risk" in allowed


# ============================================================================
# Test 22: WorkflowState FSM transitions correctly
# ============================================================================

def test_workflow_fsm_transitions():
    """AdaptiveWorkflowManager must transition states correctly."""
    wfm = AdaptiveWorkflowManager()
    assert wfm.workflow_state == WorkflowState.INITIALIZED

    wfm.transition_to(WorkflowState.ASSESSING_RISK)
    assert wfm.workflow_state == WorkflowState.ASSESSING_RISK

    wfm.transition_to(WorkflowState.MONITOR_ONLY)
    assert wfm.workflow_state == WorkflowState.MONITOR_ONLY

    wfm.transition_to(WorkflowState.GENERATING_CANDIDATES)
    assert wfm.workflow_state == WorkflowState.GENERATING_CANDIDATES

    wfm.transition_to(WorkflowState.NO_FEASIBLE_MANEUVER)
    assert wfm.workflow_state == WorkflowState.NO_FEASIBLE_MANEUVER


# ============================================================================
# Test 23: Retry counter increments correctly
# ============================================================================

def test_retry_counter_increments():
    """AdaptiveWorkflowManager retry counter must increment on each retry."""
    wfm = AdaptiveWorkflowManager(max_maneuver_retries=3)
    assert wfm.maneuver_retry_count == 0

    wfm.increment_retry()
    assert wfm.maneuver_retry_count == 1

    wfm.increment_retry()
    assert wfm.maneuver_retry_count == 2

    wfm.increment_retry()
    assert wfm.maneuver_retry_count == 3

    # At limit, should_retry_maneuver must return False
    ctx = DecisionContext(conjunction_id="TEST", primary_object_id="25544")
    assert wfm.should_retry_maneuver(ctx) is False


# ============================================================================
# Test 24: Multiple rejection reasons are all recorded
# ============================================================================

def test_multiple_rejection_reasons_recorded():
    """All rejected candidates must have their specific failure reason recorded."""
    pc = 5e-4
    conj = _make_conjunction(pc=pc)
    candidates = _make_candidates(
        ("M_PC", 1.5, 5e-4, "CRITICAL"),
        ("M_DV", 25.0, 1e-6, "LOW"),
        ("M_DRIFT", 2.0, 1e-6, "LOW"),
    )
    evaluations = {
        "M_PC": _make_infeasible_pc("M_PC"),
        "M_DV": _make_infeasible_dv("M_DV"),
        "M_DRIFT": _make_infeasible_drift("M_DRIFT"),
    }
    orchestrator = _build_orchestrator_with_mocked_tools("CRITICAL", pc, candidates, evaluations)

    context = orchestrator.run(candidate_payload=conj, llm_override=lambda *a: None)

    assert context.status == "NO_FEASIBLE_MANEUVER"
    assert context.candidate_failure_reasons.get("M_PC") == CandidateFailureReason.PC_STILL_TOO_HIGH.value
    assert context.candidate_failure_reasons.get("M_DV") == CandidateFailureReason.DELTA_V_TOO_HIGH.value
    assert context.candidate_failure_reasons.get("M_DRIFT") == CandidateFailureReason.DRIFT_CONSTRAINT_VIOLATED.value
