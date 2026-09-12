from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.propagation.app.config import settings
from services.propagation.app.database.repository import get_db, DatabaseRepository
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


@router.get("/objects", response_model=List[OrbitalObject], summary="List Tracked Orbital Objects")
def get_objects(
    object_type: Optional[str] = Query(default=None, description="Filter by SATELLITE, DEBRIS, SYNTHETIC_DEBRIS"),
    limit: int = Query(default=500, ge=1, le=5000, description="Maximum objects to propagate & return (1-5000)"),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db)
):
    repo = DatabaseRepository(db)
    db_objs = repo.get_all_objects(object_type=object_type, limit=limit, offset=offset)
    now_dt = datetime.now(timezone.utc)

    results: List[OrbitalObject] = []
    for o in db_objs:
        engine = SGP4PropagationEngine(o.raw_tle_line1, o.raw_tle_line2, o.name)
        state = engine.propagate_state(now_dt)
        data_quality = engine.calculate_data_age(o.epoch, now_dt)

        obj = OrbitalObject(
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
        results.append(obj)

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

    synth_item = generate_verified_synthetic_debris(target_data, tca_offset_minutes=45.0, target_separation_km=8.2)

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

    # Target conjunction screening specifically between target satellite and synthetic debris
    fine_filter = FineFilter(threshold_km=50.0)
    candidate = fine_filter.compute_conjunction_candidate(target_data, synth_item, horizon_minutes=90)

    conjunctions = []
    if candidate:
        repo.save_conjunction(candidate)
        conjunctions.append(candidate)

    return {
        "message": f"Successfully injected synthetic object {synth_item['name']}.",
        "synthetic_object_id": synth_db.catalog_id,
        "target_satellite": target.name,
        "conjunctions_detected": len(conjunctions),
        "conjunction_candidates": conjunctions
    }
