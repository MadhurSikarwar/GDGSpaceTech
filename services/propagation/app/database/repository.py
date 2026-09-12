import json
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from services.propagation.app.config import settings
from services.propagation.app.database.models import Base, ObjectDB, ConjunctionCandidateDB, HistoricalTLEDB
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

        return db_obj

    def get_all_objects(self, object_type: Optional[str] = None) -> List[ObjectDB]:
        query = self.db.query(ObjectDB)
        if object_type:
            query = query.filter(ObjectDB.object_type == object_type)
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
                created_at=candidate.created_at
            )
            self.db.add(db_conj)
        else:
            db_conj.tca = candidate.tca
            db_conj.miss_distance_km = candidate.closest_approach.distance_km
            db_conj.relative_velocity_km_s = candidate.closest_approach.relative_velocity_km_s

        self.db.commit()
        self.db.refresh(db_conj)
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
                created_at=r.created_at
            ))
        return result
