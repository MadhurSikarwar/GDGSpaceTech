from datetime import datetime, timezone
from pydantic import BaseModel, Field


class SpaceWeatherSnapshot(BaseModel):
    kp_index: float = Field(..., description="Latest planetary Kp index (0-9, quasi-logarithmic)")
    ap_index: float = Field(..., description="Latest planetary A-index (a_running), linear geomagnetic-energy proxy")
    f107_sfu: float = Field(..., description="Latest observed F10.7 cm solar radio flux, solar flux units")
    activity_level: str = Field(..., description="Qualitative tier: QUIET, MODERATE, ELEVATED, STORM")
    drag_activity_scalar: float = Field(
        ..., description="Multiplier (>=1.0) applied to LEO along-track covariance growth rate, derived from ap_index"
    )
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC fetch timestamp")
    source: str = Field(default="NOAA_SWPC", description="Data source identifier")
    live: bool = Field(default=True, description="False when serving the quiet-sun fallback (NOAA unreachable)")
