# 🚀 OrbitalGuard — Complete Multi-Agent Handoff & Technical Context

---

## 1. PROJECT / REPOSITORY

- **Project Name**: OrbitalGuard — Autonomous Orbital Traffic Intelligence System
- **Tagline**: TRACK. PREDICT. AVOID.
- **GitHub Repository**: [https://github.com/MadhurSikarwar/GDGSpaceTech](https://github.com/MadhurSikarwar/GDGSpaceTech)
- **Local Directory**: `GDG-SPACE-TECH/`
- **Active Git Branches**:
  - `main`: Shared team baseline containing shared schemas, multi-agent skeleton stubs, integration contracts, docker configuration, root documentation, and platform setup.
  - `tracking-screening`: Development branch containing the completed Tracking Agent + Screening Agent baseline service.

> [!IMPORTANT]
> **Branching Policy**: `main` serves as the shared foundation. Teammates must clone the repository, pull `main`, and create their dedicated feature branch (e.g. `git checkout -b risk-agent main`). Do not modify another teammate's service directory or branch without coordination.

---

## 2. PROBLEM STATEMENT & SCOPE

### Hackathon Challenge
> *"Space Tech & Orbital Sustainability: Build autonomous agents to track space debris, optimize satellite maneuver planning, or coordinate open-source orbital traffic management to protect global communication infrastructure."*

### How OrbitalGuard Solves This Problem
OrbitalGuard implements an autonomous multi-agent intelligence pipeline to track space objects, predict orbital trajectories, screen for potential close approaches, compute conjunction risk, simulate avoidance maneuvers, and recommend optimal low-$\Delta v$ collision-avoidance maneuvers with human-in-the-loop approval.

### Scope Boundaries
1. **Implemented (Platform Baseline)**:
   - CelesTrak TLE/OMM ingestion & offline cache fallback.
   - SGP4 orbital state propagation (TEME reference frame).
   - 90-minute future trajectory generation.
   - Two-stage screening (Altitude-band coarse filter + SGP4 fine distance scan with local TCA refinement).
   - Verified synthetic debris injection (`SYNTHETIC_DEBRIS`).
   - Shared Pydantic contract layer and FastAPI REST services.
2. **Teammate Assignment Scope**:
   - **Risk Agent**: Conjunction hazard scoring and risk level classification.
   - **Maneuver Agent**: Impulse burn ($\Delta v$) simulation and candidate generation.
   - **Optimizer Agent**: Multi-objective trade-off selection and decision recommendation.
   - **Frontend**: Interactive 3D/2D dashboard rendering orbital states, trajectories, and decision flows.
3. **Out of Scope / Future Extensions**:
   - Operational real-spacecraft hardware thruster control.
   - Covariance propagation & physical Probability of Collision ($P_c$) calculations.

---

## 3. SYSTEM PIPELINE ARCHITECTURE

```text
Public Orbital Data (CelesTrak / Cache)
        ↓
Tracking Agent (SGP4 Propagation in TEME Frame)
        ↓
Current State Vector + 90-Min Trajectory
        ↓
Screening Agent (2-Stage Coarse Altitude + Fine SGP4 Refined Scan)
        ↓
ConjunctionCandidate JSON
        ↓
Risk Agent (Risk Scoring & Assessment)
        ↓
RiskAssessment JSON
        ↓
Maneuver Agent (Simulated Avoidance Burns Δv)
        ↓
ManeuverCandidates JSON
        ↓
Optimizer / Decision Agent (Trade-off Decision & Recommendation)
        ↓
ManeuverDecision JSON
        ↓
Human Approval Gate & Frontend Dashboard
```

> [!NOTE]
> **Simulation & Decision Support System**: OrbitalGuard is a decision-support and simulation platform. It generates verified simulated maneuver recommendations for human operators and does NOT issue real operational commands to physical spacecraft hardware.

---

## 4. TEAM RESPONSIBILITIES

| Role / Agent | Owner | Directory Path | Responsibilities |
| :--- | :--- | :--- | :--- |
| **Tracking + Screening** | Baseline Developer | `services/propagation/` | Ingestion, SGP4 TEME propagation, 90-min trajectories, 2-stage screening, synthetic debris generator, REST API. |
| **Risk Agent** | Risk Teammate | `services/risk/` | Consumes `ConjunctionCandidate`, calculates hazard scores ($0-100$) and risk tiers (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), outputs `RiskAssessment`. |
| **Maneuver Agent** | Maneuver Teammate | `services/maneuver/` | Consumes `RiskAssessment`, simulates impulse burn vectors ($\Delta v$ in $\text{m/s}$), calculates post-maneuver separation distance ($\text{km}$), outputs `ManeuverCandidates`. |
| **Optimizer Agent** | Optimizer Teammate | `services/optimizer/` | Consumes `ManeuverCandidates`, selects optimal burn balancing minimum $\Delta v$ and risk mitigation, outputs `ManeuverDecision`. |
| **Frontend** | Frontend Teammate | `frontend/` | Renders 3D/2D orbital positions, trajectory polylines, close approach alerts, and interactive maneuver approval interface. |

---

## 5. CURRENT REPOSITORY STRUCTURE

```text
GDG-SPACE-TECH/
├── .env.example                     # Configuration template (ports, DB URL, threshold settings)
├── .gitignore                       # Excludes local databases (*.db), .env, and __pycache__
├── docker-compose.yml               # Multi-agent Docker container orchestration
├── HANDOFF_CONTEXT.md               # Complete multi-agent handoff & technical context document
├── INTEGRATION_CONTRACT.md          # Authoritative multi-agent integration contract specification
├── README.md                        # Master project documentation & quickstart guide
├── requirements.txt                 # Platform Python dependencies
├── shared/                          # 📦 Shared Contracts Package
│   └── schemas/                     # Pydantic schemas for all agent interfaces
│       ├── object.py                # Canonical OrbitalObject schema
│       ├── state.py                 # StateVector (position_km, velocity_km_s, altitude_km)
│       ├── trajectory.py            # Trajectory & TrajectoryPoint
│       ├── conjunction.py           # ConjunctionCandidate schema
│       ├── risk.py                  # RiskAssessment schema (for Risk Agent)
│       ├── maneuver.py              # ManeuverCandidates schema (for Maneuver Agent)
│       └── decision.py              # ManeuverDecision schema (for Optimizer Agent)
├── services/
│   ├── propagation/                 # 🛰️ Tracking + Screening Service (Agent 1 & 2 - Platform Baseline)
│   │   ├── Dockerfile
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   ├── app/
│   │   │   ├── main.py              # FastAPI app entry point (/api/v1 + unversioned aliases)
│   │   │   ├── config.py            # Settings & threshold configurations
│   │   │   ├── api/routes.py        # REST endpoints (/objects, /trajectory, /conjunctions, /demo)
│   │   │   ├── database/            # SQLAlchemy models (PostgreSQL / SQLite) & repository
│   │   │   ├── ingestion/           # CelesTrak HTTP client & 2-Line TLE / OMM JSON parser
│   │   │   ├── propagation/         # SGP4 engine (TEME) & 90-min trajectory generator
│   │   │   ├── screening/           # 2-Stage Coarse (Altitude) & Fine (SGP4) screening
│   │   │   └── synthetic/           # Verified synthetic debris generator (SYNTHETIC_DEBRIS)
│   │   ├── data/                    # Mock JSON fixtures & offline CelesTrak cache
│   │   └── tests/                   # 19 unit & integration tests
│   ├── risk/                        # 🛡️ Risk Agent (Agent 3 Teammate Stub)
│   │   ├── README.md
│   │   └── app/main.py
│   ├── maneuver/                    # 🚀 Maneuver Agent (Agent 4 Teammate Stub)
│   │   ├── README.md
│   │   └── app/main.py
│   └── optimizer/                   # ⚡ Optimizer Agent (Agent 5 Teammate Stub)
│       ├── README.md
│       └── app/main.py
├── frontend/                        # 🖥️ Frontend Dashboard (Teammate Stub)
│   ├── README.md
│   └── index.html
└── tests/                           # 🧪 Root Integration Test Suite
    └── test_integration_contracts.py # End-to-end multi-agent contract verification test
```

---

## 6. TRACKING + SCREENING — IMPLEMENTED BASELINE

The Tracking + Screening service in `services/propagation/` is fully operational and verified:

1. **CelesTrak Ingestion**: Ingests active satellites, stations (ISS), and debris from CelesTrak via HTTP requests.
2. **OMM & TLE Parser**: Parses 2-line/3-line TLE formats and CelesTrak OMM JSON records.
3. **Offline Fallback**: Automatically falls back to `services/propagation/data/celestrak_cache.json` if network access is unavailable during hackathon demos.
4. **Raw Data Retention**: Preserves raw TLE text and original OMM records in `orbital_data.raw_tle_line1`, `raw_tle_line2`, and `raw_data`.
5. **Skyfield & SGP4 Propagation Engine**: Computes Cartesian state vectors $(\mathbf{r}, \mathbf{v})$ in the **TEME** reference frame.
6. **Standardized Units**:
   - Position: Kilometers ($\text{km}$)
   - Velocity: Kilometers per second ($\text{km/s}$)
   - Altitude: Geodetic altitude above WGS84 ellipsoid in kilometers ($\text{km}$)
   - Timestamps: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)
