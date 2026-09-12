"""
Tests for OrbitalGuard Phase 2 Agentic Decision Orchestrator.
Covers dynamic tool selection, multi-turn reasoning, hard physics constraint boundaries,
safe handling of invalid/failing tool calls, infeasible candidate rejection, and human approval gates.
"""

from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.decision import ManeuverDecision
from shared.tools.models import ManeuverConstraintEvaluation
from services.optimizer.app.main import app as optimizer_app
from services.optimizer.app.agent import (
    DecisionAgentOrchestrator,
    DecisionContext,
    ToolRegistry,
    LLMClient,
)


@pytest.fixture
def test_conjunction():
    now = datetime.now(timezone.utc)
    return ConjunctionCandidate(
        conjunction_id="CONJ-TEST-AGENT-001",
        primary_object="25544",
        secondary_object="SYNTHETIC-99999",
        primary_object_name="ISS",
        secondary_object_name="DEB-DEMO",
        tca=now + timedelta(minutes=45.0),
        closest_approach=ClosestApproach(distance_km=3.2, relative_velocity_km_s=9.8),
        screening=ScreeningInfo(threshold_km=50.0),
        data_provenance=DataProvenance(),
        probability_of_collision=3.5e-4,
    )


# ============================================================================
# Test 1 & 2: Agent can invoke a tool and select different tools based on context
# ============================================================================

def test_agent_invokes_and_selects_tool(test_conjunction):
    # Mock LLM that requests assess_risk on turn 1, then terminates on turn 2
    turn_count = 0

    def mock_llm(context_summary, available_tools, tool_history):
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            return {
                "action": "call_tool",
                "tool_name": "assess_risk",
                "tool_args": {"conjunction_id": test_conjunction.conjunction_id},
                "reasoning": "Need hazard assessment first."
            }
        else:
            return {
                "action": "make_decision",
                "selected_maneuver_id": "NONE",
                "explanation": "Evaluated risk as requested."
            }

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        llm_override=mock_llm,
    )

    assert len(context.tool_history) >= 1
    assert context.tool_history[0].tool_name == "assess_risk"
    assert context.tool_history[0].success is True
    assert context.risk_assessment is not None
    assert context.risk_assessment.risk_level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")


# ============================================================================
# Test 3 & 4: Agent inspects tool results and requests another tool
# ============================================================================

def test_agent_multi_turn_tool_chaining(test_conjunction):
    step = 0

    def mock_llm(context_summary, available_tools, tool_history):
        nonlocal step
        step += 1
        if step == 1:
            return {
                "action": "call_tool",
                "tool_name": "get_space_weather",
                "tool_args": {},
                "reasoning": "Need thermospheric drag conditions."
            }
        elif step == 2:
            return {
                "action": "call_tool",
                "tool_name": "assess_risk",
                "tool_args": {"conjunction_id": test_conjunction.conjunction_id},
                "reasoning": "Need risk score."
            }
        else:
            return {
                "action": "make_decision",
                "selected_maneuver_id": "NONE",
                "explanation": "Completed chain of analysis."
            }

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        llm_override=mock_llm,
    )

    tool_names = [t.tool_name for t in context.tool_history]
    assert "get_space_weather" in tool_names
    assert "assess_risk" in tool_names
    assert context.space_weather is not None
    assert context.risk_assessment is not None


# ============================================================================
# Test 5 & 13: Agent terminates successfully and human approval is required
# ============================================================================

def test_agent_termination_and_human_approval(test_conjunction):
    def mock_llm(context_summary, available_tools, tool_history):
        return {
            "action": "call_tool",
            "tool_name": "generate_maneuver_candidates",
            "tool_args": {"conjunction_id": test_conjunction.conjunction_id},
            "reasoning": "Need candidates."
        }

    orchestrator = DecisionAgentOrchestrator(max_iterations=3)
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        llm_override=mock_llm,
    )

    decision = orchestrator.to_maneuver_decision(context)
    assert isinstance(decision, ManeuverDecision)
    assert decision.human_approval_required is True
    assert context.human_approval_required is True
    assert decision.simulation.status == "PENDING"


# ============================================================================
# Test 6: Agent respects maximum iteration/tool-call limits
# ============================================================================

