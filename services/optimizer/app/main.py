import os
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, Body, Query
from shared.schemas.maneuver import ManeuverCandidates
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo
from services.maneuver.app.main import get_cached_maneuvers, generate_maneuvers

app = FastAPI(
    title="OrbitalGuard Optimizer Agent",
    version="1.0.0",
    description="Selects optimal maneuver trade-off minimizing delta-V while mitigating conjunction risk."
)


@app.get("/health")
def health_check():
    return {"status": "HEALTHY", "service": "Optimizer Agent"}


def generate_reasoning(optimal_candidate, primary_object: str) -> str:
    """Uses Groq LLM to generate a human-readable justification for the decision."""
    groq_api_key = os.getenv("GROQ_API_KEY")
    fallback_reason = f"{optimal_candidate.maneuver_id} {optimal_candidate.burn_direction.lower()} burn provides {optimal_candidate.new_separation_km} km separation, reducing risk to {optimal_candidate.resulting_risk} with minimal fuel expenditure ({optimal_candidate.delta_v_m_s} m/s)."
    
    if not groq_api_key or "gsk_" not in groq_api_key:
        return fallback_reason
        
    configured_model = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
    candidate_models = [configured_model, "groq/compound-mini", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
    
    prompt = f"""
    You are an AI decision agent for OrbitalGuard. Your job is to summarize why a specific avoidance maneuver was chosen for satellite {primary_object}.
    
    Chosen Maneuver: {optimal_candidate.maneuver_id}
    - Delta-V: {optimal_candidate.delta_v_m_s} m/s
    - Direction: {optimal_candidate.burn_direction}
    - Resulting Separation: {optimal_candidate.new_separation_km} km
    - Resulting Risk: {optimal_candidate.resulting_risk}
    
    Write a clear, professional, one-sentence justification explaining that this maneuver provides safe separation distance while minimizing fuel consumption.
    """
    
    headers = {
        "Authorization": f"Bearer {groq_api_key}",
        "Content-Type": "application/json"
    }
    
    # Try models in order until one succeeds
    for model in dict.fromkeys(candidate_models):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a concise aerospace decision agent."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 100
        }
        try:
            response = httpx.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                if content:
                    # Normalize non-standard unicode characters (e.g. narrow non-breaking spaces, non-breaking hyphens)
                    cleaned = (
                        content.replace('\u202f', ' ')
                        .replace('\u00a0', ' ')
                        .replace('\u2019', "'")
                        .replace('\u2018', "'")
                        .replace('\u2011', '-')
                        .replace('\u2013', '-')
                        .replace('\u2014', '-')
                        .replace('\u0394', 'Delta-')
                    )
                    return cleaned
        except Exception as e:
            continue

    return fallback_reason


@app.post("/optimize-decision", response_model=ManeuverDecision)
def optimize_decision(
    candidates_payload: Optional[ManeuverCandidates] = Body(None, description="Direct ManeuverCandidates payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction candidate to optimize")
):
    """
    Selects optimal candidate and outputs ManeuverDecision contract.
    Accepts direct ManeuverCandidates body, or resolves candidates by conjunction_id.
    """
    target_payload = candidates_payload

    if target_payload is None:
        if not conjunction_id:
            raise HTTPException(
                status_code=400,
                detail="Either a ManeuverCandidates JSON body or a 'conjunction_id' query parameter must be provided."
            )
        
        target_payload = get_cached_maneuvers(conjunction_id)
        if target_payload is None:
            try:
                target_payload = generate_maneuvers(conjunction_id=conjunction_id)
            except Exception as e:
                raise HTTPException(
                    status_code=404,
                    detail=f"Maneuver candidates for conjunction '{conjunction_id}' not found: {e}"
                )

    if not target_payload.candidates:
        return ManeuverDecision(
            conjunction_id=target_payload.conjunction_id,
            decision=DecisionInfo(
                recommended_maneuver_id="NONE",
                reason="No maneuver required. Conjunction risk is already within acceptable limits."
            ),
            human_approval_required=False,
            simulation=SimulationInfo(
                status="EXECUTED",
                new_tca_distance_km=None
            )
        )
    
    # Filter for LOW risk candidates
    viable_candidates = [c for c in target_payload.candidates if c.resulting_risk == "LOW"]
    
    # If no LOW risk, fallback to MEDIUM
    if not viable_candidates:
        viable_candidates = [c for c in target_payload.candidates if c.resulting_risk == "MEDIUM"]
    
    # If still none, pick the one with max separation to maximize safety
    if not viable_candidates:
        optimal_candidate = max(target_payload.candidates, key=lambda c: c.new_separation_km)
    else:
        # Select the one with minimum delta-V
        optimal_candidate = min(viable_candidates, key=lambda c: c.delta_v_m_s)
        
    reason_str = generate_reasoning(optimal_candidate, target_payload.primary_object)
    
    # Save maneuver decision to database (Supabase/PostgreSQL)
    save_maneuver_decision_to_db(
        conjunction_id=target_payload.conjunction_id,
        optimal_candidate=optimal_candidate,
        reason=reason_str,
        approval_status="PENDING"
    )

    return ManeuverDecision(
        conjunction_id=target_payload.conjunction_id,
        decision=DecisionInfo(
            recommended_maneuver_id=optimal_candidate.maneuver_id,
            reason=reason_str
        ),
        human_approval_required=True,
        simulation=SimulationInfo(
            status="PENDING",
            new_tca_distance_km=optimal_candidate.new_separation_km
        )
    )


def save_maneuver_decision_to_db(conjunction_id: str, optimal_candidate, reason: str, approval_status: str = "PENDING") -> bool:
    """Persists the optimized maneuver decision and Groq reasoning to PostgreSQL."""
    try:
        from services.propagation.app.database.repository import SessionLocal
        from services.propagation.app.database.models import ManeuverDecisionDB

        session = SessionLocal()
        try:
            existing = session.query(ManeuverDecisionDB).filter(ManeuverDecisionDB.conjunction_id == conjunction_id).first()
            if existing:
                existing.recommended_maneuver_id = optimal_candidate.maneuver_id if optimal_candidate else "NONE"
                existing.new_tca_distance_km = optimal_candidate.new_separation_km if optimal_candidate else 0.0
                existing.decision_reason = reason
                existing.simulation_status = approval_status
            else:
                record = ManeuverDecisionDB(
                    conjunction_id=conjunction_id,
                    recommended_maneuver_id=optimal_candidate.maneuver_id if optimal_candidate else "NONE",
                    new_tca_distance_km=optimal_candidate.new_separation_km if optimal_candidate else 0.0,
                    decision_reason=reason,
                    simulation_status=approval_status
                )
                session.add(record)
            session.commit()
            return True
        finally:
            session.close()
    except Exception as e:
        print(f"Notice: Failed to persist maneuver decision to DB: {e}")
    return False
