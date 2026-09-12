from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class GroundStation(BaseModel):
    station_id: str = Field(..., description="Short station identifier, e.g. 'SVALBARD'")
    name: str = Field(..., description="Full station name")
    lat_deg: float = Field(..., description="Geodetic latitude, degrees (+N)")
    lon_deg: float = Field(..., description="Geodetic longitude, degrees (+E)")
    altitude_m: float = Field(default=0.0, description="Elevation above WGS84 ellipsoid, meters")
    min_elevation_deg: float = Field(default=10.0, description="Minimum elevation mask angle, degrees")


class PassWindow(BaseModel):
    station_id: str = Field(..., description="Ground station this window belongs to")
    aos: datetime = Field(..., description="Acquisition of signal (UTC) — elevation crosses min_elevation_deg rising")
    los: datetime = Field(..., description="Loss of signal (UTC) — elevation crosses min_elevation_deg falling")
    max_elevation_deg: float = Field(..., description="Peak elevation angle reached during the pass")


class GroundStationPasses(BaseModel):
    catalog_id: str = Field(..., description="NORAD / catalog ID of the object these passes were computed for")
    generated_at: datetime = Field(..., description="UTC generation timestamp")
    horizon_minutes: int = Field(..., description="Propagation horizon the passes were computed over")
    passes: List[PassWindow] = Field(default_factory=list, description="Chronological AOS/LOS windows across all stations")