7. **Data Quality Metrics**: Calculates age of orbital data (`data_age_hours`) and quality level (`HIGH`, `MEDIUM`, `LOW`).
8. **90-Minute Trajectory Generator**: Computes 91 sequential points spanning 0 to 90 minutes at 1-minute steps.
9. **Two-Stage Conjunction Screening**:
   - *Stage 1 (Coarse Filter)*: Altitude-band overlap filtering ($\pm 50\text{ km}$ buffer). Discards non-interacting orbit pairs (e.g. LEO vs GEO) instantly.
   - *Stage 2 (Fine Filter)*: Propagates candidate pairs over 90 minutes at 1-minute steps evaluating both objects in TEME at matching UTC timestamps. Performs local 5-second step time refinement around the candidate minimum interval to pinpoint Time of Closest Approach (TCA) and minimum distance ($d_{\min}$).
   - Computes relative velocity vector magnitude in $\text{km/s}$.
10. **Verified Synthetic Debris Generator**: Injects `DEB-DEMO (SYNTHETIC_DEBRIS)` with ID `SYNTHETIC-99999`. Anchors to target satellite state at $t_{tca} = \text{NOW} + 45\text{ min}$, applies a verified perpendicular offset vector ($\sim 8.2\text{ km}$ distance), and generates valid SGP4 TLE lines to guarantee reproducible conjunction candidate detection during presentation demos.
11. **FastAPI REST Service**: Exposes 8 REST endpoints under `/api/v1/` with unversioned aliases (`/objects`, `/conjunctions`, `/health`).
12. **Database Persistence**: SQLAlchemy models supporting PostgreSQL with zero-dependency SQLite fallback (`orbitalguard.db`).

