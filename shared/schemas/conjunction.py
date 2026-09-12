from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ClosestApproach(BaseModel):
    distance_km: float = Field(..., description="Minimum separation distance in km at TCA")
    relative_velocity_km_s: float = Field(..., description="Relative speed in km/s between objects at TCA")


class ScreeningInfo(BaseModel):
    threshold_km: float = Field(..., description="Configured screening distance threshold in km")
    method: str = Field(default="SGP4_TRAJECTORY_SCREENING", description="Screening algorithm used")


class DataProvenance(BaseModel):
    primary_source: str = Field(default="CelesTrak", description="Primary orbital data source")
    propagator: str = Field(default="SGP4", description="Propagation model engine")


class ConjunctionCandidate(BaseModel):
    conjunction_id: str = Field(..., description="Unique identifier for the conjunction candidate event")
    primary_object: str = Field(..., description="ID / Catalog ID of primary tracked object (e.g. satellite)")
    secondary_object: str = Field(..., description="ID / Catalog ID of secondary tracked object (e.g. debris)")
    primary_object_name: Optional[str] = Field(default=None, description="Name of primary object")
    secondary_object_name: Optional[str] = Field(default=None, description="Name of secondary object")
    tca: datetime = Field(..., description="Time of Closest Approach (UTC ISO-8601)")
    closest_approach: ClosestApproach = Field(..., description="Closest approach distance and velocity")
    screening: ScreeningInfo = Field(..., description="Screening parameters")
    data_provenance: DataProvenance = Field(..., description="Data lineage metadata")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="UTC creation timestamp")