def test_agent_respects_iteration_limits(test_conjunction):
    # Mock LLM that continuously asks for space weather forever
    def mock_infinite_llm(context_summary, available_tools, tool_history):
        return {
            "action": "call_tool",
            "tool_name": "get_space_weather",
            "tool_args": {"force_refresh": True},
            "reasoning": "Polling space weather endlessly."
        }

    max_limit = 4
    orchestrator = DecisionAgentOrchestrator(max_iterations=max_limit)
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        llm_override=mock_infinite_llm,
    )

    assert context.iteration_count <= max_limit
    assert context.status in ("AWAITING_HUMAN_APPROVAL", "RECOMMENDATION_GENERATED", "ANALYZING", "INCOMPLETE_ANALYSIS")


# ============================================================================
# Test 7 & 8: Invalid tool calls and tool failures are handled safely
# ============================================================================

def test_agent_handles_invalid_tool_calls_gracefully(test_conjunction):
    step = 0

    def mock_llm(context_summary, available_tools, tool_history):
        nonlocal step
        step += 1
        if step == 1:
            return {
                "action": "call_tool",
                "tool_name": "NON_EXISTENT_TOOL_NAME",
                "tool_args": {"arg": 123},
                "reasoning": "Making a bogus call."
            }
        else:
            return {
                "action": "make_decision",
                "selected_maneuver_id": "NONE",
                "explanation": "Recovered from invalid tool call."
            }

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        llm_override=mock_llm,
    )

    # Verify error was logged in tool history without crashing the agent
    assert len(context.tool_history) >= 1
    assert context.tool_history[0].tool_name == "NON_EXISTENT_TOOL_NAME"
    assert context.tool_history[0].success is False
    assert "Unknown tool" in context.tool_history[0].error


# ============================================================================
# Test 9 & 10: Deterministic results cannot be overridden; infeasible rejected
# ============================================================================

def test_llm_cannot_override_infeasible_candidate(test_conjunction):
    # Create candidates where M1 is infeasible (excessive delta-V 30.0 m/s > 20 m/s)
    # and M2 is feasible (1.5 m/s)
    candidates = ManeuverCandidates(
        conjunction_id=test_conjunction.conjunction_id,
        primary_object=test_conjunction.primary_object,
        candidates=[
            ManeuverCandidate(
                maneuver_id="M_INFEASIBLE",
                delta_v_m_s=30.0,
                burn_direction="POSIGRADE",
                new_separation_km=100.0,
                resulting_risk="LOW",
                predicted_pc=1.0e-7,
            ),
            ManeuverCandidate(
                maneuver_id="M_FEASIBLE",
                delta_v_m_s=1.2,
                burn_direction="POSIGRADE",
                new_separation_km=25.0,
                resulting_risk="LOW",
                predicted_pc=2.0e-6,
            )
        ]
    )

    # LLM stubbornly attempts to pick M_INFEASIBLE
    def mock_bad_llm(context_summary, available_tools, tool_history):
        return {
            "action": "make_decision",
            "selected_maneuver_id": "M_INFEASIBLE",
            "explanation": "I arbitrarily prefer M_INFEASIBLE because 100km is big."
        }

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        preloaded_candidates=candidates,
        llm_override=mock_bad_llm,
    )

    # The deterministic constraint evaluator MUST have overridden the LLM!
    assert context.selected_maneuver_id != "M_INFEASIBLE"
    assert context.selected_maneuver_id == "M_FEASIBLE"
    assert "M_INFEASIBLE" in context.infeasible_candidate_ids


# ============================================================================
# Test 11: Feasible maneuver can be recommended
# ============================================================================

def test_feasible_maneuver_recommendation(test_conjunction):
    candidates = ManeuverCandidates(
        conjunction_id=test_conjunction.conjunction_id,
        primary_object=test_conjunction.primary_object,
        candidates=[
            ManeuverCandidate(
                maneuver_id="M_OPT",
                delta_v_m_s=0.95,
                burn_direction="POSIGRADE",
                new_separation_km=28.4,
                resulting_risk="LOW",
                predicted_pc=3.0e-6,
            )
        ]
    )

    def mock_good_llm(context_summary, available_tools, tool_history):
        return {
            "action": "make_decision",
            "selected_maneuver_id": "M_OPT",
            "explanation": "M_OPT achieves 28.4 km separation for 0.95 m/s delta-V with verified LOW risk."
        }

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        preloaded_candidates=candidates,
        llm_override=mock_good_llm,
    )

    assert context.selected_maneuver_id == "M_OPT"
    assert context.status == "AWAITING_HUMAN_APPROVAL"
    assert "0.95" in context.explanation or "28.4" in context.explanation


