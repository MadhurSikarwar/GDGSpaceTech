import os
import json
import logging
from typing import Optional, List
from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.risk import RiskAssessment

logger = logging.getLogger("risk_database")

# Check if SQLAlchemy database connection is possible
_session_factory = None
_db_available = False

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from services.propagation.app.database.models import ConjunctionDB

    db_url = os.getenv("DATABASE_URL", "sqlite:///./orbitalguard.db")
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    
    # Check if host is resolvable or SQLite
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
    _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _db_available = True
except Exception as e:
    logger.warning(f"Database connection initialization skipped: {e}")
    _db_available = False


def get_conjunction_from_db(conjunction_id: str) -> Optional[ConjunctionCandidate]:
    """Retrieve conjunction candidate from database if available."""
    if not _db_available or _session_factory is None:
        return None
    
    session = _session_factory()
    try:
        from services.propagation.app.database.models import ConjunctionDB
        from shared.schemas.conjunction import ClosestApproach, ScreeningInfo, DataProvenance

        record = session.query(ConjunctionDB).filter(ConjunctionDB.conjunction_id == conjunction_id).first()
        if not record:
            return None
        
        return ConjunctionCandidate(
            conjunction_id=record.conjunction_id,
            primary_object=record.satellite_id,
            secondary_object=record.debris_id,
            primary_object_name=record.primary_object_name,
            secondary_object_name=record.secondary_object_name,
            tca=record.tca,
            closest_approach=ClosestApproach(
                distance_km=record.closest_approach_km,
                relative_velocity_km_s=record.relative_velocity_kms
            ),
            screening=ScreeningInfo(
                threshold_km=record.screening_threshold_km,
                method="SGP4_TRAJECTORY_SCREENING"
            ),
            data_provenance=DataProvenance(
                primary_source="CelesTrak",
                propagator="SGP4"
            ),
            created_at=record.created_at
        )
    except Exception as e:
        logger.warning(f"Error querying conjunction {conjunction_id} from DB: {e}")
        return None
    finally:
        session.close()


def save_risk_assessment_to_db(assessment: RiskAssessment) -> bool:
    """
    Persist risk assessment outcome to database record if database is reachable.
    Updates risk_score and risk_level on the conjunction candidate.
    """
    if not _db_available or _session_factory is None:
        return False
    
    session = _session_factory()
    try:
        from services.propagation.app.database.models import ConjunctionDB

        record = session.query(ConjunctionDB).filter(
            ConjunctionDB.conjunction_id == assessment.conjunction_id
        ).first()

        if record:
            record.risk_score = assessment.risk_score
            record.risk_level = assessment.risk_level
            session.commit()
            return True
        return False
    except Exception as e:
        logger.warning(f"Error persisting risk assessment {assessment.conjunction_id} to DB: {e}")
        session.rollback()
        return False
    finally:
        session.close()


def load_fixture_conjunctions(fixture_path: Optional[str] = None) -> List[ConjunctionCandidate]:
    """Load sample conjunction candidates from JSON fixture file for offline/standalone execution."""
    if fixture_path is None:
        # Default path relative to workspace
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        fixture_path = os.path.join(base_dir, "propagation", "data", "sample_conjunctions.json")

    if not os.path.exists(fixture_path):
        return []

    try:
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [ConjunctionCandidate.model_validate(item) for item in data]
    except Exception as e:
        logger.error(f"Failed to load fixture from {fixture_path}: {e}")
        return []
