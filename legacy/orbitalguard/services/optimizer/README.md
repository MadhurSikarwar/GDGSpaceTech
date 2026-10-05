# ⚡ Optimizer / Decision Agent (Agent 5)

**Role**: Maneuver Decision Optimization & Trade-off Recommendation  
**Contract Schema**: `shared.schemas.decision.ManeuverDecision`  
**Input Data**: `shared.schemas.maneuver.ManeuverCandidates` from Maneuver Agent

## Quickstart for Teammate
1. Import contract schemas from `shared.schemas`.
2. Consume `ManeuverCandidates` payload.
3. Select optimal maneuver candidate balancing fuel expenditure ($\Delta v$) and collision risk mitigation.
4. Output payload matching `ManeuverDecision`.
