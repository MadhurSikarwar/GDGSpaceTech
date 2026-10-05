import logging
import math
from datetime import datetime, timezone
from typing import List, Optional, Dict
import numpy as np
from fastapi import FastAPI, HTTPException, Body, Query
from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.maneuver import ManeuverCandidates, ManeuverCandidate
from shared.schemas.risk import RiskAssessment
from shared.schemas.state import Vector3
from services.risk.app.database import get_conjunction_from_db, load_fixture_conjunctions
from services.risk.app.scoring import assess_conjunction_risk, classify_risk_level, PC_TIER_HIGH

from fastapi.middleware.cors import CORSMiddleware
import httpx

logger = logging.getLogger(__name__)

app = FastAPI(
    title="OrbitalGuard Maneuver Agent",
    version="1.0.0",
    description="Generates simulated avoidance maneuver options (posigrade, retrograde, normal)."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global in-memory cache for maneuver candidates by conjunction_id
_maneuver_cache: Dict[str, ManeuverCandidates] = {}


def get_cached_maneuvers(conjunction_id: str) -> Optional[ManeuverCandidates]:
    return _maneuver_cache.get(conjunction_id)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Maneuver Agent"}

def save_maneuver_candidates_to_db(candidates: ManeuverCandidates) -> bool:
    try:
        from services.propagation.app.database.repository import SessionLocal
        from services.propagation.app.database.models import ManeuverCandidateDB

        session = SessionLocal()
        try:
            for c in candidates.candidates:
                db_id = f"{candidates.conjunction_id}_{c.maneuver_id}"
                existing = session.query(ManeuverCandidateDB).filter(
                    ManeuverCandidateDB.maneuver_id == db_id
                ).first()
                if not existing:
                    record = ManeuverCandidateDB(
                        maneuver_id=db_id,
                        conjunction_id=candidates.conjunction_id,
                        burn_direction=c.burn_direction,
                        delta_v_m_s=c.delta_v_m_s,
                        new_separation_km=c.new_separation_km,
                        resulting_risk=c.resulting_risk
                    )
                    session.add(record)
            session.commit()
            return True
        finally:
            session.close()
    except Exception as e:
        print(f"Notice: Failed to persist maneuver candidates to DB: {e}")
    return False


def _generate_heuristic_candidates(closest_approach_km: float, time_to_tca_minutes: float) -> List[ManeuverCandidate]:
    """
    Documented fallback for when full orbital data isn't available: the
    direct-RiskAssessment-body API contract carries only scalar hazard
    factors (distance/time/velocity), never TLEs or absolute state -- there
    is no orbital state to run a real physics optimizer against no matter
    how it's implemented. Real, TLE/TCA-driven delta-v planning happens in
    _generate_optimizer_candidates below (the path the frontend always
    uses, via conjunction_id); this exists only for that reduced-information
    case, the same live-optimizer-when-possible/honest-fallback-otherwise
    split already used throughout this codebase's frontend (api.js).
    """
    dv_options = [0.2, 0.5, 1.0, 2.0, 5.0]
    directions = ["POSIGRADE", "RETROGRADE"]
    out: List[ManeuverCandidate] = []
    idx = 1
    for direction in directions:
        for dv in dv_options:
            # Simplified along-track displacement approximation (not the
            # real CW solution -- see module docstring on why a real one
            # isn't possible here at all).
            displacement_km = 0.18 * dv * time_to_tca_minutes
            new_separation = math.sqrt(closest_approach_km ** 2 + displacement_km ** 2)
            resulting_risk = "LOW" if new_separation > 50.0 else "MEDIUM" if new_separation > 10.0 else "HIGH"
            out.append(ManeuverCandidate(
                maneuver_id=f"M{idx}", delta_v_m_s=dv, burn_direction=direction,
                new_separation_km=round(new_separation, 2), resulting_risk=resulting_risk,
            ))
            idx += 1
    return out


def _generate_optimizer_candidates(candidate: ConjunctionCandidate) -> Optional[List[ManeuverCandidate]]:
    """
    Real scipy.optimize.minimize (SLSQP) delta-v planning against the actual
    CW-propagated, Pc-constrained physics -- see delta_v_optimizer.py.
    Re-derives both objects' state at TCA fresh via SGP4 from their TLEs
    (the same inputs the screening pipeline used, just re-propagated rather
    than reading back whatever it stored) rather than depending on
    ConjunctionCandidate.closest_approach's vector fields, so it works for
    any conjunction whose primary/secondary are still resolvable in the
    catalog -- including ones flagged before Pc support existed. Returns
    None (caller falls back to the heuristic) only when that resolution
    itself fails, e.g. an object no longer in the catalog, or SLSQP fails
    to converge at all.
    """
    from services.propagation.app.database.repository import DatabaseRepository, SessionLocal as PropSessionLocal
    from services.propagation.app.spaceweather.noaa_client import get_space_weather
    from services.maneuver.app.delta_v_optimizer import (
        build_optimizer_inputs, solve_minimum_delta_v, classify_constraint_status,
        burn_direction_label, predicted_pc_for_delta_v, shifted_miss_distance_km,
        PC_CRITICAL_THRESHOLD, MAX_SMA_DRIFT_KM,
    )

    session = PropSessionLocal()
    try:
        repo = DatabaseRepository(session)
        primary_db = repo.get_object_by_catalog_id(candidate.primary_object)
        secondary_db = repo.get_object_by_catalog_id(candidate.secondary_object)
        if not primary_db or not secondary_db:
            return None

        try:
            drag_scalar = get_space_weather().drag_activity_scalar
        except Exception:
            drag_scalar = 1.0

        inputs = build_optimizer_inputs(
            primary_tle1=primary_db.raw_tle_line1, primary_tle2=primary_db.raw_tle_line2, primary_name=primary_db.name,
            primary_epoch=primary_db.epoch, primary_type=primary_db.object_type,
            secondary_tle1=secondary_db.raw_tle_line1, secondary_tle2=secondary_db.raw_tle_line2,
            secondary_name=secondary_db.name, secondary_epoch=secondary_db.epoch, secondary_type=secondary_db.object_type,
            tca_dt=candidate.tca,
            combined_hbr_km=(candidate.combined_hard_body_radius_m or 20.0) / 1000.0,
            drag_activity_scalar=drag_scalar,
        )
    finally:
        session.close()

    now = datetime.now(timezone.utc)
    tca = candidate.tca if candidate.tca.tzinfo else candidate.tca.replace(tzinfo=timezone.utc)
    time_to_tca_s = max(1.0, (tca - now).total_seconds())

    result = solve_minimum_delta_v(inputs, time_to_tca_s)
    if not result.success:
        return None  # caller falls back to the heuristic rather than surfacing an empty/broken plan

    status = classify_constraint_status(result, inputs, time_to_tca_s, PC_CRITICAL_THRESHOLD, MAX_SMA_DRIFT_KM)

    out: List[ManeuverCandidate] = []
    for i, scale in enumerate((1.0, 1.5, 2.0), start=1):
        dv_ric = result.x * scale
        pc = predicted_pc_for_delta_v(dv_ric, inputs, time_to_tca_s)
        new_separation_km = shifted_miss_distance_km(dv_ric, inputs, time_to_tca_s)
        risk_tier = classify_risk_level(risk_score=0.0, distance_km=new_separation_km, time_to_tca_minutes=time_to_tca_s / 60.0, probability_of_collision=pc)
        dv_m_s = dv_ric * 1000.0
        out.append(ManeuverCandidate(
            maneuver_id=f"OPT{i}",
            delta_v_m_s=round(float(np.linalg.norm(dv_ric)) * 1000.0, 3),
            burn_direction=burn_direction_label(dv_ric),
            new_separation_km=round(new_separation_km, 3),
            resulting_risk=risk_tier,
            delta_v_vector=Vector3(x=round(float(dv_m_s[0]), 4), y=round(float(dv_m_s[1]), 4), z=round(float(dv_m_s[2]), 4)),
            predicted_pc=pc,
            is_optimizer_minimum=(i == 1),
            constraint_status=status,
        ))
    return out


@app.post("/generate-maneuvers", response_model=ManeuverCandidates)
def generate_maneuvers(
    risk_assessment: Optional[RiskAssessment] = Body(None, description="Direct RiskAssessment payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction candidate"),
    satellite_id: Optional[str] = Query("25544", description="Primary catalog ID")
):
    """
    Evaluates delta-V burns and outputs ManeuverCandidates contract.
    Accepts direct RiskAssessment body, or fetches and scores by conjunction_id.
    """
    target_assessment = risk_assessment
    target_satellite_id = satellite_id or "25544"
    candidate: Optional[ConjunctionCandidate] = None

    if target_assessment is None:
        if not conjunction_id:
            raise HTTPException(
                status_code=400,
                detail="Either a RiskAssessment JSON body or a 'conjunction_id' query parameter must be provided."
            )

        # 1. Fetch conjunction candidate from DB, Tracking API, or fixtures
        candidate = get_conjunction_from_db(conjunction_id)
        if candidate is None:
            try:
                with httpx.Client(timeout=3.0) as client:
                    resp = client.get("http://localhost:8000/api/v1/conjunctions")
                    if resp.status_code == 200:
                        for c in resp.json():
                            if c.get("conjunction_id") == conjunction_id:
                                candidate = ConjunctionCandidate.model_validate(c)
                                break
            except Exception:
                pass

        if candidate is None:
            fixtures = load_fixture_conjunctions()
            for f in fixtures:
                if f.conjunction_id == conjunction_id:
                    candidate = f
                    break

        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail=f"Conjunction with ID '{conjunction_id}' not found."
            )

        target_satellite_id = candidate.primary_object
        target_assessment = assess_conjunction_risk(candidate)

    # Gate maneuver planning strictly based on the resolved risk tier.
    # This guarantees that if the Risk Agent determines the encounter is HIGH or CRITICAL 
    # (whether driven by Pc or heuristics), the Maneuver Agent will consistently
    # generate avoidance options, preventing any UX contradiction on screen.
    needs_maneuver = target_assessment.risk_level in ["HIGH", "CRITICAL"]
    if not needs_maneuver:
        result = ManeuverCandidates(
            conjunction_id=target_assessment.conjunction_id,
            primary_object=target_satellite_id,
            candidates=[]
        )
        _maneuver_cache[target_assessment.conjunction_id] = result
        return result

    candidates: Optional[List[ManeuverCandidate]] = None
    if candidate is not None:
        try:
            candidates = _generate_optimizer_candidates(candidate)
        except Exception as exc:
            logger.warning(f"Delta-V optimizer failed for {candidate.conjunction_id}, falling back to heuristic: {exc}")
            candidates = None

    if candidates is None:
        candidates = _generate_heuristic_candidates(
            target_assessment.factors.closest_approach_km,
            target_assessment.factors.time_to_tca_minutes,
        )

    result = ManeuverCandidates(
        conjunction_id=target_assessment.conjunction_id,
        primary_object=target_satellite_id,
        candidates=candidates
    )
    save_maneuver_candidates_to_db(result)
    _maneuver_cache[target_assessment.conjunction_id] = result
    return result
