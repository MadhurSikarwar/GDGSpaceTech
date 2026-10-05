"""
Tool: run_screening
Runs the 2-stage coarse and fine conjunction screening pipeline across tracked objects.
Reuses existing services/propagation/app/screening/conjunction.py ScreeningPipeline.
"""

from typing import List, Optional, Dict, Any

from shared.schemas.conjunction import ConjunctionCandidate
from shared.tools.errors import ComputationError
from services.propagation.app.config import settings
from services.propagation.app.screening.conjunction import ScreeningPipeline
from services.propagation.app.spaceweather.noaa_client import get_space_weather
from services.propagation.app.database.repository import SessionLocal, DatabaseRepository


def run_screening(
    primary_catalog_ids: Optional[List[str]] = None,
    secondary_catalog_ids: Optional[List[str]] = None,
    horizon_minutes: int = 90,
    threshold_km: Optional[float] = None,
    combined_hbr_m: Optional[float] = None,
    objects_override: Optional[List[Dict[str, Any]]] = None,
    save_to_database: bool = False,
) -> List[ConjunctionCandidate]:
    """
    Execute deterministic two-stage conjunction screening (altitude overlap filter + SGP4 fine refinement).

    Args:
        primary_catalog_ids: Optional filter list of primary satellite catalog IDs.
        secondary_catalog_ids: Optional filter list of secondary debris catalog IDs.
        horizon_minutes: Forward screening horizon in minutes (default 90).
        threshold_km: Minimum distance threshold in km for declaring a conjunction (default 50 km).
        combined_hbr_m: Combined hard-body radius in meters for Pc computation.
        objects_override: Direct list of object dictionaries to screen instead of database objects.
        save_to_database: If True, persists detected conjunction candidates to database.

    Returns:
        List of ConjunctionCandidate records meeting the encounter screening threshold.

    Raises:
        ValueError: If horizon_minutes or threshold_km are non-positive.
        ComputationError: If screening pipeline fails or no objects are available.
    """
    if horizon_minutes <= 0:
        raise ValueError(f"horizon_minutes must be positive, got {horizon_minutes}")
    if threshold_km is not None and threshold_km <= 0:
        raise ValueError(f"threshold_km must be positive, got {threshold_km}")

    thresh = threshold_km or settings.SCREENING_THRESHOLD_KM
    hbr_km = (combined_hbr_m or settings.COMBINED_HARD_BODY_RADIUS_M) / 1000.0

    try:
        weather = get_space_weather()
        drag_scalar = weather.drag_activity_scalar
    except Exception:
        drag_scalar = 1.0

    session = SessionLocal()
    try:
        repo = DatabaseRepository(session)
        if objects_override is not None:
            objects_data = objects_override
        else:
            db_objs = repo.get_all_objects()
            if not db_objs:
                raise ComputationError("No tracked objects found in database to screen.")

            objects_data = []
            for o in db_objs:
                # Apply optional filtering
                if primary_catalog_ids and o.object_type in ("SATELLITE", "PRIMARY") and o.catalog_id not in primary_catalog_ids:
                    continue
                if secondary_catalog_ids and o.object_type not in ("SATELLITE", "PRIMARY") and o.catalog_id not in secondary_catalog_ids:
                    continue

                objects_data.append({
                    "catalog_id": o.catalog_id,
                    "name": o.name,
                    "object_type": o.object_type,
                    "tle_line_1": o.raw_tle_line1,
                    "tle_line_2": o.raw_tle_line2,
                    "source": o.source,
                    "epoch": o.epoch,
                })

        if len(objects_data) < 2:
            raise ComputationError(
                f"At least 2 space objects are required for conjunction screening, found {len(objects_data)}."
            )

        pipeline = ScreeningPipeline(
            threshold_km=thresh,
            combined_hbr_km=hbr_km,
            drag_activity_scalar=drag_scalar,
        )

        candidates = pipeline.run_screening(objects_data, horizon_minutes=horizon_minutes)

        if save_to_database:
            for c in candidates:
                repo.save_conjunction(c)

        return candidates
    except ComputationError:
        raise
    except Exception as e:
        raise ComputationError(f"Conjunction screening execution failed: {e}") from e
    finally:
        session.close()
