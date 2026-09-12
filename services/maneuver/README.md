# 🚀 Maneuver Agent (Agent 4)

**Role**: Simulated Avoidance Maneuver Candidate Generator  
**Contract Schema**: `shared.schemas.maneuver.ManeuverCandidates`  
**Input Data**: `shared.schemas.risk.RiskAssessment` from Risk Agent

## Quickstart for Teammate
1. Import contract schemas from `shared.schemas`.
2. Consume `RiskAssessment` payload.
3. Simulate candidate impulse burns ($\Delta v$ in $\text{m/s}$) and post-maneuver separation distance in $\text{km}$.
4. Output payload matching `ManeuverCandidates`.