---

## 7. SHARED CONTRACTS (`shared/schemas/`)

All agents communicate using versioned Pydantic schemas in `shared/schemas/`:

### 7.1 Canonical Data Standards
- **Position**: Kilometers ($\text{km}$)
- **Velocity**: Kilometers per second ($\text{km/s}$)
- **Altitude**: Kilometers ($\text{km}$)
- **Separation Distance**: Kilometers ($\text{km}$)
- **Delta-V ($\Delta v$)**: Meters per second ($\text{m/s}$)
- **Timestamps**: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)
- **Reference Frame**: `TEME` (True Equator Mean Equinox)
- **Object Types**: `SATELLITE`, `DEBRIS`, `ROCKET_BODY`, `SYNTHETIC_DEBRIS`, `UNKNOWN`
- **Synthetic ID Format**: `SYNTHETIC-99999` (never impersonates NORAD IDs)

### 7.2 Summary of Contracts

| Schema | File Path | Producer | Consumer | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `OrbitalObject` | `shared/schemas/object.py` | Tracking Agent | All Agents & Frontend | Single canonical representation of a tracked space object (RAW data + CALCULATED state + quality). |
| `StateVector` | `shared/schemas/state.py` | Tracking Agent | All Agents & Frontend | Instantaneous Cartesian position ($\text{km}$), velocity ($\text{km/s}$), altitude ($\text{km}$), and TEME frame. |
| `Trajectory` | `shared/schemas/trajectory.py` | Tracking Agent | All Agents & Frontend | Array of future points over propagation horizon. |
| `ConjunctionCandidate` | `shared/schemas/conjunction.py` | Screening Agent | Risk Agent & Frontend | Identified potential close approach event ($d_{\min} \le 50\text{ km}$, TCA, relative velocity). |
| `RiskAssessment` | `shared/schemas/risk.py` | Risk Agent | Maneuver Agent & Frontend | Risk score ($0-100$), risk level tier, and evaluation notes. |
| `ManeuverCandidates` | `shared/schemas/maneuver.py` | Maneuver Agent | Optimizer Agent & Frontend | List of simulated avoidance burn options ($\Delta v$ in $\text{m/s}$). |
| `ManeuverDecision` | `shared/schemas/decision.py` | Optimizer Agent | Frontend | Optimal maneuver choice recommendation and human approval state. |

