from fastapi import FastAPI
from shared.schemas.risk import RiskAssessment, RiskFactors, UncertaintyInfo

app = FastAPI(
    title="OrbitalGuard Risk Agent",
    version="1.0.0",
    description="Calculates risk assessment scores for flagged conjunction candidates."
)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Risk Agent"}


@app.post("/assess-risk", response_model=RiskAssessment)
def assess_risk(conjunction_id: str):
    """
    Teammate Stub: Consumes ConjunctionCandidate from Tracking/Screening service
    and produces RiskAssessment contract.
    """
    return RiskAssessment(
        conjunction_id=conjunction_id,
        risk_score=87.5,
        risk_level="HIGH",
        factors=RiskFactors(
            closest_approach_km=8.2,
            time_to_tca_minutes=42.0,
            relative_velocity_km_s=7.4
        ),
        uncertainty=UncertaintyInfo(),
        notes="High risk candidate flagged by Risk Agent stub."
    )
