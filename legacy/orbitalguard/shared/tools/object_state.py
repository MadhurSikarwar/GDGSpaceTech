"""
Tool: get_object_state
Retrieves the current or specified-epoch orbital state of a tracked space object.
Reuses existing SGP4 engine and database repository.
"""

from datetime import datetime, timezone
from typing import Optional

from shared.schemas.object import OrbitalObject, ObjectType, OrbitalData, DataQuality, PropagationInfo
from shared.schemas.state import StateVector
from shared.tools.errors import ObjectNotFoundError, ComputationError
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine


def get_object_state(
    catalog_id: str,
    epoch: Optional[datetime] = None,
    raw_tle_line1: Optional[str] = None,
    raw_tle_line2: Optional[str] = None,
    name: Optional[str] = None,
    object_type: Optional[str] = None,
) -> OrbitalObject:
    """
    Retrieve the propagated state vector and orbital metadata for a space object.

    Args:
        catalog_id: NORAD Catalog ID or unique object identifier.
        epoch: Target UTC datetime to propagate the state to. Defaults to current UTC time.
        raw_tle_line1: Optional raw TLE Line 1 override. If provided with line 2, bypasses DB lookup.
        raw_tle_line2: Optional raw TLE Line 2 override. If provided with line 1, bypasses DB lookup.
        name: Optional object name.
        object_type: Optional object classification (SATELLITE, DEBRIS, etc.).

    Returns:
        OrbitalObject containing state vector (position, velocity, altitude) in TEME frame.

    Raises:
        ObjectNotFoundError: If catalog_id cannot be resolved and no TLE was provided.
        ComputationError: If SGP4 propagation fails.
    """
    target_dt = epoch or datetime.now(timezone.utc)
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)

    tle1 = raw_tle_line1
    tle2 = raw_tle_line2
    obj_name = name or catalog_id
    obj_type = object_type or "UNKNOWN"
    intl_desig = None
    source = "CustomTLE" if (tle1 and tle2) else "CelesTrak"
    obj_epoch = target_dt
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
            obj_type = db_obj.object_type
            intl_desig = db_obj.international_designator
            source = db_obj.source or "CelesTrak"
            obj_epoch = db_obj.epoch
            object_id = db_obj.object_id
        finally:
            session.close()

    try:
        engine = SGP4PropagationEngine(tle1, tle2, obj_name)
        state = engine.propagate_state(target_dt)
        data_quality = engine.calculate_data_age(obj_epoch, target_dt)
    except Exception as e:
        raise ComputationError(f"SGP4 propagation failed for object '{catalog_id}': {e}") from e

    parsed_type = ObjectType(obj_type) if obj_type in ObjectType.__members__ else ObjectType.UNKNOWN

    return OrbitalObject(
        object_id=object_id,
        catalog_id=catalog_id,
        name=obj_name,
        object_type=parsed_type,
        international_designator=intl_desig,
        orbital_data=OrbitalData(
            epoch=obj_epoch,
            source=source,
            format="TLE",
            raw_tle_line1=tle1,
            raw_tle_line2=tle2
        ),
        state=state,
        propagation=PropagationInfo(model="SGP4", reference_frame="TEME"),
        data_quality=data_quality
    )
