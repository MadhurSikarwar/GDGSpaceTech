import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, Text, Boolean, Integer
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ObjectDB(Base):
    __tablename__ = "orbital_objects"

    object_id = Column(String(64), primary_key=True)
    catalog_id = Column(String(32), index=True, nullable=False)
    name = Column(String(128), nullable=False)
    object_type = Column(String(32), default="SATELLITE")
    international_designator = Column(String(32), nullable=True)
    epoch = Column(DateTime(timezone=True), nullable=False, default=get_utc_now)
    source = Column(String(64), default="CelesTrak")
    raw_tle_line1 = Column(Text, nullable=False)
    raw_tle_line2 = Column(Text, nullable=False)


class ConjunctionCandidateDB(Base):
    __tablename__ = "conjunction_candidates"

    conjunction_id = Column(String(128), primary_key=True)
    primary_object_id = Column(String(64), nullable=True)
    secondary_object_id = Column(String(64), nullable=True)
    primary_object_name = Column(String(128), nullable=False)
    secondary_object_name = Column(String(128), nullable=False)
    tca = Column(DateTime(timezone=True), nullable=False)
    miss_distance_km = Column(Float, nullable=False)
    relative_velocity_km_s = Column(Float, nullable=False)
    screening_threshold_km = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)


class RiskAssessmentDB(Base):
    __tablename__ = "risk_assessments"
    
    id = Column(String(64), primary_key=True)
    conjunction_id = Column(String(128), nullable=True)
    risk_score = Column(Float, nullable=False)
    risk_level = Column(String(32), nullable=False)
    closest_approach_km = Column(Float, nullable=False)
    time_to_tca_minutes = Column(Float, nullable=False)
    relative_velocity_km_s = Column(Float, nullable=False)
    collision_probability = Column(Float, nullable=True)
    uncertainty_model = Column(String(64), default="PROTOTYPE_FIXED_UNCERTAINTY")
    confidence = Column(String(16), default="MODERATE")
    notes = Column(Text, nullable=True)
    evaluated_at = Column(DateTime(timezone=True), default=get_utc_now)


class ManeuverCandidateDB(Base):
    __tablename__ = "maneuver_candidates"
    
    maneuver_id = Column(String(64), primary_key=True)
    conjunction_id = Column(String(128), nullable=False)
    delta_v_m_s = Column(Float, nullable=False)
    burn_direction = Column(String(32), nullable=False)
    new_separation_km = Column(Float, nullable=False)
    resulting_risk = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)


class ManeuverDecisionDB(Base):
    __tablename__ = "maneuver_decisions"
    
    conjunction_id = Column(String(128), primary_key=True)
    recommended_maneuver_id = Column(String(64), nullable=False)
    decision_reason = Column(Text, nullable=False)
    human_approval_required = Column(Boolean, default=True)
    simulation_status = Column(String(32), default="PENDING")
    new_tca_distance_km = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)


class HistoricalTLEDB(Base):
    __tablename__ = "historical_tle"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    object_id = Column(String(64), nullable=False)
    catalog_id = Column(String(32), nullable=False)
    epoch = Column(DateTime(timezone=True), nullable=False)
    raw_tle = Column(Text, nullable=False)
    source = Column(String(64), default="CelesTrak")
    timestamp = Column(DateTime(timezone=True), default=get_utc_now)
