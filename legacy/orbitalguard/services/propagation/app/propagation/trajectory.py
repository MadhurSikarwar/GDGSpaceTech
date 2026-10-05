from datetime import datetime, timedelta, timezone
from typing import Optional, List
from shared.schemas.trajectory import Trajectory, TrajectoryPoint
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine


def generate_trajectory(
    object_id: str,
    catalog_id: str,
    name: str,
    tle_line1: str,
    tle_line2: str,
    start_dt: Optional[datetime] = None,
    horizon_minutes: int = 90,
    step_minutes: float = 1.0
) -> Trajectory:
    """
    Generate future trajectory over horizon_minutes with step_minutes intervals.
    """
    if start_dt is None:
        start_dt = datetime.now(timezone.utc)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    engine = SGP4PropagationEngine(tle_line1, tle_line2, name)
    points: List[TrajectoryPoint] = []

    current_dt = start_dt
    end_dt = start_dt + timedelta(minutes=horizon_minutes)

    while current_dt <= end_dt:
        state = engine.propagate_state(current_dt)
        points.append(TrajectoryPoint(
            timestamp=current_dt,
            x=state.position_km.x,
            y=state.position_km.y,
            z=state.position_km.z,
            altitude_km=state.altitude_km
        ))
        current_dt += timedelta(minutes=step_minutes)

    return Trajectory(
        object_id=object_id,
        catalog_id=catalog_id,
        name=name,
        trajectory=points,
        propagation_horizon_minutes=horizon_minutes,
        step_minutes=step_minutes,
        generated_at=start_dt,
        reference_frame="TEME"
    )
