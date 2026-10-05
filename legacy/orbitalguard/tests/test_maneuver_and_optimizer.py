import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from services.maneuver.app.main import app as maneuver_app
from services.optimizer.app.main import app as optimizer_app
from shared.schemas.risk import RiskAssessment, RiskFactors, UncertaintyInfo
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate


def test_maneuver_agent_health():
    client = TestClient(maneuver_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"


def test_maneuver_agent_low_risk_no_burns():
    client = TestClient(maneuver_app)
    payload = {
        "conjunction_id": "CONJ-TEST-LOW",
        "risk_score": 10.0,
        "risk_level": "LOW",
        "factors": {
            "closest_approach_km": 60.0,
            "time_to_tca_minutes": 120.0,
            "relative_velocity_km_s": 5.0
        },
        "uncertainty": {}
    }
    response = client.post("/generate-maneuvers", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["conjunction_id"] == "CONJ-TEST-LOW"
    assert len(data["candidates"]) == 0


def test_maneuver_agent_high_risk_burn_options():
    client = TestClient(maneuver_app)
    payload = {
        "conjunction_id": "CONJ-TEST-HIGH",
        "risk_score": 85.0,
        "risk_level": "HIGH",
        "factors": {
            "closest_approach_km": 4.5,
            "time_to_tca_minutes": 60.0,
            "relative_velocity_km_s": 7.5
        },
        "uncertainty": {}
    }
    response = client.post("/generate-maneuvers", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["conjunction_id"] == "CONJ-TEST-HIGH"
    assert len(data["candidates"]) == 10  # 2 directions x 5 dv options
    # Check that at least one candidate achieves LOW risk
    low_risk = [c for c in data["candidates"] if c["resulting_risk"] == "LOW"]
    assert len(low_risk) > 0


def test_optimizer_agent_health():
    client = TestClient(optimizer_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"


def test_optimizer_agent_empty_candidates():
    client = TestClient(optimizer_app)
    payload = {
        "conjunction_id": "CONJ-EMPTY",
        "primary_object": "25544",
        "candidates": []
    }
    response = client.post("/optimize-decision", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["decision"]["recommended_maneuver_id"] == "NONE"
    assert data["human_approval_required"] is False


def test_optimizer_agent_optimal_selection():
    client = TestClient(optimizer_app)
    payload = {
        "conjunction_id": "CONJ-SELECT",
        "primary_object": "25544",
        "candidates": [
            {
                "maneuver_id": "M1",
                "delta_v_m_s": 2.0,
                "burn_direction": "POSIGRADE",
                "new_separation_km": 65.0,
                "resulting_risk": "LOW"
            },
            {
                "maneuver_id": "M2",
                "delta_v_m_s": 0.8,
                "burn_direction": "POSIGRADE",
                "new_separation_km": 52.0,
                "resulting_risk": "LOW"
            },
            {
                "maneuver_id": "M3",
                "delta_v_m_s": 0.2,
                "burn_direction": "RETROGRADE",
                "new_separation_km": 15.0,
                "resulting_risk": "MEDIUM"
            }
        ]
    }
    response = client.post("/optimize-decision", json=payload)
    assert response.status_code == 200
    data = response.json()
    # M2 should be chosen because it has LOW risk and lowest delta-V (0.8 m/s < 2.0 m/s)
    assert data["decision"]["recommended_maneuver_id"] == "M2"
    assert data["human_approval_required"] is True
    assert data["simulation"]["status"] == "PENDING"
    assert data["simulation"]["new_tca_distance_km"] == 52.0
    assert len(data["decision"]["reason"]) > 0


def test_end_to_end_maneuver_and_optimizer_pipeline():
    """Tests the exact handoff from Maneuver Agent output to Optimizer Agent input."""
    maneuver_client = TestClient(maneuver_app)
    optimizer_client = TestClient(optimizer_app)

    risk_input = {
        "conjunction_id": "CONJ-PIPELINE-01",
        "risk_score": 92.0,
        "risk_level": "CRITICAL",
        "factors": {
            "closest_approach_km": 2.1,
            "time_to_tca_minutes": 60.0,
            "relative_velocity_km_s": 8.0
        },
        "uncertainty": {}
    }

    # Step 1: Generate candidates with Block 2 (Maneuver Agent)
    m_resp = maneuver_client.post("/generate-maneuvers", json=risk_input)
    assert m_resp.status_code == 200
    candidates_payload = m_resp.json()
    assert len(candidates_payload["candidates"]) > 0

    # Step 2: Feed candidates into Block 3 (Optimizer Agent)
    o_resp = optimizer_client.post("/optimize-decision", json=candidates_payload)
    assert o_resp.status_code == 200
    decision_payload = o_resp.json()

    assert decision_payload["conjunction_id"] == "CONJ-PIPELINE-01"
    assert decision_payload["decision"]["recommended_maneuver_id"] != "NONE"
    assert decision_payload["decision"]["reason"] is not None
    print(f"\n[Test Pipeline Decision]: {decision_payload['decision']}")
