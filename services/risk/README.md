# 🛡️ Risk Agent (Agent 3)

**Role**: Conjunction Risk Assessment & Hazard Scoring  
**Contract Schema**: `shared.schemas.risk.RiskAssessment`  
**Input Data**: `shared.schemas.conjunction.ConjunctionCandidate` from Tracking/Screening Service

## Quickstart for Teammate
1. Import contract schemas from `shared.schemas`.
2. Fetch close approach candidates from `GET http://localhost:8000/api/v1/conjunctions` or mock fixture `services/propagation/data/sample_conjunctions.json`.
3. Implement your risk assessment logic and return `RiskAssessment`.
