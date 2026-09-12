from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Body
import httpx

from shared.schemas.conjunction import ConjunctionCandidate
from shared.schemas.risk import RiskAssessment
from services.risk.app.scoring import assess_conjunction_risk
from services.risk.app.database import (
    get_conjunction_from_db,
    save_risk_assessment_to_db,
    load_fixture_conjunctions
)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="OrbitalGuard Risk Agent",
    version="1.0.0",
    description="Deterministic Conjunction Hazard Scoring and Risk Classification Service."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """Root landing endpoint with service info and docs link."""
    return {
        "service": "OrbitalGuard Risk Agent",
        "status": "ONLINE",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json",
        "endpoints": {
            "health": "GET /health",
            "assess_risk": "POST /assess-risk",
            "batch_assess": "POST /batch-assess"
        }
    }


@app.get("/health")
def health_check():
    """Health status and metadata for Risk Agent."""
    return {
        "status": "HEALTHY",
        "service": "OrbitalGuard Risk Agent",
        "scoring_model": "DETERMINISTIC_MULTI_FACTOR_HAZARD_INDEX",
        "version": "1.0.0"
    }


@app.post("/assess-risk", response_model=RiskAssessment)
async def assess_risk(
    candidate: Optional[ConjunctionCandidate] = Body(None, description="Direct ConjunctionCandidate payload"),
    conjunction_id: Optional[str] = Query(None, description="ID of conjunction to retrieve and score")
):
    """
    Assess hazard risk for a ConjunctionCandidate.
    Accepts direct ConjunctionCandidate payload, or queries by conjunction_id via DB / Tracking Service / Fixture.
    """
    target_candidate: Optional[ConjunctionCandidate] = candidate

    if target_candidate is None:
        if not conjunction_id:
            raise HTTPException(
                status_code=400,
                detail="Either a ConjunctionCandidate JSON body or a 'conjunction_id' query parameter must be provided."
            )

        # 1. Try local database
        target_candidate = get_conjunction_from_db(conjunction_id)

        # 2. Try fetching from Tracking & Screening Service API if running
        if target_candidate is None:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get("http://localhost:8000/api/v1/conjunctions")
                    if resp.status_code == 200:
                        candidates_data = resp.json()
                        for c in candidates_data:
                            if c.get("conjunction_id") == conjunction_id:
                                target_candidate = ConjunctionCandidate.model_validate(c)
                                break
            except Exception:
                pass

        # 3. Fallback to fixture data
        if target_candidate is None:
            fixtures = load_fixture_conjunctions()
            for c in fixtures:
                if c.conjunction_id == conjunction_id:
                    target_candidate = c
                    break

        if target_candidate is None:
            raise HTTPException(
                status_code=404,
                detail=f"ConjunctionCandidate with ID '{conjunction_id}' not found in DB, Tracking API, or fixtures."
            )

    # Compute deterministic risk assessment
    assessment = assess_conjunction_risk(target_candidate)

    # Save to database if available
    save_risk_assessment_to_db(assessment)

    return assessment


@app.post("/batch-assess", response_model=List[RiskAssessment])
async def batch_assess(
    candidates: Optional[List[ConjunctionCandidate]] = Body(None)
):
    """
    Batch assess multiple ConjunctionCandidate objects.
    If no body is provided, loads all available candidates from fixtures.
    """
    if candidates is None:
        candidates = load_fixture_conjunctions()

    results = []
    for c in candidates:
        assessment = assess_conjunction_risk(c)
        save_risk_assessment_to_db(assessment)
        results.append(assessment)

    return results
