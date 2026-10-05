"""Decision agent against a live OrbitWatch database (skipped when none is reachable).

The LLM is switched off (no API key), so this exercises the deterministic
workflow, the tools and the guardrails; it creates one assessment and removes it.
"""
import pytest


@pytest.fixture(scope="module")
def live_event():
    from orbitwatch import db
    try:
        row = db.query_one("admin", """
            SELECT event_id FROM conjunction_event
             WHERE time_of_closest_approach > UTC_TIMESTAMP() + INTERVAL 2 HOUR
             ORDER BY probability_of_collision DESC LIMIT 1""")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no OrbitWatch database reachable: {exc}")
    if not row:
        pytest.skip("no upcoming close approach to assess")
    return row["event_id"]


def test_deterministic_assessment_and_guardrails(live_event, monkeypatch):
    from orbitwatch import db
    from orbitwatch.agent import orchestrator
    monkeypatch.setenv("GROQ_API_KEY", "")
    aid = orchestrator.start(live_event, None, "admin", background=False)
    try:
        a = db.query_one("admin", "SELECT * FROM agent_assessment WHERE assessment_id = %s", (aid,))
        steps = db.query("admin", "SELECT actor, tool_name FROM agent_step WHERE assessment_id = %s ORDER BY step_no", (aid,))
        assert a["status"] == "complete" and a["engine"] == "deterministic"
        tools = [s["tool_name"] for s in steps if s["actor"] == "tool"]
        assert tools[:4] == ["get_conjunction", "get_space_weather", "compute_collision_probability", "assess_risk"]
        if a["decision"] == "MANEUVER_RECOMMENDED":
            assert a["human_approval_required"] and a["delta_v_mps"] > 0 and a["pc_after"] < a["pc_before"]
            assert "evaluate_maneuver_constraints" in tools
        else:
            assert a["decision"] in ("MONITOR", "NO_FEASIBLE_MANEUVER", "DATA_UNAVAILABLE")
        assert len(a["explanation"]) > 40
    finally:
        db.execute("admin", "DELETE FROM agent_assessment WHERE assessment_id = %s", (aid,))


def test_guardrail_rejects_an_unevaluated_or_infeasible_choice(live_event, monkeypatch):
    from orbitwatch import db
    from orbitwatch.agent.orchestrator import DecisionAgent
    _, aid = db.execute("admin", "INSERT INTO agent_assessment (event_id, engine) VALUES (%s, 'test')", (live_event,))
    try:
        agent = DecisionAgent(aid, live_event, "admin")
        for name in ("get_conjunction", "compute_collision_probability", "assess_risk"):
            agent.call_tool(name, {})
        if agent.tools.cache["risk"]["risk_tier"] == "LOW":
            out = agent.guard({"decision": "MANEUVER_RECOMMENDED", "selected_candidate_id": "M9", "explanation": ""})
            assert out["decision"] == "MONITOR"
        else:
            out = agent.guard({"decision": "MANEUVER_RECOMMENDED", "selected_candidate_id": "M9", "explanation": ""})
            assert out["selected_candidate_id"] != "M9"
            if out["decision"] == "MANEUVER_RECOMMENDED":
                assert agent.tools.evaluations[out["selected_candidate_id"]]["feasible"]
    finally:
        db.execute("admin", "DELETE FROM agent_assessment WHERE assessment_id = %s", (aid,))
