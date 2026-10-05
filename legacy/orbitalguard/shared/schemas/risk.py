from typing import Optional
from pydantic import BaseModel, Field


class RiskFactors(BaseModel):
    closest_approach_km: float = Field(..., description="Distance at TCA in km")
    time_to_tca_minutes: float = Field(..., description="Minutes remaining until TCA")
    relative_velocity_km_s: float = Field(..., description="Relative velocity magnitude at TCA in km/s")


class UncertaintyInfo(BaseModel):
    model: str = Field(default="PROTOTYPE_FIXED_UNCERTAINTY", description="Uncertainty model applied")
    confidence: str = Field(default="MODERATE", description="Confidence assessment level")


class RiskAssessment(BaseModel):
    conjunction_id: str = Field(..., description="Referenced conjunction candidate ID")
    risk_score: float = Field(..., description="Calculated risk score (0 - 100)")
    risk_level: str = Field(..., description="Risk tier: CRITICAL, HIGH, MEDIUM, LOW")
    factors: RiskFactors = Field(..., description="Risk assessment input factors")
    uncertainty: UncertaintyInfo = Field(..., description="Uncertainty estimation metrics")
    notes: Optional[str] = Field(default=None, description="Additional agent summary or evaluation notes")
    collision_probability: Optional[float] = Field(
        default=None,
        description="Pc carried through from the source ConjunctionCandidate, when available. "
                     "Drives risk_level classification via classify_risk_level() when present."
    )
