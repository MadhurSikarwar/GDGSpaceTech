from typing import List, Optional
from pydantic import BaseModel, Field

from shared.schemas.state import Vector3


class ManeuverCandidate(BaseModel):
    maneuver_id: str = Field(..., description="Unique maneuver identifier (e.g. M1, M2)")
    delta_v_m_s: float = Field(..., description="Impulse delta-V magnitude in m/s")
    burn_direction: str = Field(default="POSIGRADE", description="Burn direction: POSIGRADE, RETROGRADE, NORMAL, ANTINORMAL")
    new_separation_km: float = Field(..., description="Simulated post-maneuver separation distance in km")
    resulting_risk: str = Field(..., description="Post-maneuver risk tier: LOW, MEDIUM, HIGH")
    delta_v_vector: Optional[Vector3] = Field(
        default=None,
        description="Delta-V decomposed in the primary's RIC (radial/in-track/cross-track) frame, m/s per axis"
    )
    predicted_pc: Optional[float] = Field(
        default=None,
        description="Probability of collision predicted at TCA after this burn, via CW-propagated miss vector"
    )
    is_optimizer_minimum: Optional[bool] = Field(
        default=None,
        description="True for the single scipy.optimize.minimize fuel-optimal solution; False/None for companion candidates"
    )
    constraint_status: Optional[str] = Field(
        default=None,
        description="SATISFIED / PC_CONSTRAINT_ACTIVE / SLOT_CONSTRAINT_ACTIVE / INFEASIBLE"
    )


class ManeuverCandidates(BaseModel):
    conjunction_id: str = Field(..., description="Referenced conjunction candidate ID")
    primary_object: str = Field(..., description="Target satellite ID")
    candidates: List[ManeuverCandidate] = Field(..., description="List of evaluated maneuver options")