---

## 8. API CONTRACT REFERENCE

Base URL: `http://localhost:8000/api/v1` (Unversioned aliases `/health`, `/objects`, `/conjunctions` are also active).

| Method | Endpoint | Description | Query Parameters | Primary Consumers |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Health status & DB mode | - | All / Monitoring |
| `POST` | `/api/v1/ingest` | Ingest TLEs (CelesTrak / Cache fallback) | `group` (`active`, `stations`) | Tracking Engine |
| `GET` | `/api/v1/objects` | List all tracked space objects | `object_type` (`SATELLITE`, `DEBRIS`) | All Agents & Frontend |
| `GET` | `/api/v1/objects/{id}` | Object details & current Cartesian state | - | All Agents & Frontend |
| `GET` | `/api/v1/objects/{id}/trajectory` | 90-min future trajectory array | `horizon` (`90`), `step` (`1.0`) | Frontend & Risk |
| `POST` | `/api/v1/screen` | Execute 2-stage screening engine | `horizon` (`90`), `threshold_km` (`50.0`) | Screening / Workflow |
| `GET` | `/api/v1/conjunctions` | List flagged close approach candidates | - | Risk Agent & Frontend |
| `POST` | `/api/v1/demo/inject-synthetic` | Inject synthetic debris & register conjunction | `target_catalog_id` (`25544`) | Frontend / Presentation Demo |

---

## 9. DOWNSTREAM AGENT HANDOFF GUIDELINES

### 🛡️ For Risk Agent Teammate (`services/risk/`)
- **Input Data**: Consumes `ConjunctionCandidate` from `GET http://localhost:8000/api/v1/conjunctions` or mock fixture `services/propagation/data/sample_conjunctions.json`.
- **Output Contract**: `shared.schemas.risk.RiskAssessment`.
- **Key Task**: Calculate `time_to_tca_minutes`, evaluate distance and relative velocity, compute `risk_score` ($0-100$), assign `risk_level` (`HIGH`, `MEDIUM`, `LOW`), and expose REST endpoint `/assess-risk`.

### 🚀 For Maneuver Agent Teammate (`services/maneuver/`)
- **Input Data**: Consumes `RiskAssessment` from Risk Agent or mock fixture `services/propagation/data/sample_risk.json`.
- **Output Contract**: `shared.schemas.maneuver.ManeuverCandidates`.
- **Key Task**: For high-risk conjunctions, simulate impulse burn vectors ($\Delta v$ in $\text{m/s}$ in POSIGRADE / RETROGRADE directions), calculate post-maneuver separation distance ($\text{km}$), and expose REST endpoint `/generate-maneuvers`.

