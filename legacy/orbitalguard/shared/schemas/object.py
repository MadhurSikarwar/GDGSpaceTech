from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from shared.schemas.state import StateVector


class ObjectType(str, Enum):
    SATELLITE = "SATELLITE"
    DEBRIS = "DEBRIS"
    ROCKET_BODY = "ROCKET_BODY"
    SYNTHETIC_DEBRIS = "SYNTHETIC_DEBRIS"
    UNKNOWN = "UNKNOWN"


class OrbitalData(BaseModel):
    epoch: datetime = Field(..., description="UTC Epoch of the orbital elements")
    source: str = Field(default="CelesTrak", description="Data source (e.g. CelesTrak, Cache)")
    format: str = Field(default="TLE", description="Orbital element format (TLE / OMM)")
    raw_tle_line1: Optional[str] = Field(default=None, description="Raw TLE line 1 if applicable")
    raw_tle_line2: Optional[str] = Field(default=None, description="Raw TLE line 2 if applicable")
    raw_data: Optional[Dict[str, Any]] = Field(default=None, description="Original raw response record")


class PropagationInfo(BaseModel):
    model: str = Field(default="SGP4", description="Orbital propagator model")
    reference_frame: str = Field(default="TEME", description="Reference coordinate frame")


class DataQuality(BaseModel):
    data_age_hours: float = Field(..., description="Age of orbital element data in hours relative to current time")
    quality: str = Field(default="HIGH", description="Assessed quality level (HIGH, MEDIUM, LOW)")


class OrbitalObject(BaseModel):
    object_id: str = Field(..., description="Unique canonical identifier")
    catalog_id: str = Field(..., description="NORAD Catalog Number or Synthetic ID")
    name: str = Field(..., description="Common object name")
    object_type: ObjectType = Field(default=ObjectType.UNKNOWN, description="Category of space object")
    international_designator: Optional[str] = Field(default=None, description="COSPAR International Designator")
    orbital_data: OrbitalData = Field(..., description="Source orbital elements and epoch")
    state: Optional[StateVector] = Field(default=None, description="Current propagated state vector")
    propagation: PropagationInfo = Field(default_factory=PropagationInfo, description="Propagator configuration")
    data_quality: DataQuality = Field(..., description="Quality and data age metrics")