# ============================================================================
# Test 12: No feasible maneuver produces explicit NO_FEASIBLE_MANEUVER
# ============================================================================

def test_no_feasible_maneuver_explicit_state(test_conjunction):
    # All candidates violate delta-V cap
    candidates = ManeuverCandidates(
        conjunction_id=test_conjunction.conjunction_id,
        primary_object=test_conjunction.primary_object,
        candidates=[
            ManeuverCandidate(
                maneuver_id="M_TOO_EXPENSIVE_1",
                delta_v_m_s=25.0,
                burn_direction="POSIGRADE",
                new_separation_km=80.0,
                resulting_risk="LOW",
                predicted_pc=1.0e-7,
            ),
            ManeuverCandidate(
                maneuver_id="M_TOO_EXPENSIVE_2",
                delta_v_m_s=40.0,
                burn_direction="RETROGRADE",
                new_separation_km=120.0,
                resulting_risk="LOW",
                predicted_pc=1.0e-8,
            ),
        ]
    )

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        preloaded_candidates=candidates,
        llm_override=lambda c, t, h: {"action": "make_decision", "selected_maneuver_id": "M_TOO_EXPENSIVE_1"},
    )

    assert context.selected_maneuver_id == "NO_FEASIBLE_MANEUVER"
    assert context.status == "NO_FEASIBLE_MANEUVER"
    assert "NO FEASIBLE MANEUVER" in context.explanation


# ============================================================================
# Test 14: LLM failure produces graceful deterministic fallback
# ============================================================================

def test_llm_failure_produces_graceful_fallback(test_conjunction):
    # LLM always returns None (simulating timeout, bad key, or network error)
    candidates = ManeuverCandidates(
        conjunction_id=test_conjunction.conjunction_id,
        primary_object=test_conjunction.primary_object,
        candidates=[
            ManeuverCandidate(
                maneuver_id="M_FEASIBLE",
                delta_v_m_s=1.2,
                burn_direction="POSIGRADE",
                new_separation_km=25.0,
                resulting_risk="LOW",
                predicted_pc=2e-6,
            )
        ]
    )

    orchestrator = DecisionAgentOrchestrator()
    context = orchestrator.run(
        candidate_payload=test_conjunction,
        preloaded_candidates=candidates,
        llm_override=lambda c, t, h: None,
    )

    # Agent must NOT crash and must have completed deterministic steps
    assert context.status in ("AWAITING_HUMAN_APPROVAL", "RECOMMENDATION_GENERATED")
    assert context.risk_assessment is not None
    assert context.selected_maneuver_id is not None
    assert context.selected_maneuver_id != ""


# ============================================================================
# Test 15: FastAPI HTTP endpoints for Decision Agent
# ============================================================================

def test_fastapi_agent_decide_endpoint(monkeypatch):
    from services.optimizer.app.main import _orchestrator
    monkeypatch.setattr(_orchestrator.llm, "query_decision", lambda **kwargs: None)
    client = TestClient(optimizer_app)
    
    # Test health check
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.json()["architecture"] == "AGENTIC_TOOL_ORCHESTRATOR"

    # Test tools endpoint
    tools_resp = client.get("/agent/tools")
    assert tools_resp.status_code == 200
    tools_data = tools_resp.json()
    assert "assess_risk" in tools_data
    assert "generate_maneuver_candidates" in tools_data
    assert "evaluate_maneuver_constraints" in tools_data

    # Test optimize-decision contract backward compatibility
    payload = {
        "conjunction_id": "CONJ-TEST-API",
        "primary_object": "25544",
        "candidates": [
            {
                "maneuver_id": "M1",
                "delta_v_m_s": 1.2,
                "burn_direction": "POSIGRADE",
                "new_separation_km": 30.0,
                "resulting_risk": "LOW",
                "predicted_pc": 1.0e-6
            }
        ]
    }
    resp = client.post("/optimize-decision", json=payload)
    assert resp.status_code == 200
    decision = resp.json()
    assert decision["conjunction_id"] == "CONJ-TEST-API"
    assert decision["decision"]["recommended_maneuver_id"] == "M1"
    assert decision["human_approval_required"] is True
