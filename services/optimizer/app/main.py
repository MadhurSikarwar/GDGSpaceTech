"""
OrbitalGuard Decision Agent Service (formerly Optimizer Agent).
Selects optimal avoidance maneuvers through intelligent orchestration of Phase 1 deterministic tools.
"""

import os
from typing import Optional, Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware

from shared.schemas.maneuver import ManeuverCandidates
from shared.schemas.decision import ManeuverDecision, DecisionInfo, SimulationInfo
from services.maneuver.app.main import get_cached_maneuvers, generate_maneuvers
from services.optimizer.app.agent import (
    DecisionAgentOrchestrator,
    DecisionContext,
    ToolRegistry,
)

app = FastAPI(
    title="OrbitalGuard Decision Agent",
    version="2.0.0",
    description="Agentic orchestrator evaluating conjunction risk, selecting Phase 1 tools, and producing safe, constraint-verified avoidance recommendations."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global orchestrator instance
_orchestrator = DecisionAgentOrchestrator()


@app.get("/health")
def health_check():
    return {
        "status": "HEALTHY",
        "service": "Decision Agent (Optimizer)",
        "version": "2.0.0",
        "architecture": "AGENTIC_TOOL_ORCHESTRATOR",
        "tools_available": len(_orchestrator.registry.get_definitions()),
    }


@app.get("/agent/tools")
def list_agent_tools():
    """List all Phase 1 deterministic tools available in the agent registry."""
    return {
        name: {
            "description": defn.description,
            "parameters": defn.parameters
        }
        for name, defn in _orchestrator.registry.get_definitions().items()
    }


@app.post("/agent/decide", response_model=DecisionContext)
def agent_decide(
    candidates_payload: Optional[ManeuverCandidates] = Body(None, description="Direct ManeuverCandidates payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction candidate to analyze"),
):
    """
    Execute full agentic decision orchestration and return complete DecisionContext trace.
    """
    if not conjunction_id and not candidates_payload:
        raise HTTPException(
            status_code=400,
            detail="Either a ManeuverCandidates JSON body or a 'conjunction_id' query parameter must be provided."
        )

    cid = conjunction_id or (candidates_payload.conjunction_id if candidates_payload else None)
    context = _orchestrator.run(
        conjunction_id=cid,
        preloaded_candidates=candidates_payload,
    )

    # Persist decision to database
    save_maneuver_decision_to_db(
        conjunction_id=context.conjunction_id,
        optimal_candidate=context.selected_candidate,
        reason=context.explanation or "Decision completed.",
        approval_status=context.approval_status,
    )

    return context


from pydantic import BaseModel

class FeedbackRequest(BaseModel):
    conjunction_id: str
    maneuver_id: Optional[str] = None
    status: str
    reason: Optional[str] = None

@app.post("/agent/feedback", response_model=DecisionContext)
def agent_feedback(request: FeedbackRequest):
    """
    Submit human approval or rejection for a maneuver candidate.
    If rejected, the agent will reconsider and generate a new recommendation.
    """
    from services.propagation.app.database.repository import DatabaseRepository, SessionLocal
    
    session = SessionLocal()
    try:
        repo = DatabaseRepository(session)
        repo.save_decision_feedback(
            conjunction_id=request.conjunction_id,
            maneuver_id=request.maneuver_id,
            status=request.status,
            reason=request.reason
        )
    finally:
        session.close()

    # Re-evaluate automatically on rejection
    if request.status.upper() == "REJECTED":
        context = _orchestrator.run(conjunction_id=request.conjunction_id)
        
        save_maneuver_decision_to_db(
            conjunction_id=context.conjunction_id,
            optimal_candidate=context.selected_candidate,
            reason=context.explanation or "Decision completed.",
            approval_status=context.approval_status,
        )
        return context
    
    # If approved, just fetch or return something dummy, or just run the orchestrator once to get the context
    # Usually returning the current state or updating the DB is enough.
    # To keep it simple, we just return the final state of the orchestrator.
    context = _orchestrator.run(conjunction_id=request.conjunction_id)
    return context


@app.post("/optimize-decision", response_model=ManeuverDecision)
def optimize_decision(
    candidates_payload: Optional[ManeuverCandidates] = Body(None, description="Direct ManeuverCandidates payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction candidate to optimize")
):
    """
    Selects optimal candidate and outputs standard ManeuverDecision contract.
    Orchestrated by the intelligent Decision Agent with strict physical constraint enforcement.
    """
    if not conjunction_id and not candidates_payload:
        raise HTTPException(
            status_code=400,
            detail="Either a ManeuverCandidates JSON body or a 'conjunction_id' query parameter must be provided."
        )

    cid = conjunction_id or (candidates_payload.conjunction_id if candidates_payload else None)

    # Run agentic orchestrator
    context = _orchestrator.run(
        conjunction_id=cid,
        preloaded_candidates=candidates_payload,
    )

    # Convert to standardized ManeuverDecision contract
    decision = _orchestrator.to_maneuver_decision(context)

    # Persist outcome to database
    save_maneuver_decision_to_db(
        conjunction_id=context.conjunction_id,
        optimal_candidate=context.selected_candidate,
        reason=decision.decision.reason,
        approval_status=decision.simulation.status,
    )

    return decision


def save_maneuver_decision_to_db(conjunction_id: str, optimal_candidate, reason: str, approval_status: str = "PENDING") -> bool:
    """Persists the optimized maneuver decision and agent reasoning to PostgreSQL."""
    try:
        from services.propagation.app.database.repository import SessionLocal
        from services.propagation.app.database.models import ManeuverDecisionDB

        session = SessionLocal()
        try:
            existing = session.query(ManeuverDecisionDB).filter(ManeuverDecisionDB.conjunction_id == conjunction_id).first()
            rec_id = optimal_candidate.maneuver_id if optimal_candidate else "NONE"
            new_dist = optimal_candidate.new_separation_km if optimal_candidate else 0.0

            if existing:
                existing.recommended_maneuver_id = rec_id
                existing.new_tca_distance_km = new_dist
                existing.decision_reason = reason
                existing.simulation_status = approval_status
            else:
                record = ManeuverDecisionDB(
                    conjunction_id=conjunction_id,
                    recommended_maneuver_id=rec_id,
                    new_tca_distance_km=new_dist,
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
