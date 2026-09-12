from datetime import datetime
from pydantic import BaseModel, Field


class Vector3(BaseModel):
    x: float = Field(..., description="X coordinate (km or km/s)")
    y: float = Field(..., description="Y coordinate (km or km/s)")
    z: float = Field(..., description="Z coordinate (km or km/s)")


class StateVector(BaseModel):
    timestamp: datetime = Field(..., description="UTC timestamp of the state")
    position_km: Vector3 = Field(..., description="Position vector in kilometers")
    velocity_km_s: Vector3 = Field(..., description="Velocity vector in kilometers per second")
    altitude_km: float = Field(..., description="Geodetic altitude above WGS84 ellipsoid in kilometers")
    reference_frame: str = Field(default="TEME", description="Reference frame (default TEME for SGP4)")
