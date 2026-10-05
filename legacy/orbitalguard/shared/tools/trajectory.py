"""
Tool: propagate_trajectory
Propagates an object's future trajectory using the existing SGP4 trajectory implementation.
Reuses generate_trajectory and shared trajectory schemas.
"""

from datetime import datetime, timezone
from typing import Optional

from shared.schemas.trajectory import Trajectory
from shared.tools.errors import ObjectNotFoundError, ComputationError
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository
from services.propagation.app.propagation.trajectory import generate_trajectory as _generate_trajectory


def propagate_trajectory(
    catalog_id: str,
    horizon_minutes: int = 90,
    step_minutes: float = 1.0,
    start_dt: Optional[datetime] = None,
    raw_tle_line1: Optional[str] = None,
    raw_tle_line2: Optional[str] = None,
    name: Optional[str] = None,
) -> Trajectory:
    """
    Propagate an object's orbital trajectory over a forward time horizon.

    Args:
        catalog_id: NORAD Catalog ID of the target object.
        horizon_minutes: Forward propagation duration in minutes (positive integer).
        step_minutes: Ephemeris time step size in minutes (positive float).
        start_dt: Start timestamp for trajectory propagation (UTC). Defaults to now.
        raw_tle_line1: Optional TLE line 1 override.
        raw_tle_line2: Optional TLE line 2 override.
        name: Optional object name.

    Returns:
        Trajectory object containing time-stamped TEME coordinates and altitudes.

    Raises:
        ValueError: If horizon_minutes or step_minutes are non-positive.
        ObjectNotFoundError: If catalog_id is not found and no TLE was provided.
        ComputationError: If SGP4 trajectory propagation fails.
    """
    if horizon_minutes <= 0:
        raise ValueError(f"horizon_minutes must be positive, got {horizon_minutes}")
    if step_minutes <= 0:
        raise ValueError(f"step_minutes must be positive, got {step_minutes}")

    t0 = start_dt or datetime.now(timezone.utc)
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)

    tle1 = raw_tle_line1
    tle2 = raw_tle_line2
    obj_name = name or catalog_id
    object_id = catalog_id

    if not (tle1 and tle2):
        session = SessionLocal()
        try:
            repo = DatabaseRepository(session)
            db_obj = repo.get_object_by_catalog_id(catalog_id)
            if not db_obj:
                raise ObjectNotFoundError(f"Tracked space object with catalog ID '{catalog_id}' not found.")
            tle1 = db_obj.raw_tle_line1
            tle2 = db_obj.raw_tle_line2
            obj_name = db_obj.name
            object_id = db_obj.object_id
        finally:
            session.close()

    try:
        return _generate_trajectory(
            object_id=object_id,
            catalog_id=catalog_id,
            name=obj_name,
            tle_line1=tle1,
            tle_line2=tle2,
            horizon_minutes=horizon_minutes,
            step_minutes=step_minutes,
            start_dt=t0,
        )
    except Exception as e:
        raise ComputationError(f"Trajectory propagation failed for object '{catalog_id}': {e}") from e
