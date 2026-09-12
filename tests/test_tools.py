"""
Unit and integration tests for OrbitalGuard Phase 1 Tool Layer.
Validates all 9 deterministic tools, input validation, schemas, and error handling.
"""

from datetime import datetime, timezone, timedelta
import numpy as np
import pytest

from shared.tools import (
    get_object_state,
    propagate_trajectory,
    compute_pc,
    run_screening,
    assess_risk,
    generate_maneuver_candidates,
    evaluate_maneuver_constraints,
    check_ground_station_visibility,
    list_ground_stations,
    get_space_weather,
    ObjectNotFoundError,
    ConjunctionNotFoundError,
    ComputationError,
    PcComputationResult,
    ManeuverConstraintEvaluation,
)
from shared.schemas.object import OrbitalObject
from shared.schemas.trajectory import Trajectory
from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance
from shared.schemas.risk import RiskAssessment
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.ground_station import GroundStationPasses
from shared.schemas.space_weather import SpaceWeatherSnapshot


# Sample verified TLE for testing (ISS ZARYA)
ISS_TLE_LINE1 = "1 25544U 98067A   26255.50000000  .00016717  00000-0  10270-3 0  9993"
ISS_TLE_LINE2 = "2 25544  51.6400 208.1000 0005000 130.0000 230.1000 15.49500000450008"

# Secondary debris sample TLE
DEB_TLE_LINE1 = "1 99999U 26001A   26255.50000000  .00010000  00000-0  10000-3 0  9991"
DEB_TLE_LINE2 = "2 99999  51.6450 208.1050 0006000 130.5000 229.6000 15.49400000100015"


# ============================================================================
# Tool 1: get_object_state
# ============================================================================

def test_get_object_state_with_tle_override():
    now = datetime.now(timezone.utc)
    obj = get_object_state(
        catalog_id="25544",
        epoch=now,
        raw_tle_line1=ISS_TLE_LINE1,
        raw_tle_line2=ISS_TLE_LINE2,
        name="ISS (ZARYA)",
        object_type="SATELLITE",
    )
    assert isinstance(obj, OrbitalObject)
    assert obj.catalog_id == "25544"
    assert obj.name == "ISS (ZARYA)"
    assert obj.state is not None
    assert obj.state.altitude_km > 300.0
    assert obj.state.reference_frame == "TEME"
    assert obj.data_quality.quality in ("HIGH", "MEDIUM", "LOW")


def test_get_object_state_not_found():
    with pytest.raises(ObjectNotFoundError):
        get_object_state(catalog_id="NON_EXISTENT_CATALOG_ID_99999999")


# ============================================================================
# Tool 2: propagate_trajectory
# ============================================================================

def test_propagate_trajectory_valid():
    traj = propagate_trajectory(
        catalog_id="25544",
        horizon_minutes=30,
        step_minutes=5.0,
        raw_tle_line1=ISS_TLE_LINE1,
        raw_tle_line2=ISS_TLE_LINE2,
        name="ISS (ZARYA)",
    )
    assert isinstance(traj, Trajectory)
    assert traj.catalog_id == "25544"
    assert len(traj.trajectory) == 7  # 0, 5, 10, 15, 20, 25, 30 min = 7 points
    assert traj.trajectory[0].altitude_km > 300.0


def test_propagate_trajectory_invalid_inputs():
    with pytest.raises(ValueError):
        propagate_trajectory(catalog_id="25544", horizon_minutes=-10)
    with pytest.raises(ValueError):
        propagate_trajectory(catalog_id="25544", step_minutes=0.0)


# ============================================================================
# Tool 3: compute_pc
# ============================================================================

def test_compute_pc_direct_vectors_and_covariance():
    rel_pos = [0.0, 0.05, 0.0]  # 50 meters offset
    rel_vel = [0.0, 0.0, 7.5]   # 7.5 km/s along Z
    cov_3d = np.diag([0.01, 0.01, 0.01])  # 100m std dev
    hbr_m = 20.0

    result = compute_pc(
        relative_position_km=rel_pos,
        relative_velocity_km_s=rel_vel,
        combined_covariance_teme_km2=cov_3d,
        combined_hbr_m=hbr_m,
    )
    assert isinstance(result, PcComputationResult)
    assert 0.0 <= result.probability_of_collision <= 1.0
    assert result.probability_of_collision > 0.0
    assert result.method == "FOSTER_2D_TLE_AGE_COVARIANCE"
    assert result.combined_hard_body_radius_m == 20.0


def test_compute_pc_insufficient_inputs():
    with pytest.raises(ValueError):
        compute_pc()


# ============================================================================
# Tool 4: run_screening
# ============================================================================

def test_run_screening_with_custom_objects():
    now = datetime.now(timezone.utc)
    objects = [
        {
            "catalog_id": "25544",
            "name": "ISS",
            "object_type": "SATELLITE",
            "tle_line_1": ISS_TLE_LINE1,
            "tle_line_2": ISS_TLE_LINE2,
            "source": "Test",
            "epoch": now,
        },
        {
            "catalog_id": "99999",
            "name": "DEBRIS",
            "object_type": "DEBRIS",
            "tle_line_1": DEB_TLE_LINE1,
            "tle_line_2": DEB_TLE_LINE2,
            "source": "Test",
            "epoch": now,
        },
    ]

    candidates = run_screening(
        horizon_minutes=30,
        threshold_km=50.0,
        objects_override=objects,
    )
    assert isinstance(candidates, list)
    for c in candidates:
        assert isinstance(c, ConjunctionCandidate)
        assert c.closest_approach.distance_km <= 50.0


