# 🛰️ OrbitalGuard — Multi-Agent Integration Contract

**System Owner**: Agent 1 & 2 (Tracking + Screening)  
**Version**: `v1.0.0` (Hackathon Platform Standard)  
**Base URL**: `http://localhost:8000/api/v1`  
**Shared Schemas Package**: `shared.schemas`

---

## 1. Executive Summary & Agent Architecture

The Tracking & Screening Service provides the canonical orbital data foundation and contract interfaces for the entire OrbitalGuard multi-agent pipeline. Downstream agents (Risk, Maneuver, Optimizer) and the Frontend consume versioned contracts and endpoints without depending on internal SGP4 or CelesTrak implementation details.

```
                    ORBITALGUARD MULTI-AGENT PIPELINE
                                    │
                         ┌──────────▼──────────┐
                         │ TRACKING + SCREENING│ (Agent 1 & 2 - Platform)
                         └──────────┬──────────┘
                                    │
                             ConjunctionCandidate
                                    │
                         ┌──────────▼──────────┐
                         │     RISK AGENT      │ (Agent 3 - Teammate)
                         └──────────┬──────────┘
                                    │
                              RiskAssessment
                                    │
                         ┌──────────▼──────────┐
                         │   MANEUVER AGENT    │ (Agent 4 - Teammate)
                         └──────────┬──────────┘
                                    │
                            ManeuverCandidates
                                    │
                         ┌──────────▼──────────┐
                         │  OPTIMIZER/DECISION │ (Agent 5 - Teammate)
                         └──────────┬──────────┘
                                    │
                             ManeuverDecision
                                    │
                         ┌──────────▼──────────┐
                         │      FRONTEND       │ (Teammate)
                         └─────────────────────┘
```

---

## 2. Global Standards & Conventions

| Standard Dimension | Convention / Unit | Description |
| :--- | :--- | :--- |
| **Position Units** | $\text{km}$ | Cartesian $(x, y, z)$ coordinates in kilometers |
| **Velocity Units** | $\text{km/s}$ | Cartesian $(v_x, v_y, v_z)$ velocity in km per second |
| **Altitude Units** | $\text{km}$ | Geodetic altitude above WGS84 Earth ellipsoid |
| **Separation Distance** | $\text{km}$ | Euclidean minimum distance between space objects |
| **Delta-V ($\Delta v$)** | $\text{m/s}$ | Burn impulse magnitude in meters per second |
| **Timestamps** | `ISO-8601 UTC` | Example: `2026-09-12T12:45:00Z` |
| **Reference Frame** | `TEME` | True Equator Mean Equinox (Standard for SGP4) |
| **Object Categories** | `SATELLITE`, `DEBRIS`, `ROCKET_BODY`, `SYNTHETIC_DEBRIS` | Object classification enum |
| **Synthetic Debris ID** | `SYNTHETIC-99999` | Reserved ID format for synthetic demo objects |

---

## 3. Shared Pydantic Contracts (`shared.schemas`)

All team members can import schemas directly in Python:

```python
from shared.schemas import (
    OrbitalObject, StateVector, Trajectory, ConjunctionCandidate,
    RiskAssessment, ManeuverCandidates, ManeuverDecision
)
```

### 3.1 `OrbitalObject` (Tracking $\rightarrow$ All Agents & Frontend)
```json
{
  "object_id": "25544",
  "catalog_id": "25544",
  "name": "ISS (ZARYA)",
  "object_type": "SATELLITE",
  "international_designator": "1998-067A",
  "orbital_data": {
    "epoch": "2026-09-12T08:15:30Z",
    "source": "CelesTrak",
    "format": "TLE",
    "raw_tle_line1": "1 25544U 98067A   26255.34409722  .00016717  00000+0  30154-3 0  9993",
    "raw_tle_line2": "2 25544  51.6416 230.1254 0006241 120.4512 245.6721 15.49812345421508"
  },
  "state": {
    "timestamp": "2026-09-12T11:00:00Z",
    "position_km": { "x": -2415.4, "y": 4812.1, "z": 4012.8 },
    "velocity_km_s": { "x": -5.12, "y": -4.21, "z": 2.14 },
    "altitude_km": 418.5,
    "reference_frame": "TEME"
  },
  "propagation": {
    "model": "SGP4",
    "reference_frame": "TEME"
  },
  "data_quality": {
    "data_age_hours": 2.74,
    "quality": "HIGH"
  }
}
```

### 3.2 `ConjunctionCandidate` (Screening $\rightarrow$ Risk Agent & Frontend)
```json
{
  "conjunction_id": "CONJ-25544-SYNTHETIC-99999-202609121145",
  "primary_object": "25544",
  "secondary_object": "SYNTHETIC-99999",
  "primary_object_name": "ISS (ZARYA)",
  "secondary_object_name": "DEB-DEMO (SYNTHETIC_DEBRIS)",
  "tca": "2026-09-12T11:45:00Z",
  "closest_approach": {
    "distance_km": 7.68,
    "relative_velocity_km_s": 10.42
  },
  "screening": {
    "threshold_km": 50.0,
    "method": "SGP4_TWO_STAGE_FINE_REFINED_SCAN"
  },
  "data_provenance": {
    "primary_source": "CelesTrak",
    "propagator": "SGP4"
  },
  "created_at": "2026-09-12T11:00:24Z"
}
```

