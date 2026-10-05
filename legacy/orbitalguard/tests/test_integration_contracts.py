from datetime import datetime, timezone
from shared.schemas import (
    OrbitalObject, ObjectType, OrbitalData, DataQuality,
    StateVector, Vector3, Trajectory, TrajectoryPoint,
    ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance,
    RiskAssessment, RiskFactors, UncertaintyInfo,
    ManeuverCandidates, ManeuverCandidate,
    ManeuverDecision, DecisionInfo, SimulationInfo
)


def test_end_to_end_contract_flow():
    now = datetime.now(timezone.utc)

    # 1. Tracking: OrbitalObject
    obj = OrbitalObject(
        object_id="25544",
        catalog_id="25544",
        name="ISS",
        object_type=ObjectType.SATELLITE,
        orbital_data=OrbitalData(epoch=now, source="CelesTrak"),
        data_quality=DataQuality(data_age_hours=1.0, quality="HIGH")
    )
    assert obj.catalog_id == "25544"

    # 2. Screening: ConjunctionCandidate
    conj = ConjunctionCandidate(
        conjunction_id="CONJ-001",
        primary_object="25544",
        secondary_object="SYNTHETIC-99999",
        tca=now,
        closest_approach=ClosestApproach(distance_km=8.2, relative_velocity_km_s=7.4),
        screening=ScreeningInfo(threshold_km=50.0, method="SGP4_TRAJECTORY_SCREENING"),
        data_provenance=DataProvenance(primary_source="CelesTrak", propagator="SGP4")
    )
    assert conj.conjunction_id == "CONJ-001"

    # 3. Risk: RiskAssessment
    risk = RiskAssessment(
        conjunction_id=conj.conjunction_id,
        risk_score=87.5,
        risk_level="HIGH",
        factors=RiskFactors(
            closest_approach_km=conj.closest_approach.distance_km,
            time_to_tca_minutes=42.0,
            relative_velocity_km_s=conj.closest_approach.relative_velocity_km_s
        ),
        uncertainty=UncertaintyInfo()
    )
    assert risk.risk_score == 87.5

    # 4. Maneuver: ManeuverCandidates
    maneuver = ManeuverCandidates(
        conjunction_id=risk.conjunction_id,
        primary_object=obj.catalog_id,
        candidates=[
            ManeuverCandidate(maneuver_id="M1", delta_v_m_s=1.2, new_separation_km=54.2, resulting_risk="LOW")
        ]
    )
    assert len(maneuver.candidates) == 1

    # 5. Optimizer: ManeuverDecision
    decision = ManeuverDecision(
        conjunction_id=maneuver.conjunction_id,
        decision=DecisionInfo(recommended_maneuver_id="M1", reason="Safest burn"),
        simulation=SimulationInfo(status="PENDING", new_tca_distance_km=54.2)
    )
    assert decision.decision.recommended_maneuver_id == "M1"