def test_run_screening_invalid_parameters():
    with pytest.raises(ValueError):
        run_screening(horizon_minutes=-5)
    with pytest.raises(ValueError):
        run_screening(threshold_km=0.0)


# ============================================================================
# Tool 5: assess_risk
# ============================================================================

def test_assess_risk_from_candidate():
    now = datetime.now(timezone.utc)
    candidate = ConjunctionCandidate(
        conjunction_id="CONJ-TEST-TOOL-001",
        primary_object="25544",
        secondary_object="99999",
        tca=now + timedelta(minutes=45.0),
        closest_approach=ClosestApproach(distance_km=4.2, relative_velocity_km_s=8.5),
        screening=ScreeningInfo(threshold_km=50.0),
        data_provenance=DataProvenance(),
        probability_of_collision=1.5e-4,
    )

    assessment = assess_risk(candidate=candidate)
    assert isinstance(assessment, RiskAssessment)
    assert assessment.conjunction_id == "CONJ-TEST-TOOL-001"
    assert assessment.risk_level in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    assert assessment.risk_score >= 0.0
    assert assessment.collision_probability == 1.5e-4


def test_assess_risk_missing_conjunction():
    with pytest.raises(ConjunctionNotFoundError):
        assess_risk(conjunction_id="DOES_NOT_EXIST_CONJ_99999")


# ============================================================================
# Tool 6: generate_maneuver_candidates
# ============================================================================

def test_generate_maneuver_candidates_heuristic():
    now = datetime.now(timezone.utc)
    candidate = ConjunctionCandidate(
        conjunction_id="CONJ-TEST-TOOL-002",
        primary_object="25544",
        secondary_object="99999",
        tca=now + timedelta(minutes=40.0),
        closest_approach=ClosestApproach(distance_km=3.5, relative_velocity_km_s=9.0),
        screening=ScreeningInfo(threshold_km=50.0),
        data_provenance=DataProvenance(),
        probability_of_collision=3.0e-4,
    )

    result = generate_maneuver_candidates(candidate=candidate, allow_heuristic_fallback=True)
    assert isinstance(result, ManeuverCandidates)
    assert result.conjunction_id == "CONJ-TEST-TOOL-002"
    assert len(result.candidates) > 0
    for m in result.candidates:
        assert isinstance(m, ManeuverCandidate)
        assert m.delta_v_m_s > 0.0
        assert m.new_separation_km > 0.0


def test_generate_maneuver_candidates_missing_id():
    with pytest.raises(ConjunctionNotFoundError):
        generate_maneuver_candidates(conjunction_id="NON_EXISTENT_CONJ_12345")


# ============================================================================
# Tool 7: evaluate_maneuver_constraints
# ============================================================================

def test_evaluate_maneuver_constraints_pass():
    candidate = ManeuverCandidate(
        maneuver_id="M1",
        delta_v_m_s=1.5,
        burn_direction="POSIGRADE",
        new_separation_km=25.0,
        resulting_risk="LOW",
        predicted_pc=1.0e-6,
    )

    evaluation = evaluate_maneuver_constraints(
        candidate=candidate,
        max_delta_v_m_s=20.0,
        max_sma_drift_km=5.0,
        pc_critical_threshold=1.0e-4,
        check_ground_station=False,
    )
    assert isinstance(evaluation, ManeuverConstraintEvaluation)
    assert evaluation.maneuver_id == "M1"
    assert evaluation.delta_v_satisfied is True
    assert evaluation.pc_satisfied is True
    assert evaluation.slot_drift_satisfied is True
    assert evaluation.is_feasible is True
    assert evaluation.overall_status == "SATISFIED"


def test_evaluate_maneuver_constraints_violated_delta_v():
    candidate = ManeuverCandidate(
        maneuver_id="M_EXCESSIVE",
        delta_v_m_s=35.0,  # Exceeds max 20.0 m/s
        burn_direction="POSIGRADE",
        new_separation_km=100.0,
        resulting_risk="LOW",
        predicted_pc=1.0e-7,
    )

    evaluation = evaluate_maneuver_constraints(
        candidate=candidate,
        max_delta_v_m_s=20.0,
        check_ground_station=False,
    )
    assert evaluation.delta_v_satisfied is False
    assert evaluation.is_feasible is False
    assert evaluation.overall_status == "VIOLATED"


# ============================================================================
# Tool 8: check_ground_station_visibility
# ============================================================================

def test_check_ground_station_visibility():
    now = datetime.now(timezone.utc)
    passes = check_ground_station_visibility(
        catalog_id="25544",
        start_dt=now,
        horizon_minutes=45,
        raw_tle_line1=ISS_TLE_LINE1,
        raw_tle_line2=ISS_TLE_LINE2,
        name="ISS",
    )
    assert isinstance(passes, GroundStationPasses)
    assert passes.catalog_id == "25544"
    assert passes.horizon_minutes == 45
    assert isinstance(passes.passes, list)


def test_list_ground_stations():
    stations = list_ground_stations()
    assert len(stations) == 3
    station_ids = {s.station_id for s in stations}
    assert "SVALBARD" in station_ids
    assert "FAIRBANKS" in station_ids
    assert "MCMURDO" in station_ids


# ============================================================================
# Tool 9: get_space_weather
# ============================================================================

def test_get_space_weather():
    snapshot = get_space_weather()
    assert isinstance(snapshot, SpaceWeatherSnapshot)
    assert 0.0 <= snapshot.kp_index <= 9.0
    assert snapshot.ap_index >= 0.0
    assert snapshot.f107_sfu > 0.0
    assert snapshot.activity_level in ("QUIET", "MODERATE", "ELEVATED", "STORM")
    assert snapshot.drag_activity_scalar >= 1.0
    assert isinstance(snapshot.live, bool)
