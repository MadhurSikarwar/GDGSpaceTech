from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

from shared.schemas.state import Vector3


class ClosestApproach(BaseModel):
    distance_km: float = Field(..., description="Minimum separation distance in km at TCA")
    relative_velocity_km_s: float = Field(..., description="Relative speed in km/s between objects at TCA")
    relative_position_km: Optional[Vector3] = Field(
        default=None,
        description="Primary-minus-secondary relative position vector (TEME) at TCA. "
                     "Required input for encounter-plane Pc projection; absent for legacy records."
    )
    relative_velocity_vector_km_s: Optional[Vector3] = Field(
        default=None,
        description="Primary-minus-secondary relative velocity vector (TEME) at TCA. "
                     "Defines the encounter-plane normal for Pc; absent for legacy records."
    )


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC creation timestamp")
    probability_of_collision: Optional[float] = Field(
        default=None,
        description="Pc: probability of collision (0-1) via 2D Foster encounter-plane integration "
                     "over an empirical TLE-age covariance proxy. None if inputs were insufficient."
    )
    pc_method: Optional[str] = Field(
        default=None,
        description="Pc algorithm identifier, e.g. 'FOSTER_2D_TLE_AGE_COVARIANCE'"
    )
    combined_hard_body_radius_m: Optional[float] = Field(
        default=None,
        description="Combined (primary + secondary) hard-body radius in meters used for the Pc disk integral"
    )