### 3.3 `RiskAssessment` (Risk Agent $\rightarrow$ Maneuver Agent & Frontend)
```json
{
  "conjunction_id": "CONJ-25544-SYNTHETIC-99999-202609121145",
  "risk_score": 87.5,
  "risk_level": "HIGH",
  "factors": {
    "closest_approach_km": 7.68,
    "time_to_tca_minutes": 44.6,
    "relative_velocity_km_s": 10.42
  },
  "uncertainty": {
    "model": "PROTOTYPE_FIXED_UNCERTAINTY",
    "confidence": "MODERATE"
  },
  "notes": "High relative velocity close approach candidate detected within 45 minutes of TCA."
}
```

### 3.4 `ManeuverCandidates` (Maneuver Agent $\rightarrow$ Optimizer Agent & Frontend)
```json
{
  "conjunction_id": "CONJ-25544-SYNTHETIC-99999-202609121145",
  "primary_object": "25544",
  "candidates": [
    {
      "maneuver_id": "M1",
      "delta_v_m_s": 0.85,
      "burn_direction": "POSIGRADE",
      "new_separation_km": 24.5,
      "resulting_risk": "MEDIUM"
    },
    {
      "maneuver_id": "M2",
      "delta_v_m_s": 1.42,
      "burn_direction": "RETROGRADE",
      "new_separation_km": 54.2,
      "resulting_risk": "LOW"
    }
  ]
}
```

### 3.5 `ManeuverDecision` (Optimizer Agent $\rightarrow$ Frontend)
```json
{
  "conjunction_id": "CONJ-25544-SYNTHETIC-99999-202609121145",
  "decision": {
    "recommended_maneuver_id": "M2",
    "reason": "M2 retrograde burn provides 54.2 km separation, reducing risk to LOW with minimal fuel expenditure."
  },
  "human_approval_required": true,
  "simulation": {
    "status": "PENDING",
    "new_tca_distance_km": 54.2
  }
}
```

---

## 4. API Endpoint Reference (`/api/v1`)

| Method | Endpoint | Description | Query Parameters |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Health status & DB check | - |
| `POST` | `/api/v1/ingest` | Ingest TLEs (CelesTrak / Cache fallback) | `group` (default: `active`) |
| `GET` | `/api/v1/objects` | List all tracked objects | `object_type` (`SATELLITE`, `DEBRIS`) |
| `GET` | `/api/v1/objects/{id}` | Object details & current Cartesian state | - |
| `GET` | `/api/v1/objects/{id}/trajectory` | 90-min future trajectory array | `horizon` (default `90`), `step` (default `1.0`) |
| `POST` | `/api/v1/screen` | Execute 2-stage screening engine | `horizon` (default `90`), `threshold_km` (`50.0`) |
| `GET` | `/api/v1/conjunctions` | List flagged conjunction candidates | - |
| `POST` | `/api/v1/demo/inject-synthetic` | Inject synthetic debris & register conjunction | `target_catalog_id` (default `25544`) |

---

## 5. Offline Mock Fixtures Location

For offline development without running the full backend, mock JSON files are available in `services/propagation/data/`:
- `services/propagation/data/sample_objects.json`
- `services/propagation/data/sample_conjunctions.json`
- `services/propagation/data/sample_risk.json`
- `services/propagation/data/sample_maneuvers.json`
- `services/propagation/data/sample_decisions.json`
- `services/propagation/data/celestrak_cache.json`

---

## 6. Integration Instructions for Teammates

### For Risk Agent Teammate:
1. Call `GET http://localhost:8000/api/v1/conjunctions` (or load `sample_conjunctions.json`).
2. Read `closest_approach.distance_km`, calculate `time_to_tca_minutes = (tca - now).total_seconds() / 60.0`.
3. Compute `risk_score` (0-100) and `risk_level` (`HIGH`, `MEDIUM`, `LOW`).
4. Output payload matching `RiskAssessment` schema.

### For Maneuver Agent Teammate:
1. Receive `RiskAssessment` payload from Risk Agent.
2. If `risk_level` is `HIGH` or `CRITICAL`, evaluate impulse burns ($\Delta v$ in $\text{m/s}$).
3. Calculate simulated post-maneuver separation distance in $\text{km}$.
4. Output payload matching `ManeuverCandidates` schema.

### For Optimizer Agent Teammate:
1. Receive `ManeuverCandidates` payload from Maneuver Agent.
2. Select optimal candidate minimizing $\Delta v$ while achieving `resulting_risk == "LOW"`.
3. Output payload matching `ManeuverDecision` schema.

### For Frontend Teammate:
1. Query `GET /api/v1/objects` to render tracked satellites and debris on 3D globe / map.
2. Query `GET /api/v1/objects/{id}/trajectory` to display orbit trajectory path polyline.
3. Query `GET /api/v1/conjunctions` to display close approach alerts.
4. Call `POST /api/v1/demo/inject-synthetic` during hackathon presentation to trigger a deterministic conjunction event.