### ⚡ For Optimizer Agent Teammate (`services/optimizer/`)
- **Input Data**: Consumes `ManeuverCandidates` from Maneuver Agent or mock fixture `services/propagation/data/sample_maneuvers.json`.
- **Output Contract**: `shared.schemas.decision.ManeuverDecision`.
- **Key Task**: Select optimal candidate minimizing $\Delta v$ while achieving `resulting_risk == "LOW"`, construct decision justification, set `human_approval_required = True`, and expose REST endpoint `/optimize-decision`.

### 🖥️ For Frontend Teammate (`frontend/`)
- **APIs to Consume**:
  - `GET /api/v1/objects` $\rightarrow$ Render tracked satellites & debris on 3D globe / map.
  - `GET /api/v1/objects/{id}/trajectory` $\rightarrow$ Render 90-minute trajectory polyline.
  - `GET /api/v1/conjunctions` $\rightarrow$ Display close approach alert cards.
  - `POST /api/v1/demo/inject-synthetic` $\rightarrow$ Trigger synthetic debris injection live during hackathon presentation.

---

## 10. MOCK & OFFLINE DEVELOPMENT FIXTURES

Teammates can develop independently immediately using mock JSON fixtures in `services/propagation/data/`:
- `services/propagation/data/sample_objects.json`
- `services/propagation/data/sample_conjunctions.json`
- `services/propagation/data/sample_risk.json`
- `services/propagation/data/sample_maneuvers.json`
- `services/propagation/data/sample_decisions.json`
- `services/propagation/data/celestrak_cache.json`

---

## 11. ENGINEERING CONVENTIONS & SCIENTIFIC LIMITATIONS

### Engineering Conventions
- All datetimes use timezone-aware UTC (`timezone.utc`).
- All Cartesian vectors use TEME reference frame.
- All REST endpoints are versioned under `/api/v1/` with backward-compatible unversioned aliases.
- Synthetic objects use catalog ID prefix `SYNTHETIC-` and `object_type = "SYNTHETIC_DEBRIS"`.

### Scientific & Operational Limitations
- **SGP4 Accuracy**: SGP4 is an analytical propagator subject to drag and unmodeled perturbations; accuracy degrades over time.
- **Refined Minimum vs Exact Physical Minimum**: Sampled/refined minimum distance is an estimate based on SGP4 propagation timesteps, not an analytical exact minimum.
- **Collision Terminology**: A flagged encounter is a *Potential Close Approach / Conjunction Candidate*, NOT an operational collision probability (covariance-based $P_c$ calculation is out of scope).
- **Decision-Support Scope**: The system provides simulated decision-support recommendations and does NOT execute physical hardware control.

---

## 12. TESTING STATUS

- **Tracking & Screening Unit/Integration Tests**: 19 tests in `services/propagation/tests/`
- **Root Multi-Agent Integration Contract Tests**: 1 test in `tests/test_integration_contracts.py`
- **Total Test Count**: **20 tests**
- **Status**: **🟢 100% PASSED** (Execution time: ~2.3 seconds)

To run all tests:
```bash
python -m pytest services/propagation/tests/ tests/ -v
```

---

## 13. DOCKER & RUNTIME SETUP

### Local Development Setup
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run Tracking & Screening Service
python -m uvicorn services.propagation.app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker Compose
```bash
docker-compose up --build
```

---

