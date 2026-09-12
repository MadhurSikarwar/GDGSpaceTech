"""
Tool: check_ground_station_visibility
Computes line-of-sight (AOS/LOS) pass windows between tracked space objects
and operational high-latitude ground stations (Svalbard, Fairbanks, McMurdo).
Reuses services/propagation/app/physics/ground_stations.py.
"""

from datetime import datetime, timezone
from typing import Optional, List

from shared.schemas.ground_station import GroundStationPasses, GroundStation
from shared.tools.errors import ObjectNotFoundError, ComputationError
from services.propagation.app.physics.ground_stations import (
    compute_passes_for_object,
    GROUND_STATIONS,
)
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository


def check_ground_station_visibility(
    catalog_id: str,
    start_dt: Optional[datetime] = None,
    horizon_minutes: int = 90,
    step_seconds: float = 30.0,
    raw_tle_line1: Optional[str] = None,
    raw_tle_line2: Optional[str] = None,
    name: Optional[str] = None,
) -> GroundStationPasses:
    """
    Compute AOS (Acquisition of Signal) and LOS (Loss of Signal) pass windows
    from operational polar ground stations to the target object.

    Args:
        catalog_id: NORAD Catalog ID of the space object.
        start_dt: Start timestamp for pass calculations (UTC). Defaults to now.
        horizon_minutes: Forward propagation horizon in minutes (default 90).
        step_seconds: Skyfield topocentric elevation sampling resolution (default 30.0s).
        raw_tle_line1: Optional direct TLE Line 1.
        raw_tle_line2: Optional direct TLE Line 2.
        name: Optional object name.

    Returns:
        GroundStationPasses containing chronological PassWindow items for all stations.

    Raises:
        ValueError: If horizon_minutes or step_seconds are non-positive.
        ObjectNotFoundError: If catalog_id cannot be resolved and no TLE was provided.
        ComputationError: If topocentric visibility calculations fail.
    """
    if horizon_minutes <= 0:
        raise ValueError(f"horizon_minutes must be positive, got {horizon_minutes}")
    if step_seconds <= 0:
        raise ValueError(f"step_seconds must be positive, got {step_seconds}")

    t0 = start_dt or datetime.now(timezone.utc)
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)

    tle1 = raw_tle_line1
    tle2 = raw_tle_line2
    obj_name = name or catalog_id

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
        finally:
            session.close()

    try:
        windows = compute_passes_for_object(
            tle_line1=tle1,
            tle_line2=tle2,
            name=obj_name,
            start_dt=t0,
            horizon_minutes=horizon_minutes,
            step_seconds=step_seconds,
        )

        return GroundStationPasses(
            catalog_id=catalog_id,
            generated_at=t0,
            horizon_minutes=horizon_minutes,
            passes=windows,
        )
    except Exception as e:
        raise ComputationError(f"Ground station pass calculation failed for object '{catalog_id}': {e}") from e


def list_ground_stations() -> List[GroundStation]:
    """Return the list of configured operational ground stations."""
    return list(GROUND_STATIONS)
