from typing import Optional
from pydantic import BaseModel, Field


class DecisionInfo(BaseModel):
    recommended_maneuver_id: str = Field(..., description="ID of recommended maneuver candidate")
    reason: str = Field(..., description="Decision justification and trade-off summary")


class SimulationInfo(BaseModel):
    status: str = Field(default="PENDING", description="Status: PENDING, EXECUTED, SIMULATED")
    new_tca_distance_km: Optional[float] = Field(default=None, description="Verified simulated post-burn distance")


class ManeuverDecision(BaseModel):
    conjunction_id: str = Field(..., description="Referenced conjunction candidate ID")
    decision: DecisionInfo = Field(..., description="Optimal decision recommendation")
    human_approval_required: bool = Field(default=True, description="Human approval gate indicator")
    simulation: SimulationInfo = Field(default_factory=SimulationInfo, description="Simulation outcome state")