## 14. RECOMMENDED GIT WORKFLOW FOR TEAMMATES

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/MadhurSikarwar/GDGSpaceTech.git
   cd GDGSpaceTech
   ```
2. **Fetch and Branch from `main`**:
   ```bash
   git checkout main
   git pull origin main
   git checkout -b <feature-branch-name>
   ```
3. **Read Contracts & Schemas First**:
   Review `INTEGRATION_CONTRACT.md` and `shared/schemas/`.
4. **Develop Within Assigned Directory Only**:
   Work only in your assigned service directory (`services/risk/`, `services/maneuver/`, `services/optimizer/`, `frontend/`).
5. **Run Tests Before Committing**:
   ```bash
   python -m pytest tests/
   ```
6. **Push Branch & Open PR**:
   ```bash
   git push -u origin <feature-branch-name>
   ```

---

## 15. INTEGRATION RULES

> [!WARNING]
> 1. **Do NOT modify shared schemas** (`shared/schemas/`) without discussing with the team.
> 2. **Do NOT change units**: Position ($\text{km}$), Velocity ($\text{km/s}$), Distance ($\text{km}$), Delta-V ($\text{m/s}$).
> 3. **Do NOT change coordinate frames**: Keep TEME frame for all Cartesian state vectors.
> 4. **Do NOT break existing API endpoints**.
> 5. **Do NOT modify another teammate's service folder**.

---

## 16. IMMEDIATE NEXT STEPS FOR EACH TEAMMATE

- **Risk Teammate**: Open `services/risk/README.md`, read `shared/schemas/conjunction.py` and `risk.py`, consume `sample_conjunctions.json`, and implement risk scoring logic in `services/risk/app/main.py`.
- **Maneuver Teammate**: Open `services/maneuver/README.md`, read `shared/schemas/risk.py` and `maneuver.py`, consume `sample_risk.json`, and implement impulse burn simulation logic in `services/maneuver/app/main.py`.
- **Optimizer Teammate**: Open `services/optimizer/README.md`, read `shared/schemas/maneuver.py` and `decision.py`, consume `sample_maneuvers.json`, and implement trade-off recommendation logic in `services/optimizer/app/main.py`.
- **Frontend Teammate**: Open `frontend/README.md`, query `GET http://localhost:8000/api/v1/objects` and `GET http://localhost:8000/api/v1/conjunctions`, and build the interactive UI.

---

## 17. FINAL PASTE-READY CONTEXT BLOCKS FOR TEAMMATES

Below are copy-pasteable context blocks tailored for each teammate to initialize their AI coding assistant session.

---

### ### BLOCK 1 — RISK AGENT

```markdown
You are assisting with the Risk Agent module for OrbitalGuard — Autonomous Orbital Traffic Intelligence.

GitHub Repository: https://github.com/MadhurSikarwar/GDGSpaceTech
Active Baseline Branch: main

FIRST STEPS:
1. Inspect the repository on disk.
2. Read INTEGRATION_CONTRACT.md for system architecture, units, and API specifications.
3. Inspect shared/schemas/ (specifically conjunction.py and risk.py).
4. Inspect your assigned module directory: services/risk/.

YOUR SCOPE & RESPONSIBILITIES:
- You own services/risk/. Do NOT modify services/propagation/, services/maneuver/, services/optimizer/, or frontend/.
- You consume ConjunctionCandidate contract objects produced by the Screening Agent (available via GET http://localhost:8000/api/v1/conjunctions or mock fixture services/propagation/data/sample_conjunctions.json).
- You must produce a valid RiskAssessment Pydantic object matching shared/schemas/risk.py.
- Calculate time_to_tca_minutes = (tca - now).total_seconds() / 60.0.
- Compute a risk_score (0-100) and assign risk_level ("CRITICAL", "HIGH", "MEDIUM", "LOW").
- Expose a FastAPI endpoint in services/risk/app/main.py.

CONVENTIONS & CONSTRAINTS:
- Use timezone-aware UTC ISO-8601 timestamps.
- Separation distance is in km, relative velocity in km/s.
- Do NOT change shared schema definitions without coordination.
- Run tests (python -m pytest tests/) to verify contract compatibility before proposing changes.

Begin by inspecting the repository, reading INTEGRATION_CONTRACT.md, and explaining your proposed Risk Agent implementation.
```

---

### ### BLOCK 2 — MANEUVER AGENT

