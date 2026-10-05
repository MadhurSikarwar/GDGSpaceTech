from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from shared.schemas.conjunction import (
    ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance
)
from shared.schemas.risk import RiskAssessment
from services.risk.app.scoring import (
    calculate_time_to_tca_minutes,
    compute_distance_score,
    compute_time_urgency_score,
    compute_relative_velocity_score,
    calculate_risk_score,
    classify_risk_level,
    assess_conjunction_risk
)
from services.risk.app.main import app


@pytest.fixture
def sample_candidate():
    now = datetime.now(timezone.utc)
    tca = now + timedelta(minutes=44.6)
    return ConjunctionCandidate(
        conjunction_id="CONJ-TEST-001",
        primary_object="25544",
        secondary_object="SYNTHETIC-99999",
        primary_object_name="ISS (ZARYA)",
        secondary_object_name="DEB-DEMO (SYNTHETIC_DEBRIS)",
        tca=tca,
        closest_approach=ClosestApproach(
            distance_km=7.68,
            relative_velocity_km_s=10.42
        ),
        screening=ScreeningInfo(
            threshold_km=50.0,
            method="SGP4_TRAJECTORY_SCREENING"
        ),
        data_provenance=DataProvenance(
            primary_source="CelesTrak",
            propagator="SGP4"
        ),
        created_at=now
    )


def test_time_to_tca_calculation():
    now = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
    tca_future = datetime(2026, 9, 12, 12, 45, 0, tzinfo=timezone.utc)
    tca_past = datetime(2026, 9, 12, 11, 45, 0, tzinfo=timezone.utc)

    assert calculate_time_to_tca_minutes(tca_future, ref_time=now) == 45.0
    assert calculate_time_to_tca_minutes(tca_past, ref_time=now) == 0.0


def test_distance_scoring_component():
    assert compute_distance_score(0.5) == 100.0
    assert compute_distance_score(1.0) == 100.0
    assert compute_distance_score(50.0) == 0.0
    assert compute_distance_score(60.0) == 0.0
    
    mid_score = compute_distance_score(7.68)
    assert 0.0 < mid_score < 100.0


def test_time_urgency_component():
    assert compute_time_urgency_score(10.0) == 100.0
    assert compute_time_urgency_score(15.0) == 100.0
    assert compute_time_urgency_score(90.0) == 20.0
    assert compute_time_urgency_score(120.0) == 20.0


def test_relative_velocity_component():
    assert compute_relative_velocity_score(1.0) == 20.0
    assert compute_relative_velocity_score(2.0) == 20.0
    assert compute_relative_velocity_score(14.0) == 100.0
    assert compute_relative_velocity_score(16.0) == 100.0


def test_risk_score_bounds_and_determinism():
    # Extreme close encounter
    critical_score = calculate_risk_score(distance_km=0.5, time_to_tca_minutes=10.0, relative_velocity_km_s=14.0)
    assert critical_score == 100.0

    # Safe far encounter
    low_score = calculate_risk_score(distance_km=50.0, time_to_tca_minutes=90.0, relative_velocity_km_s=2.0)
    assert low_score == 9.0  # 0.55*0 + 0.3*20 + 0.15*20 = 9.0

    # Intermediate test
    score = calculate_risk_score(distance_km=7.68, time_to_tca_minutes=44.6, relative_velocity_km_s=10.42)
    assert 0.0 <= score <= 100.0


def test_risk_level_classification():
    assert classify_risk_level(risk_score=90.0, distance_km=2.0, time_to_tca_minutes=15.0) == "CRITICAL"
    assert classify_risk_level(risk_score=75.0, distance_km=8.0, time_to_tca_minutes=45.0) == "HIGH"
    assert classify_risk_level(risk_score=50.0, distance_km=25.0, time_to_tca_minutes=60.0) == "MEDIUM"
    assert classify_risk_level(risk_score=20.0, distance_km=45.0, time_to_tca_minutes=90.0) == "LOW"


def test_assess_conjunction_risk_schema_contract(sample_candidate):
    assessment = assess_conjunction_risk(sample_candidate)
    
    assert isinstance(assessment, RiskAssessment)
    assert assessment.conjunction_id == sample_candidate.conjunction_id
    assert assessment.risk_level in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    assert 0.0 <= assessment.risk_score <= 100.0
    assert assessment.factors.closest_approach_km == 7.68
    assert assessment.factors.relative_velocity_km_s == 10.42
    assert assessment.uncertainty.model == "PROTOTYPE_FIXED_UNCERTAINTY"
    assert assessment.notes is not None


def test_api_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"


def test_api_assess_risk_with_body(sample_candidate):
    client = TestClient(app)
    payload = sample_candidate.model_dump(mode="json")
    response = client.post("/assess-risk", json=payload)
    
    assert response.status_code == 200
    data = response.json()
    assert data["conjunction_id"] == sample_candidate.conjunction_id
    assert "risk_score" in data
    assert "risk_level" in data
    assert data["factors"]["closest_approach_km"] == 7.68


def test_api_batch_assess():
    client = TestClient(app)
    response = client.post("/batch-assess")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "conjunction_id" in data[0]
        assert "risk_score" in data[0]
