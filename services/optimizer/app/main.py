import os
import httpx
from fastapi import FastAPI
from shared.schemas.maneuver import ManeuverCandidates
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo

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
                    # Normalize non-standard unicode characters (e.g. narrow non-breaking spaces)
                    cleaned = content.replace('\u202f', ' ').replace('\u00a0', ' ').replace('\u2019', "'").replace('\u2018', "'")
                    return cleaned
        except Exception as e:
            continue

    return fallback_reason


@app.post("/optimize-decision", response_model=ManeuverDecision)
def optimize_decision(candidates_payload: ManeuverCandidates):
    """
    Selects optimal candidate and outputs ManeuverDecision contract.
    """
    if not candidates_payload.candidates:
        return ManeuverDecision(
            conjunction_id=candidates_payload.conjunction_id,
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
    viable_candidates = [c for c in candidates_payload.candidates if c.resulting_risk == "LOW"]
    
    # If no LOW risk, fallback to MEDIUM
    if not viable_candidates:
        viable_candidates = [c for c in candidates_payload.candidates if c.resulting_risk == "MEDIUM"]
    
    # If still none, pick the one with max separation to maximize safety
    if not viable_candidates:
        optimal_candidate = max(candidates_payload.candidates, key=lambda c: c.new_separation_km)
    else:
        # Select the one with minimum delta-V
        optimal_candidate = min(viable_candidates, key=lambda c: c.delta_v_m_s)
        
    reason_str = generate_reasoning(optimal_candidate, candidates_payload.primary_object)
    
    return ManeuverDecision(
        conjunction_id=candidates_payload.conjunction_id,
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
