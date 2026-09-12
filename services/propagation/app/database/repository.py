import json
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from services.propagation.app.config import settings
from services.propagation.app.database.models import Base, ObjectDB, ConjunctionDB, HistoricalTLEDB
from shared.schemas.conjunction import ConjunctionCandidate, ClosestApproach, ScreeningInfo, DataProvenance


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


def get_db():
    """FastAPI DB session dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
        raw_str = json.dumps(raw_data) if raw_data else None

        now_dt = datetime.now(timezone.utc)

        if not db_obj:
            db_obj = ObjectDB(
                catalog_id=catalog_id,
                object_name=name,
                object_type=object_type,
                international_designator=international_designator,
                epoch=epoch,
                source=source,
                tle_line_1=tle_line_1,
                tle_line_2=tle_line_2,
                raw_data=raw_str,
                ingested_at=now_dt
            )
            self.db.add(db_obj)
        else:
            db_obj.object_name = name
            db_obj.object_type = object_type
            db_obj.epoch = epoch
            db_obj.source = source
            db_obj.tle_line_1 = tle_line_1
            db_obj.tle_line_2 = tle_line_2
            db_obj.raw_data = raw_str
            db_obj.updated_at = now_dt

        self.db.commit()
        self.db.refresh(db_obj)

        # Save historical snapshot
        if tle_line_1 and tle_line_2:
            hist = HistoricalTLEDB(
                object_id=db_obj.id,
                catalog_id=catalog_id,
                epoch=epoch,
                raw_tle=f"{tle_line_1}\n{tle_line_2}",
                source=source,
                timestamp=now_dt
            )
            self.db.add(hist)
            self.db.commit()

        return db_obj

    def get_all_objects(self, object_type: Optional[str] = None) -> List[ObjectDB]:
        query = self.db.query(ObjectDB)
        if object_type:
            query = query.filter(ObjectDB.object_type == object_type)
        return query.all()

    def get_object_by_catalog_id(self, catalog_id: str) -> Optional[ObjectDB]:
        return self.db.query(ObjectDB).filter(ObjectDB.catalog_id == catalog_id).first()

    def save_conjunction(self, candidate: ConjunctionCandidate) -> ConjunctionDB:
        db_conj = self.db.query(ConjunctionDB).filter(
            ConjunctionDB.conjunction_id == candidate.conjunction_id
        ).first()

        if not db_conj:
            db_conj = ConjunctionDB(
                conjunction_id=candidate.conjunction_id,
                satellite_id=candidate.primary_object,
                debris_id=candidate.secondary_object,
                primary_object_name=candidate.primary_object_name,
                secondary_object_name=candidate.secondary_object_name,
                tca=candidate.tca,
                closest_approach_km=candidate.closest_approach.distance_km,
                relative_velocity_kms=candidate.closest_approach.relative_velocity_km_s,
                screening_threshold_km=candidate.screening.threshold_km,
                created_at=candidate.created_at
            )
            self.db.add(db_conj)
        else:
            db_conj.tca = candidate.tca
            db_conj.closest_approach_km = candidate.closest_approach.distance_km
            db_conj.relative_velocity_kms = candidate.closest_approach.relative_velocity_km_s

        self.db.commit()
        self.db.refresh(db_conj)
        return db_conj

    def get_conjunctions(self) -> List[ConjunctionCandidate]:
        records = self.db.query(ConjunctionDB).order_by(ConjunctionDB.created_at.desc()).all()
        result = []
        for r in records:
            result.append(ConjunctionCandidate(
                conjunction_id=r.conjunction_id,
                primary_object=r.satellite_id,
                secondary_object=r.debris_id,
                primary_object_name=r.primary_object_name,
                secondary_object_name=r.secondary_object_name,
                tca=r.tca,
                closest_approach=ClosestApproach(
                    distance_km=r.closest_approach_km,
                    relative_velocity_km_s=r.relative_velocity_kms
                ),
                screening=ScreeningInfo(
                    threshold_km=r.screening_threshold_km,
                    method="SGP4_TRAJECTORY_SCREENING"
                ),
                data_provenance=DataProvenance(
                    primary_source="CelesTrak",
                    propagator="SGP4"
                ),
                created_at=r.created_at
            ))
        return result
