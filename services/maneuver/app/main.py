from fastapi import FastAPI
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate

app = FastAPI(
    title="OrbitalGuard Maneuver Agent",
    version="1.0.0",
    description="Generates simulated avoidance maneuver options (posigrade, retrograde, normal)."
)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Maneuver Agent"}


@app.post("/generate-maneuvers", response_model=ManeuverCandidates)
def generate_maneuvers(conjunction_id: str, satellite_id: str = "25544"):
    """
    Teammate Stub: Evaluates delta-V burns and output ManeuverCandidates contract.
    """
    return ManeuverCandidates(
        conjunction_id=conjunction_id,
        primary_object=satellite_id,
        candidates=[
            ManeuverCandidate(
                maneuver_id="M1",
                delta_v_m_s=0.85,
                burn_direction="POSIGRADE",
                new_separation_km=24.5,
                resulting_risk="MEDIUM"
            ),
            ManeuverCandidate(
                maneuver_id="M2",
                delta_v_m_s=1.42,
                burn_direction="RETROGRADE",
                new_separation_km=54.2,
                resulting_risk="LOW"
            )
        ]
    )
