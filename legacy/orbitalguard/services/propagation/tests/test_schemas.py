from datetime import datetime, timezone
import pytest
from shared.schemas import (
    OrbitalObject, ObjectType, OrbitalData, DataQuality,
    StateVector, Vector3, Trajectory, TrajectoryPoint,
    ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance,
    RiskAssessment, RiskFactors, UncertaintyInfo,
    ManeuverCandidates, ManeuverCandidate,
    ManeuverDecision, DecisionInfo, SimulationInfo
)


def test_orbital_object_schema():
    now = datetime.now(timezone.utc)
    obj = OrbitalObject(
        object_id="25544",
        catalog_id="25544",
        name="ISS",
        object_type=ObjectType.SATELLITE,
        international_designator="1998-067A",
        orbital_data=OrbitalData(epoch=now, source="CelesTrak"),
        data_quality=DataQuality(data_age_hours=1.5, quality="HIGH")
    )
    assert obj.catalog_id == "25544"
    assert obj.object_type == ObjectType.SATELLITE
    data_json = obj.model_dump_json()
    assert "25544" in data_json


def test_conjunction_candidate_schema():
    now = datetime.now(timezone.utc)
    cand = ConjunctionCandidate(
        conjunction_id="CONJ-001",
        primary_object="25544",
        secondary_object="SYNTHETIC-99999",
        tca=now,
        closest_approach=ClosestApproach(distance_km=8.2, relative_velocity_km_s=7.4),
        screening=ScreeningInfo(threshold_km=50.0, method="SGP4_TRAJECTORY_SCREENING"),
        data_provenance=DataProvenance(primary_source="CelesTrak", propagator="SGP4")
    )
    assert cand.closest_approach.distance_km == 8.2
    assert cand.secondary_object == "SYNTHETIC-99999"


def test_downstream_risk_and_maneuver_schemas():
    risk = RiskAssessment(
        conjunction_id="CONJ-001",
        risk_score=87.0,
        risk_level="HIGH",
        factors=RiskFactors(closest_approach_km=8.2, time_to_tca_minutes=42.0, relative_velocity_km_s=7.4),
        uncertainty=UncertaintyInfo()
    )
    assert risk.risk_score == 87.0

    maneuver = ManeuverCandidates(
        conjunction_id="CONJ-001",
        primary_object="25544",
        candidates=[
            ManeuverCandidate(maneuver_id="M1", delta_v_m_s=1.2, new_separation_km=52.0, resulting_risk="LOW")
        ]
    )
    assert len(maneuver.candidates) == 1

    decision = ManeuverDecision(
        conjunction_id="CONJ-001",
        decision=DecisionInfo(recommended_maneuver_id="M1", reason="Safest option"),
        simulation=SimulationInfo(status="PENDING", new_tca_distance_km=52.0)
    )
    assert decision.decision.recommended_maneuver_id == "M1"
