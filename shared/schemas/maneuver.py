from typing import List, Optional
from pydantic import BaseModel, Field


class ManeuverCandidate(BaseModel):
    maneuver_id: str = Field(..., description="Unique maneuver identifier (e.g. M1, M2)")
    delta_v_m_s: float = Field(..., description="Impulse delta-V magnitude in m/s")
    burn_direction: str = Field(default="POSIGRADE", description="Burn direction: POSIGRADE, RETROGRADE, NORMAL, ANTINORMAL")
    new_separation_km: float = Field(..., description="Simulated post-maneuver separation distance in km")
    resulting_risk: str = Field(..., description="Post-maneuver risk tier: LOW, MEDIUM, HIGH")


class ManeuverCandidates(BaseModel):
    conjunction_id: str = Field(..., description="Referenced conjunction candidate ID")
    primary_object: str = Field(..., description="Target satellite ID")
    candidates: List[ManeuverCandidate] = Field(..., description="List of evaluated maneuver options")
