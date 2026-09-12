import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.propagation.app.config import settings
from services.propagation.app.database.repository import get_db, DatabaseRepository, get_objects_version
from services.propagation.app.ingestion.celestrak import CelesTrakIngestionClient
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine
from services.propagation.app.propagation.trajectory import generate_trajectory
from services.propagation.app.screening.conjunction import ScreeningPipeline
from services.propagation.app.screening.fine_filter import FineFilter
from services.propagation.app.synthetic.generator import generate_verified_synthetic_debris
from shared.schemas.object import OrbitalObject, ObjectType, OrbitalData, DataQuality, PropagationInfo
from shared.schemas.trajectory import Trajectory
from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.state import StateVector

router = APIRouter(prefix=settings.API_PREFIX, tags=["Orbital Intelligence Foundation"])


@router.get("/health", summary="Service Health & Status")
def get_health(db: Session = Depends(get_db)):
    repo = DatabaseRepository(db)
    object_count = repo.get_object_count()
    return {
        "status": "HEALTHY",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "database_url": settings.DATABASE_URL.split("://")[0],
        "offline_mode": settings.OFFLINE_MODE,
        "tracked_objects_count": object_count,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/ingest", summary="Ingest Orbital Data from CelesTrak or Cache")
def ingest_data(
    group: str = Query(default="active", description="CelesTrak group or cache"),
    db: Session = Depends(get_db)
):
    client = CelesTrakIngestionClient()
    tles = client.fetch_group_tles(group=group)
    repo = DatabaseRepository(db)

    ingested = []
    for item in tles:
        obj_db = repo.save_object(
            catalog_id=item["catalog_id"],
            name=item["name"],
            object_type=item["object_type"],
            epoch=item["epoch"],
            source=item["source"],
            tle_line_1=item["tle_line_1"],
            tle_line_2=item["tle_line_2"],
            international_designator=item.get("international_designator"),
            raw_data=item.get("raw_data")
        )
        ingested.append(obj_db.catalog_id)

    return {
        "message": f"Successfully ingested {len(ingested)} orbital records.",
        "source": "CelesTrakCache" if settings.OFFLINE_MODE else "CelesTrak",
        "catalog_ids": ingested
    }


def _propagate_objects(db_objs: List[Any]) -> List[OrbitalObject]:
    """Run SGP4 + build the response model for a batch of ObjectDB rows.

    This is the expensive part of GET /objects: one full propagation and
    Pydantic construction per object. Pulled out so it can be reused by both
    the (now cached) list endpoint and anything else that needs the same
    conversion for a small ad-hoc batch, without re-deriving it.
    """
    now_dt = datetime.now(timezone.utc)
    results: List[OrbitalObject] = []
    for o in db_objs:
        engine = SGP4PropagationEngine(o.raw_tle_line1, o.raw_tle_line2, o.name)
        state = engine.propagate_state(now_dt)
        data_quality = engine.calculate_data_age(o.epoch, now_dt)

        results.append(OrbitalObject(
            object_id=o.object_id,
            catalog_id=o.catalog_id,
            name=o.name,
            object_type=ObjectType(o.object_type) if o.object_type in ObjectType.__members__ else ObjectType.UNKNOWN,
            international_designator=o.international_designator,
            orbital_data=OrbitalData(
                epoch=o.epoch,
                source=o.source or "CelesTrak",
                format="TLE",
                raw_tle_line1=o.raw_tle_line1,
                raw_tle_line2=o.raw_tle_line2
            ),
            state=state,
            propagation=PropagationInfo(model="SGP4", reference_frame="TEME"),
            data_quality=data_quality
        ))
    return results


# ---- short-lived cache for the bulk objects list --------------------------
# GET /objects re-propagates every tracked object (SGP4 + Pydantic construction)
# from scratch on every single call. At ~10.9k live objects that's several
# seconds and multiple MB, and it's the single most-hit expensive endpoint in
# the service: it fires on every full page load, every "sync catalog" click,
# and immediately after ingest/inject (the frontend reloads the catalog right
# after injecting synthetic debris). Two complementary bounds on that cost:
#   - limit/offset below caps how many objects are ever fetched+propagated in
#     one call (frontend defaults to 5000 of however many are tracked), with
#     priority ordering in the DB layer (repository.get_all_objects)
#     guaranteeing synthetic debris and the ISS demo target are always on the
#     first page even when paginated.
#   - This cache serves a repeat call for the *same* (object_type, limit,
#     offset) instantly instead of recomputing it.
#
# Keyed on DatabaseRepository's write counter rather than a plain TTL alone,
# so a fresh ingest/inject is *never* masked by a stale cache entry -- the
# very next read always sees it. The TTL on top just bounds how long a state
# snapshot can be served without a write happening (position drifts a few km
# over a few seconds for LEO objects, which is already within the noise of
# "current state" for a dashboard, not a precision propagation).
#
# Keyed by (object_type, limit, offset) so different pages/filters don't
# collide. This process runs as one worker (see .claude/launch.json), so a
# plain module-level dict is enough; a benign race between two concurrent
# requests recomputing at once just means one extra recompute, never
# corrupted data (each entry is replaced atomically, never mutated in place).
_OBJECTS_CACHE_TTL_SECONDS = 4.0
_objects_cache: Dict[tuple, Dict[str, Any]] = {}


@router.get("/objects", response_model=List[OrbitalObject], summary="List Tracked Orbital Objects")
def get_objects(
    object_type: Optional[str] = Query(default=None, description="Filter by SATELLITE, DEBRIS, SYNTHETIC_DEBRIS"),
    limit: int = Query(default=5000, ge=1, le=5000, description="Maximum objects to propagate & return (1-5000)"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db)
):
    cache_key = (object_type, limit, offset)
    version = get_objects_version()
    cached = _objects_cache.get(cache_key)
    if (
        cached is not None
        and cached["version"] == version
        and (time.monotonic() - cached["computed_at"]) < _OBJECTS_CACHE_TTL_SECONDS
    ):
        return cached["results"]

    repo = DatabaseRepository(db)
    db_objs = repo.get_all_objects(object_type=object_type, limit=limit, offset=offset)
    results = _propagate_objects(db_objs)

    _objects_cache[cache_key] = {"version": version, "computed_at": time.monotonic(), "results": results}
    return results


@router.get("/objects/{object_id}", response_model=OrbitalObject, summary="Get Single Object Details & Instant State")
def get_object_detail(object_id: str, db: Session = Depends(get_db)):
    repo = DatabaseRepository(db)
    o = repo.get_object_by_catalog_id(object_id)
    if not o:
        # Search by database primary key ID
        objs = repo.get_all_objects()
        o = next((item for item in objs if item.object_id == object_id), None)

    if not o:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Object '{object_id}' not found.")

    now_dt = datetime.now(timezone.utc)
    engine = SGP4PropagationEngine(o.raw_tle_line1, o.raw_tle_line2, o.name)
    state = engine.propagate_state(now_dt)
    data_quality = engine.calculate_data_age(o.epoch, now_dt)

    return OrbitalObject(
        object_id=o.object_id,
        catalog_id=o.catalog_id,
        name=o.name,
        object_type=ObjectType(o.object_type) if o.object_type in ObjectType.__members__ else ObjectType.UNKNOWN,
        international_designator=o.international_designator,
        orbital_data=OrbitalData(
            epoch=o.epoch,
            source=o.source or "CelesTrak",
            format="TLE",
            raw_tle_line1=o.raw_tle_line1,
            raw_tle_line2=o.raw_tle_line2
        ),
        state=state,
        propagation=PropagationInfo(model="SGP4", reference_frame="TEME"),
        data_quality=data_quality
    )


@router.get("/objects/{object_id}/trajectory", response_model=Trajectory, summary="Get 90-Minute Future Trajectory")
def get_object_trajectory(
    object_id: str,
    horizon: int = Query(default=90, ge=1, le=1440, description="Propagation horizon in minutes"),
    step: float = Query(default=1.0, ge=0.1, le=60.0, description="Time step in minutes"),
    db: Session = Depends(get_db)
):
    repo = DatabaseRepository(db)
    o = repo.get_object_by_catalog_id(object_id)
    if not o:
        objs = repo.get_all_objects()
        o = next((item for item in objs if item.object_id == object_id), None)

    if not o:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Object '{object_id}' not found.")

    traj = generate_trajectory(
        object_id=o.object_id,
        catalog_id=o.catalog_id,
        name=o.name,
        tle_line1=o.raw_tle_line1,
        tle_line2=o.raw_tle_line2,
        horizon_minutes=horizon,
        step_minutes=step
    )
    return traj


@router.post("/screen", response_model=List[ConjunctionCandidate], summary="Run 2-Stage Screening Pipeline")
def run_screening_pipeline(
    horizon: int = Query(default=90, description="Screening horizon in minutes"),
    threshold_km: Optional[float] = Query(default=None, description="Distance threshold in km"),
    db: Session = Depends(get_db)
):
    repo = DatabaseRepository(db)
    db_objs = repo.get_all_objects()
    if not db_objs:
        # Ingest cached data first if database is empty
        ingest_data(group="active", db=db)
        db_objs = repo.get_all_objects()

    objects_data = []
    for o in db_objs:
        objects_data.append({
            "catalog_id": o.catalog_id,
            "name": o.name,
            "object_type": o.object_type,
            "tle_line_1": o.raw_tle_line1,
            "tle_line_2": o.raw_tle_line2,
            "source": o.source
        })

    pipeline = ScreeningPipeline(threshold_km=threshold_km)
    candidates = pipeline.run_screening(objects_data, horizon_minutes=horizon)

    # Save flagged conjunction candidates to DB
    for c in candidates:
        repo.save_conjunction(c)

    return candidates


@router.get("/conjunctions", response_model=List[ConjunctionCandidate], summary="List Conjunction Candidates")
def list_conjunctions(db: Session = Depends(get_db)):
    repo = DatabaseRepository(db)
    candidates = repo.get_conjunctions()
    return candidates


@router.post("/demo/inject-synthetic", summary="Inject Synthetic Debris & Guarantee Conjunction for Demo")
def inject_synthetic_demo(
    target_catalog_id: str = Query(default="25544", description="Target satellite catalog ID (e.g. ISS 25544)"),
    tca_offset_minutes: Optional[float] = Query(default=None, description="Optional custom TCA offset in minutes"),
    profile_index: Optional[int] = Query(default=None, description="Optional encounter geometry profile index (0-4)"),
    db: Session = Depends(get_db)
):
    repo = DatabaseRepository(db)
    target = repo.get_object_by_catalog_id(target_catalog_id)
    
    if not target:
        # Ingest cache first if missing
        ingest_data(group="active", db=db)
        target = repo.get_object_by_catalog_id(target_catalog_id)

    if not target:
        raise HTTPException(status_code=404, detail=f"Target satellite '{target_catalog_id}' not found.")

    target_data = {
        "catalog_id": target.catalog_id,
        "name": target.name,
        "tle_line_1": target.raw_tle_line1,
        "tle_line_2": target.raw_tle_line2
    }

    synth_item = generate_verified_synthetic_debris(
        target_data,
        tca_offset_minutes=tca_offset_minutes,
        profile_index=profile_index
    )

    # Save synthetic debris to DB
    synth_db = repo.save_object(
        catalog_id=synth_item["catalog_id"],
        name=synth_item["name"],
        object_type=synth_item["object_type"],
        epoch=synth_item["epoch"],
        source=synth_item["source"],
        tle_line_1=synth_item["tle_line_1"],
        tle_line_2=synth_item["tle_line_2"],
        international_designator=synth_item.get("international_designator"),
        raw_data=synth_item.get("raw_data")
    )

    # Screen ONLY the injected object against its intended target, not the
    # whole catalog. This used to call run_screening_pipeline() -- the same
    # handler behind POST /screen -- which pulls every tracked object
    # (~10.9k live) and runs the full O(primaries x secondaries) two-stage
    # pipeline (~10,792 satellites x ~96 debris/rocket-body/synthetic objects,
    # over a million candidate pairs, each coarse-filtered and many then
    # SGP4-propagated). That took minutes and blew well past the frontend's
    # 20s request timeout, so injecting debris always appeared to fail even
    # though it had actually succeeded server-side by the time it timed out.
    #
    # The demo's entire purpose is guaranteeing *this one* conjunction
    # (generate_verified_synthetic_debris already engineers the synthetic
    # object's orbit to cross the target's at ~8km) -- it was never about
    # discovering whether the new debris also has an unrelated close approach
    # with some other satellite. Screening exactly the pair we already know
    # about is both correct for that purpose and ~2 objects instead of
    # ~10.9k, so it completes in well under a second. The general /screen
    # endpoint (full catalog) is unchanged and still available as its own
    # explicit, user-triggered action.
    pair_data = [
        {
            "catalog_id": target.catalog_id,
            "name": target.name,
            "object_type": target.object_type,
            "tle_line_1": target.raw_tle_line1,
            "tle_line_2": target.raw_tle_line2,
            "source": target.source,
        },
        {
            "catalog_id": synth_db.catalog_id,
            "name": synth_db.name,
            "object_type": synth_db.object_type,
            "tle_line_1": synth_db.raw_tle_line1,
            "tle_line_2": synth_db.raw_tle_line2,
            "source": synth_db.source,
        },
    ]
    pipeline = ScreeningPipeline(threshold_km=50.0)
    conjunctions = pipeline.run_screening(pair_data, horizon_minutes=90)

    for c in conjunctions:
        repo.save_conjunction(c)

    return {
        "message": f"Successfully injected synthetic object {synth_item['name']}.",
        "synthetic_object_id": synth_db.catalog_id,
        "target_satellite": target.name,
        "conjunctions_detected": len(conjunctions),
        "conjunction_candidates": conjunctions
    }
