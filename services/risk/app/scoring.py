from datetime import datetime, timezone
from typing import Optional
from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.risk import RiskAssessment, RiskFactors, UncertaintyInfo


def calculate_time_to_tca_minutes(tca: datetime, ref_time: Optional[datetime] = None) -> float:
    """
    Calculate the minutes remaining until Time of Closest Approach (TCA).
    Ensures timezone-aware comparison in UTC.
    """
    if ref_time is None:
        ref_time = datetime.now(timezone.utc)
    
    # Ensure both datetimes are timezone-aware UTC
    if tca.tzinfo is None:
        tca = tca.replace(tzinfo=timezone.utc)
    if ref_time.tzinfo is None:
        ref_time = ref_time.replace(tzinfo=timezone.utc)

    delta_seconds = (tca - ref_time).total_seconds()
    minutes = delta_seconds / 60.0
    return max(0.0, round(minutes, 2))


def compute_distance_score(distance_km: float) -> float:
    """
    Compute 0-100 hazard score component for minimum separation distance (km).
    < 1.0 km is maximum severity (100.0), >= 50.0 km drops to 0.0.
    """
    if distance_km <= 1.0:
        return 100.0
    if distance_km >= 50.0:
        return 0.0
    
    # Non-linear scaling emphasizing high proximity (< 15 km)
    scaled = 100.0 * (((50.0 - distance_km) / 49.0) ** 1.4)
    return max(0.0, min(100.0, scaled))


def compute_time_urgency_score(time_to_tca_minutes: float) -> float:
    """
    Compute 0-100 hazard score component for time urgency until TCA.
    <= 15 minutes is high urgency (100.0), >= 90 minutes scales down to 20.0.
    """
    if time_to_tca_minutes <= 15.0:
        return 100.0
    if time_to_tca_minutes >= 90.0:
        return 20.0
    
    scaled = 100.0 - 80.0 * ((time_to_tca_minutes - 15.0) / 75.0)
    return max(20.0, min(100.0, scaled))


def compute_relative_velocity_score(relative_velocity_km_s: float) -> float:
    """
    Compute 0-100 hazard score component for encounter kinetic severity.
    Scaled from 2.0 km/s (20.0) up to 14.0 km/s (100.0).
    """
    if relative_velocity_km_s <= 2.0:
        return 20.0
    if relative_velocity_km_s >= 14.0:
        return 100.0
    
    scaled = 20.0 + (relative_velocity_km_s - 2.0) * (80.0 / 12.0)
    return max(20.0, min(100.0, scaled))


def calculate_risk_score(
    distance_km: float,
    time_to_tca_minutes: float,
    relative_velocity_km_s: float
) -> float:
    """
    Calculate deterministic composite hazard score (0.0 to 100.0).
    Weights: Distance (55%), Time-to-TCA (30%), Relative Velocity (15%).
    """
    s_dist = compute_distance_score(distance_km)
    s_time = compute_time_urgency_score(time_to_tca_minutes)
    s_vel = compute_relative_velocity_score(relative_velocity_km_s)

    raw_score = (0.55 * s_dist) + (0.30 * s_time) + (0.15 * s_vel)
    return round(max(0.0, min(100.0, raw_score)), 1)


def classify_risk_level(
    risk_score: float,
    distance_km: float,
    time_to_tca_minutes: float
) -> str:
    """
    Assign explainable, deterministic risk tier:
    CRITICAL, HIGH, MEDIUM, or LOW.
    """
    if risk_score >= 85.0 or (distance_km < 5.0 and time_to_tca_minutes <= 30.0):
        return "CRITICAL"
    if risk_score >= 70.0 or distance_km < 12.0:
        return "HIGH"
    if risk_score >= 40.0:
        return "MEDIUM"
    return "LOW"


def generate_risk_notes(
    conjunction_id: str,
    distance_km: float,
    time_to_tca_minutes: float,
    relative_velocity_km_s: float,
    risk_level: str
) -> str:
    """Generate concise, operator-friendly diagnostic notes for the assessment."""
    if risk_level == "CRITICAL":
        return (
            f"CRITICAL encounter hazard ({distance_km:.2f} km separation in {time_to_tca_minutes:.1f} mins) "
            f"at {relative_velocity_km_s:.2f} km/s. Immediate avoidance maneuver assessment required."
        )
    elif risk_level == "HIGH":
        return (
            f"High conjunction risk: Miss distance of {distance_km:.2f} km with {time_to_tca_minutes:.1f} mins to TCA. "
            f"Relative velocity {relative_velocity_km_s:.2f} km/s."
        )
    elif risk_level == "MEDIUM":
        return (
            f"Moderate hazard level: Candidate within screening boundary ({distance_km:.2f} km). "
            f"Continued orbital monitoring recommended."
        )
    else:
        return (
            f"Low conjunction risk: Safe clearance of {distance_km:.2f} km maintained over evaluation horizon."
        )


def assess_conjunction_risk(
    candidate: ConjunctionCandidate,
    ref_time: Optional[datetime] = None
) -> RiskAssessment:
    """
    Pure business logic function evaluating a ConjunctionCandidate and returning a valid RiskAssessment.
    """
    dist_km = candidate.closest_approach.distance_km
    rel_vel = candidate.closest_approach.relative_velocity_km_s
    time_to_tca = calculate_time_to_tca_minutes(candidate.tca, ref_time=ref_time)

    score = calculate_risk_score(
        distance_km=dist_km,
        time_to_tca_minutes=time_to_tca,
        relative_velocity_km_s=rel_vel
    )
    
    tier = classify_risk_level(
        risk_score=score,
        distance_km=dist_km,
        time_to_tca_minutes=time_to_tca
    )

    notes = generate_risk_notes(
        conjunction_id=candidate.conjunction_id,
        distance_km=dist_km,
        time_to_tca_minutes=time_to_tca,
        relative_velocity_km_s=rel_vel,
        risk_level=tier
    )

    return RiskAssessment(
        conjunction_id=candidate.conjunction_id,
        risk_score=score,
        risk_level=tier,
        factors=RiskFactors(
            closest_approach_km=dist_km,
            time_to_tca_minutes=time_to_tca,
            relative_velocity_km_s=rel_vel
        ),
        uncertainty=UncertaintyInfo(
            model="PROTOTYPE_FIXED_UNCERTAINTY",
            confidence="HIGH" if dist_km < 10.0 else "MODERATE"
        ),
        notes=notes
    )
