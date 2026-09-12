import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ObjectDB(Base):
    __tablename__ = "objects"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    catalog_id = Column(String(32), index=True, nullable=False)
    object_name = Column(String(128), nullable=False)
    object_type = Column(String(32), default="UNKNOWN")
    international_designator = Column(String(32), nullable=True)
    epoch = Column(DateTime(timezone=True), nullable=False, default=get_utc_now)
    source = Column(String(64), default="CelesTrak")
    tle_line_1 = Column(String(128), nullable=True)
    tle_line_2 = Column(String(128), nullable=True)
    raw_data = Column(Text, nullable=True)
    ingested_at = Column(DateTime(timezone=True), default=get_utc_now)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now)


class ConjunctionDB(Base):
    __tablename__ = "conjunctions"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    conjunction_id = Column(String(64), unique=True, index=True, nullable=False)
    satellite_id = Column(String(32), nullable=False)
    debris_id = Column(String(32), nullable=False)
    primary_object_name = Column(String(128), nullable=True)
    secondary_object_name = Column(String(128), nullable=True)
    tca = Column(DateTime(timezone=True), nullable=False)
    closest_approach_km = Column(Float, nullable=False)
    relative_velocity_kms = Column(Float, nullable=False)
    screening_threshold_km = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now)
    risk_score = Column(Float, nullable=True)
    risk_level = Column(String(32), nullable=True)


class HistoricalTLEDB(Base):
    __tablename__ = "historical_tle"

    id = Column(String(64), primary_key=True, default=generate_uuid)
    object_id = Column(String(64), nullable=False)
    catalog_id = Column(String(32), nullable=False)
    epoch = Column(DateTime(timezone=True), nullable=False)
    raw_tle = Column(Text, nullable=False)
    source = Column(String(64), default="CelesTrak")
    timestamp = Column(DateTime(timezone=True), default=get_utc_now)
