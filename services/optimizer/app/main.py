from fastapi import FastAPI
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo

app = FastAPI(
    title="OrbitalGuard Optimizer Agent",
    version="1.0.0",
    description="Selects optimal maneuver trade-off minimizing delta-V while mitigating conjunction risk."
)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Optimizer Agent"}


@app.post("/optimize-decision", response_model=ManeuverDecision)
def optimize_decision(conjunction_id: str):
    """
    Teammate Stub: Selects optimal candidate and outputs ManeuverDecision contract.
    """
    return ManeuverDecision(
        conjunction_id=conjunction_id,
        decision=DecisionInfo(
            recommended_maneuver_id="M2",
            reason="M2 retrograde burn provides 54.2 km separation, reducing risk to LOW with minimal fuel expenditure."
        ),
        human_approval_required=True,
        simulation=SimulationInfo(
            status="PENDING",
            new_tca_distance_km=54.2
        )
    )
