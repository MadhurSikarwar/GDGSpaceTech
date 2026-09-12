from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class TrajectoryPoint(BaseModel):
    timestamp: datetime = Field(..., description="UTC timestamp of trajectory point")
    x: float = Field(..., description="X position in km")
    y: float = Field(..., description="Y position in km")
    z: float = Field(..., description="Z position in km")
    altitude_km: float = Field(..., description="Geodetic altitude in km")


class Trajectory(BaseModel):
    object_id: str = Field(..., description="Canonical ID of the object")
    catalog_id: str = Field(..., description="NORAD / Catalog ID")
    name: str = Field(..., description="Object name")
    trajectory: List[TrajectoryPoint] = Field(..., description="List of trajectory points")
    propagation_horizon_minutes: int = Field(default=90, description="Horizon in minutes")
    step_minutes: float = Field(default=1.0, description="Step size in minutes")
    generated_at: datetime = Field(..., description="UTC generation timestamp")
    reference_frame: str = Field(default="TEME", description="Reference frame")