```markdown
You are assisting with the Maneuver Agent module for OrbitalGuard — Autonomous Orbital Traffic Intelligence.

GitHub Repository: https://github.com/MadhurSikarwar/GDGSpaceTech
Active Baseline Branch: main

FIRST STEPS:
1. Inspect the repository on disk.
2. Read INTEGRATION_CONTRACT.md for system architecture, units, and API specifications.
3. Inspect shared/schemas/ (specifically risk.py and maneuver.py).
4. Inspect your assigned module directory: services/maneuver/.

YOUR SCOPE & RESPONSIBILITIES:
- You own services/maneuver/. Do NOT modify services/propagation/, services/risk/, services/optimizer/, or frontend/.
- You consume RiskAssessment contract objects produced by the Risk Agent (or mock fixture services/propagation/data/sample_risk.json).
- You must produce a valid ManeuverCandidates Pydantic object matching shared/schemas/maneuver.py.
- Evaluate simulated impulse burn options (delta_v_m_s in m/s, burn_direction: POSIGRADE, RETROGRADE, NORMAL).
- Calculate simulated post-maneuver separation distance (new_separation_km in km) and resulting_risk tier.
- Expose a FastAPI endpoint in services/maneuver/app/main.py.

CONVENTIONS & CONSTRAINTS:
- Units: Delta-V in m/s, separation distance in km, timestamps in UTC ISO-8601.
- Do NOT change shared schema definitions without coordination.
- Run tests (python -m pytest tests/) to verify contract compatibility before proposing changes.

Begin by inspecting the repository, reading INTEGRATION_CONTRACT.md, and explaining your proposed Maneuver Agent implementation.
```

---

### ### BLOCK 3 — OPTIMIZER / DECISION AGENT

```markdown
You are assisting with the Optimizer / Decision Agent module for OrbitalGuard — Autonomous Orbital Traffic Intelligence.

GitHub Repository: https://github.com/MadhurSikarwar/GDGSpaceTech
Active Baseline Branch: main

FIRST STEPS:
1. Inspect the repository on disk.
2. Read INTEGRATION_CONTRACT.md for system architecture, units, and API specifications.
3. Inspect shared/schemas/ (specifically maneuver.py and decision.py).
4. Inspect your assigned module directory: services/optimizer/.

YOUR SCOPE & RESPONSIBILITIES:
- You own services/optimizer/. Do NOT modify services/propagation/, services/risk/, services/maneuver/, or frontend/.
- You consume ManeuverCandidates contract objects produced by the Maneuver Agent (or mock fixture services/propagation/data/sample_maneuvers.json).
- You must produce a valid ManeuverDecision Pydantic object matching shared/schemas/decision.py.
- Select the optimal candidate balancing minimum fuel expenditure (delta_v_m_s) while achieving resulting_risk == "LOW".
- Output decision recommendation reason, set human_approval_required = True, and update simulation status.
- Expose a FastAPI endpoint in services/optimizer/app/main.py.

CONVENTIONS & CONSTRAINTS:
- Do NOT change shared schema definitions without coordination.
- Run tests (python -m pytest tests/) to verify contract compatibility before proposing changes.

Begin by inspecting the repository, reading INTEGRATION_CONTRACT.md, and explaining your proposed Optimizer Agent implementation.
```

---

### ### BLOCK 4 — FRONTEND AGENT

```markdown
You are assisting with the Frontend Dashboard module for OrbitalGuard — Autonomous Orbital Traffic Intelligence.

GitHub Repository: https://github.com/MadhurSikarwar/GDGSpaceTech
Active Baseline Branch: main

FIRST STEPS:
1. Inspect the repository on disk.
2. Read INTEGRATION_CONTRACT.md for API endpoints, JSON response structures, and dataset conventions.
3. Inspect your assigned directory: frontend/.

YOUR SCOPE & RESPONSIBILITIES:
- You own frontend/. Do NOT modify backend service python code.
- Query backend API endpoints running at http://localhost:8000/api/v1:
  - GET /api/v1/objects -> List tracked space objects and current state vectors (TEME, km, km/s, altitude).
  - GET /api/v1/objects/{id}/trajectory -> Retrieve 90-minute trajectory array.
  - GET /api/v1/conjunctions -> List flagged close approach candidate alerts.
  - POST /api/v1/demo/inject-synthetic -> Trigger synthetic debris injection for live presentation demo.
- Build an intuitive UI displaying tracked satellites/debris, trajectory paths, risk alerts, and maneuver approval controls.

CONVENTIONS & CONSTRAINTS:
- Display position in km, velocity in km/s, altitude in km, distance in km.
- Use ISO-8601 UTC timestamps.

Begin by inspecting the repository, reading INTEGRATION_CONTRACT.md, and proposing your UI layout and component architecture.
```
