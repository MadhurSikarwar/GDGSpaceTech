"""
Tool-specific output schemas that complement shared/schemas/*.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from shared.schemas.ground_station import PassWindow


class PcComputationResult(BaseModel):
    """Result of Foster 2D encounter-plane probability-of-collision computation."""
    probability_of_collision: float = Field(..., description="Collision probability Pc (0.0 to 1.0)")
    estimated_error: float = Field(..., description="Numerical quadrature absolute error estimate")
    method: str = Field(default="FOSTER_2D_TLE_AGE_COVARIANCE", description="Pc algorithm identifier")
    combined_hard_body_radius_m: float = Field(..., description="Combined hard-body radius in meters used for integration")


class ManeuverConstraintEvaluation(BaseModel):
    """Comprehensive evaluation of a maneuver candidate against physical and operational constraints."""
    maneuver_id: str = Field(..., description="Maneuver candidate ID evaluated")
    is_feasible: bool = Field(..., description="True if all hard constraints are satisfied")
    overall_status: str = Field(..., description="SATISFIED, PC_CONSTRAINT_ACTIVE, SLOT_CONSTRAINT_ACTIVE, or VIOLATED")
    
    # Delta-V magnitude constraint
    delta_v_m_s: float = Field(..., description="Delta-V magnitude in m/s")
    max_delta_v_m_s: float = Field(..., description="Configured maximum allowed delta-V magnitude in m/s")
    delta_v_satisfied: bool = Field(..., description="True if delta_v_m_s <= max_delta_v_m_s")
    
    # Probability of collision constraint
    predicted_pc: Optional[float] = Field(default=None, description="Predicted post-burn collision probability")
    pc_threshold: float = Field(..., description="Critical collision probability safety threshold")
    pc_satisfied: Optional[bool] = Field(default=None, description="True if predicted_pc <= pc_threshold (when Pc is available)")
    
    # Operational slot drift constraint
    sma_drift_km: Optional[float] = Field(default=None, description="Calculated semi-major axis drift in km")
    max_sma_drift_km: float = Field(..., description="Configured maximum allowed semi-major axis drift in km")
    slot_drift_satisfied: Optional[bool] = Field(default=None, description="True if abs(sma_drift_km) <= max_sma_drift_km")
    
    # Ground station visibility constraint
    ground_station_visibility_checked: bool = Field(default=False, description="True if ground station pass check was performed")
    ground_station_satisfied: Optional[bool] = Field(default=None, description="True if object has at least one pass window during evaluation horizon")
    ground_station_passes: List[PassWindow] = Field(default_factory=list, description="Ground station visibility pass windows identified")
    
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional constraint evaluation diagnostics")
