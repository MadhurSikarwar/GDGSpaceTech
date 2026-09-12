import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from services.propagation.app.config import settings
from services.propagation.app.database.models import Base, ObjectDB, ConjunctionCandidateDB, HistoricalTLEDB
from services.propagation.app.realtime.connection_manager import manager as ws_manager
from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance

logger = logging.getLogger(__name__)


# Connect to DB engine with automatic SQLite fallback
try:
    connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
    engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
    # Test connection
    with engine.connect() as conn:
        pass
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
except Exception:
    # Graceful fallback to local SQLite
    sqlite_url = "sqlite:///./orbitalguard.db"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all database tables."""
    Base.metadata.create_all(bind=engine)
    migrate_additive_columns()


# SQLAlchemy's create_all() only creates missing TABLES -- it never alters an
# existing table's columns. orbitalguard.db already has a populated
# conjunction_candidates table (from before Pc support existed), so adding
# new Column(...) definitions to ConjunctionCandidateDB above does nothing to
# the live DB file on its own. This runs once at startup and additively
# ALTERs in whatever columns the current model declares but the live table
# is still missing -- nullable columns only, so existing rows just read back
# NULL for them, nothing is dropped or rewritten. SQLite supports additive
# `ALTER TABLE ... ADD COLUMN` natively, which is all this needs.
def migrate_additive_columns():
    inspector = inspect(engine)
    if "conjunction_candidates" not in inspector.get_table_names():
        return  # create_all() just made it fresh with every current column

    existing_cols = {c["name"] for c in inspector.get_columns("conjunction_candidates")}
    additive = {
        "collision_probability": "FLOAT",
        "pc_method": "VARCHAR(64)",
        "combined_hard_body_radius_m": "FLOAT",
    }
    missing = {name: sql_type for name, sql_type in additive.items() if name not in existing_cols}
    if not missing:
        return

    with engine.begin() as conn:
        for name, sql_type in missing.items():
            conn.execute(text(f"ALTER TABLE conjunction_candidates ADD COLUMN {name} {sql_type}"))
            logger.info(f"Migrated conjunction_candidates: added column '{name}' ({sql_type}).")


def get_db():
    """FastAPI DB session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# In-process counter bumped every time an object is written. routes.py keys
# its short-lived /objects response cache off this so a stale snapshot is
# never served: a plain time-based TTL alone would risk the frontend reloading
# the catalog immediately after an ingest/inject (which it does) and not
# seeing the new object for the rest of that TTL window.
_objects_version = 0


def get_objects_version() -> int:
    return _objects_version


class DatabaseRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_object(
        self,
        catalog_id: str,
        name: str,
        object_type: str,
        epoch: datetime,
        source: str = "CelesTrak",
        tle_line_1: Optional[str] = None,
        tle_line_2: Optional[str] = None,
        international_designator: Optional[str] = None,
        raw_data: Optional[dict] = None
    ) -> ObjectDB:
        db_obj = self.db.query(ObjectDB).filter(ObjectDB.catalog_id == catalog_id).first()

        now_dt = datetime.now(timezone.utc)

        if not db_obj:
            import uuid
            db_obj = ObjectDB(
                object_id=str(uuid.uuid4()),
                catalog_id=catalog_id,
                name=name,
                object_type=object_type,
                international_designator=international_designator,
                epoch=epoch,
                source=source,
                raw_tle_line1=tle_line_1 or "",
                raw_tle_line2=tle_line_2 or ""
            )
            self.db.add(db_obj)
        else:
            db_obj.name = name
            db_obj.object_type = object_type
            db_obj.epoch = epoch
            db_obj.source = source
            db_obj.raw_tle_line1 = tle_line_1 or ""
            db_obj.raw_tle_line2 = tle_line_2 or ""

        self.db.commit()
        self.db.refresh(db_obj)

        global _objects_version
        _objects_version += 1
        ws_manager.broadcast_threadsafe({
            "type": "object_updated",
            "catalog_id": db_obj.catalog_id,
            "object_type": db_obj.object_type,
            "name": db_obj.name,
        })

        return db_obj

    def get_object_count(self) -> int:
        return self.db.query(ObjectDB).count()

    def get_all_objects(
        self,
        object_type: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None
    ) -> List[ObjectDB]:
        from sqlalchemy import case
        query = self.db.query(ObjectDB)
        if object_type:
            query = query.filter(ObjectDB.object_type == object_type)
        priority_order = case(
            (ObjectDB.object_type == "SYNTHETIC_DEBRIS", 0),
            (ObjectDB.catalog_id == "25544", 1),
            else_=2
        )
        query = query.order_by(priority_order, ObjectDB.catalog_id)
        if offset is not None and offset > 0:
            query = query.offset(offset)
        if limit is not None and limit > 0:
            query = query.limit(limit)
        return query.all()

    def get_object_by_catalog_id(self, catalog_id: str) -> Optional[ObjectDB]:
        return self.db.query(ObjectDB).filter(ObjectDB.catalog_id == catalog_id).first()

    def save_conjunction(self, candidate: ConjunctionCandidate) -> ConjunctionCandidateDB:
        primary_obj = self.get_object_by_catalog_id(candidate.primary_object)
        secondary_obj = self.get_object_by_catalog_id(candidate.secondary_object)
        
        primary_id = primary_obj.object_id if primary_obj else None
        secondary_id = secondary_obj.object_id if secondary_obj else None

        db_conj = self.db.query(ConjunctionCandidateDB).filter(
            ConjunctionCandidateDB.conjunction_id == candidate.conjunction_id
        ).first()
        is_new = db_conj is None

        if not db_conj:
            db_conj = ConjunctionCandidateDB(
                conjunction_id=candidate.conjunction_id,
                primary_object_id=primary_id,
                secondary_object_id=secondary_id,
                primary_object_name=candidate.primary_object_name,
                secondary_object_name=candidate.secondary_object_name,
                tca=candidate.tca,
                miss_distance_km=candidate.closest_approach.distance_km,
                relative_velocity_km_s=candidate.closest_approach.relative_velocity_km_s,
                screening_threshold_km=candidate.screening.threshold_km,
                collision_probability=candidate.probability_of_collision,
                pc_method=candidate.pc_method,
                combined_hard_body_radius_m=candidate.combined_hard_body_radius_m,
                created_at=candidate.created_at
            )
            self.db.add(db_conj)
        else:
            db_conj.tca = candidate.tca
            db_conj.miss_distance_km = candidate.closest_approach.distance_km
            db_conj.relative_velocity_km_s = candidate.closest_approach.relative_velocity_km_s
            db_conj.collision_probability = candidate.probability_of_collision
            db_conj.pc_method = candidate.pc_method
            db_conj.combined_hard_body_radius_m = candidate.combined_hard_body_radius_m

        self.db.commit()
        self.db.refresh(db_conj)
        if is_new:
            ws_manager.broadcast_threadsafe({
                "type": "conjunction_flagged",
                "conjunction_id": db_conj.conjunction_id,
                "primary_object_name": db_conj.primary_object_name,
                "secondary_object_name": db_conj.secondary_object_name,
                "miss_distance_km": db_conj.miss_distance_km,
                "probability_of_collision": db_conj.collision_probability,
            })
        return db_conj

    def get_conjunctions(self) -> List[ConjunctionCandidate]:
        from sqlalchemy.orm import aliased
        PrimaryObj = aliased(ObjectDB)
        SecondaryObj = aliased(ObjectDB)

        records = self.db.query(
            ConjunctionCandidateDB, PrimaryObj.catalog_id, SecondaryObj.catalog_id
        ).outerjoin(
            PrimaryObj, ConjunctionCandidateDB.primary_object_id == PrimaryObj.object_id
        ).outerjoin(
            SecondaryObj, ConjunctionCandidateDB.secondary_object_id == SecondaryObj.object_id
        ).order_by(ConjunctionCandidateDB.created_at.desc()).all()

        result = []
        for r, primary_catalog_id, secondary_catalog_id in records:
            result.append(ConjunctionCandidate(
                conjunction_id=r.conjunction_id,
                primary_object=primary_catalog_id or "",
                secondary_object=secondary_catalog_id or "",
                primary_object_name=r.primary_object_name,
                secondary_object_name=r.secondary_object_name,
                tca=r.tca,
                closest_approach=ClosestApproach(
                    distance_km=r.miss_distance_km,
                    relative_velocity_km_s=r.relative_velocity_km_s
                ),
                screening=ScreeningInfo(
                    threshold_km=r.screening_threshold_km,
                    method="SGP4_TRAJECTORY_SCREENING"
                ),
                data_provenance=DataProvenance(
                    primary_source="CelesTrak",
                    propagator="SGP4"
                ),
                created_at=r.created_at,
                probability_of_collision=r.collision_probability,
                pc_method=r.pc_method,
                combined_hard_body_radius_m=r.combined_hard_body_radius_m
            ))
        return result
