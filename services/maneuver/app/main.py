import math
from fastapi import FastAPI
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.risk import RiskAssessment

app = FastAPI(
    title="OrbitalGuard Maneuver Agent",
    version="1.0.0",
    description="Generates simulated avoidance maneuver options (posigrade, retrograde, normal)."
)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Maneuver Agent"}


@app.post("/generate-maneuvers", response_model=ManeuverCandidates)
def generate_maneuvers(risk_assessment: RiskAssessment, satellite_id: str = "25544"):
    """
    Evaluates delta-V burns and outputs ManeuverCandidates contract.
    """
    # If the risk is LOW, we do not need to generate maneuvers
    if risk_assessment.risk_level == "LOW":
        return ManeuverCandidates(
            conjunction_id=risk_assessment.conjunction_id,
            primary_object=satellite_id,
            candidates=[]
        )
    
    closest_approach_km = risk_assessment.factors.closest_approach_km
    time_to_tca_minutes = risk_assessment.factors.time_to_tca_minutes
    
    # Delta-V options in m/s (ranging from gentle orbital trim to emergency evasive burn)
    dv_options = [0.2, 0.5, 1.0, 2.0, 5.0]
    directions = ["POSIGRADE", "RETROGRADE"]
    
    candidates = []
    idx = 1
    
    for direction in directions:
        for dv in dv_options:
            # Simplified Clohessy-Wiltshire along-track displacement:
            # displacement_km = 3 * (dv / 1000) * (time_to_tca_minutes * 60)
            # Which simplifies to: displacement_km = 0.18 * dv * time_to_tca_minutes
            displacement_km = 0.18 * dv * time_to_tca_minutes
            
            # Orthogonal vector addition approximation
            new_separation = math.sqrt(closest_approach_km**2 + displacement_km**2)
            
            if new_separation > 50.0:
                resulting_risk = "LOW"
            elif new_separation > 10.0:
                resulting_risk = "MEDIUM"
            else:
                resulting_risk = "HIGH"
                
            candidates.append(
                ManeuverCandidate(
                    maneuver_id=f"M{idx}",
                    delta_v_m_s=dv,
                    burn_direction=direction,
                    new_separation_km=round(new_separation, 2),
                    resulting_risk=resulting_risk
                )
            )
            idx += 1

    return ManeuverCandidates(
        conjunction_id=risk_assessment.conjunction_id,
        primary_object=satellite_id,
        candidates=candidates
    )
