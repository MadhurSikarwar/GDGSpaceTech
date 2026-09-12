import math
from typing import Optional, Dict
from fastapi import FastAPI, HTTPException, Body, Query
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.risk import RiskAssessment
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions
from services.risk.app.scoring import assess_conjunction_risk

from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(
    title="OrbitalGuard Maneuver Agent",
    version="1.0.0",
    description="Generates simulated avoidance maneuver options (posigrade, retrograde, normal)."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global in-memory cache for maneuver candidates by conjunction_id
_maneuver_cache: Dict[str, ManeuverCandidates] = {}


def get_cached_maneuvers(conjunction_id: str) -> Optional[ManeuverCandidates]:
    return _maneuver_cache.get(conjunction_id)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Maneuver Agent"}

def save_maneuver_candidates_to_db(candidates: ManeuverCandidates) -> bool:
    try:
        from services.propagation.app.database.repository import SessionLocal
        from services.propagation.app.database.models import ManeuverCandidateDB

        session = SessionLocal()
        try:
            for c in candidates.candidates:
                db_id = f"{candidates.conjunction_id}_{c.maneuver_id}"
                existing = session.query(ManeuverCandidateDB).filter(
                    ManeuverCandidateDB.maneuver_id == db_id
                ).first()
                if not existing:
                    record = ManeuverCandidateDB(
                        maneuver_id=db_id,
                        conjunction_id=candidates.conjunction_id,
                        burn_direction=c.burn_direction,
                        delta_v_m_s=c.delta_v_m_s,
                        new_separation_km=c.new_separation_km,
                        resulting_risk=c.resulting_risk
                    )
                    session.add(record)
            session.commit()
            return True
        finally:
            session.close()
    except Exception as e:
        print(f"Notice: Failed to persist maneuver candidates to DB: {e}")
    return False


@app.post("/generate-maneuvers", response_model=ManeuverCandidates)
def generate_maneuvers(
    risk_assessment: Optional[RiskAssessment] = Body(None, description="Direct RiskAssessment payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction candidate"),
    satellite_id: Optional[str] = Query("25544", description="Primary catalog ID")
):
    """
    Evaluates delta-V burns and outputs ManeuverCandidates contract.
    Accepts direct RiskAssessment body, or fetches and scores by conjunction_id.
    """
    target_assessment = risk_assessment
    target_satellite_id = satellite_id or "25544"

    if target_assessment is None:
        if not conjunction_id:
            raise HTTPException(
                status_code=400,
                detail="Either a RiskAssessment JSON body or a 'conjunction_id' query parameter must be provided."
            )

        # 1. Fetch conjunction candidate from DB, Tracking API, or fixtures
        candidate = get_conjunction_from_db(conjunction_id)
        if candidate is None:
            try:
                with httpx.Client(timeout=3.0) as client:
                    resp = client.get("http://localhost:8000/api/v1/conjunctions")
                    if resp.status_code == 200:
                        for c in resp.json():
                            if c.get("conjunction_id") == conjunction_id:
                                candidate = ConjunctionCandidate.model_validate(c)
                                break
            except Exception:
                pass

        if candidate is None:
            fixtures = load_fixture_conjunctions()
            for f in fixtures:
                if f.conjunction_id == conjunction_id:
                    candidate = f
                    break

        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conjunction with ID '{conjunction_id}' not found."
            )

        target_satellite_id = candidate.primary_object
        target_assessment = assess_conjunction_risk(candidate)

    # If the risk is LOW, we do not need to generate maneuvers
    if target_assessment.risk_level == "LOW":
        result = ManeuverCandidates(
            conjunction_id=target_assessment.conjunction_id,
            primary_object=target_satellite_id,
            candidates=[]
        )
        _maneuver_cache[target_assessment.conjunction_id] = result
        return result
    
    closest_approach_km = target_assessment.factors.closest_approach_km
    time_to_tca_minutes = target_assessment.factors.time_to_tca_minutes
    
    # Delta-V options in m/s (ranging from gentle orbital trim to emergency evasive burn)
    dv_options = [0.2, 0.5, 1.0, 2.0, 5.0]
    directions = ["POSIGRADE", "RETROGRADE"]
    
    candidates = []
    idx = 1
    
    for direction in directions:
        for dv in dv_options:
            # Simplified Clohessy-Wiltshire along-track displacement:
            # displacement_km = 0.18 * dv * time_to_tca_minutes
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

    result = ManeuverCandidates(
        conjunction_id=target_assessment.conjunction_id,
        primary_object=target_satellite_id,
        candidates=candidates
    )
    save_maneuver_candidates_to_db(result)
    _maneuver_cache[target_assessment.conjunction_id] = result
    return result
