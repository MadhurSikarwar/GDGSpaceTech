# 🛰️ OrbitalGuard — Autonomous Orbital Traffic Intelligence System

**Tagline**: TRACK. PREDICT. AVOID.  
**Architecture**: Multi-Agent Decision-Support & Simulation Platform  
**Primary Stack**: Python 3.12, Skyfield / SGP4, FastAPI, Pydantic v2, SQLAlchemy (PostgreSQL / SQLite fallback), Docker

---

## 1. System Overview & Agent Responsibilities

OrbitalGuard tracks satellites and orbital debris, predicts future 90-minute positions, screens for potential close approaches, assesses conjunction risk, and generates simulated avoidance maneuver recommendations.

```text
CelesTrak / Space-Track
          ↓
   TRACKING SERVICE (Agent 1 - Platform)
          ↓
  SGP4 PROPAGATION (Skyfield / SGP4 Engine in TEME frame)
          ↓
 Position + Velocity + Trajectory (km, km/s, UTC)
          ↓
   SCREENING SERVICE (Agent 2 - Platform)
          ↓
 Potential Conjunction Candidates (2-Stage Coarse/Fine Filter)
          ↓
       RISK AGENT (Agent 3 - Teammate)
          ↓
     MANEUVER AGENT (Agent 4 - Teammate)
          ↓
  OPTIMIZER / DECISION AGENT (Agent 5 - Teammate)
          ↓
     HUMAN APPROVAL GATE & FRONTEND DASHBOARD
```

---

## 2. Directory Structure

```text
GDG-SPACE-TECH/
├── shared/
│   └── schemas/                 # Shared Pydantic contracts across all agents
│       ├── object.py            # Canonical OrbitalObject schema
│       ├── state.py             # StateVector (position_km, velocity_km_s, altitude_km)
│       ├── trajectory.py        # Trajectory & TrajectoryPoint schema
│       ├── conjunction.py       # ConjunctionCandidate schema
│       ├── risk.py              # RiskAssessment schema (for Risk Agent)
│       ├── maneuver.py          # ManeuverCandidates schema (for Maneuver Agent)
│       └── decision.py          # ManeuverDecision schema (for Optimizer Agent)
├── services/
│   ├── propagation/             # Tracking + Screening Service (Agent 1 & 2)
│   │   ├── app/
│   │   │   ├── api/routes.py    # REST endpoints (/api/v1/...)
│   │   │   ├── database/        # SQLAlchemy models & repository
│   │   │   ├── ingestion/       # CelesTrak HTTP client & TLE/OMM parser
│   │   │   ├── propagation/     # SGP4 propagation engine & 90-min trajectory
│   │   │   ├── screening/       # 2-Stage Coarse (Altitude) & Fine (SGP4) filter
│   │   │   ├── synthetic/       # Verified synthetic debris generator
│   │   │   ├── config.py        # Settings & thresholds
│   │   │   └── main.py          # FastAPI application entry point
│   │   ├── data/                # Mock fixtures & offline CelesTrak cache
│   │   └── tests/               # Unit & integration tests for Tracking/Screening
│   ├── risk/                    # Risk Agent (Agent 3 - Teammate Stub)
│   ├── maneuver/                # Maneuver Agent (Agent 4 - Teammate Stub)
│   └── optimizer/               # Optimizer / Decision Agent (Agent 5 - Teammate Stub)
├── frontend/                    # Frontend Dashboard Stub
├── tests/                       # Cross-agent integration contract tests
├── INTEGRATION_CONTRACT.md      # Authoritative Multi-Agent Integration Contract
├── docker-compose.yml           # Container orchestration
├── requirements.txt             # Core dependencies
├── .env.example                 # Environment variables template
└── .gitignore                   # Git ignore file
```

---

## 3. Technology Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| **Orbital Data** | CelesTrak | TLE/OMM orbital data |
| **Backup Data** | Space-Track | Secondary orbital data source |
| **Orbital Propagation** | **Skyfield + SGP4** | Calculate satellite/debris positions & velocities |
| **Backend — Tracking** | **Python** | Tracking + screening logic |
| **Backend API** | **FastAPI** | Expose tracking/conjunction data |
| **Database** | **PostgreSQL / SQLite** | Store objects, TLEs, conjunctions |
| **DB ORM** | SQLAlchemy | Python ↔ Database |
| **HTTP Client** | Requests / HTTPX | Fetch CelesTrak data |
| **Risk Agent** | Python | Risk scoring |
| **Maneuver Agent** | Python | Generate/simulate avoidance options |
| **Optimizer Agent** | Python | Optimal maneuver decision recommendation |
| **AI/LLM Layer** | Gemini | Agent reasoning / natural-language alerts |
| **Frontend** | React / Next.js | Interactive dashboard |
| **Testing** | Pytest | Automated testing |
| **Containerization** | Docker | Consistent deployment environment |

---

## 4. Quick Start Guide

### 4.1 Install Dependencies
```bash
pip install -r requirements.txt
```

### 4.2 Run Tracking & Screening Service
```bash
python -m uvicorn services.propagation.app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive OpenAPI documentation will be available at:
- **Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

### 4.3 Run Complete Test Suite
```bash
python -m pytest services/propagation/tests/ tests/ -v
```

---

## 5. Key API Endpoints (`/api/v1/`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Health status & DB mode |
| `POST` | `/api/v1/ingest` | Ingest TLEs (CelesTrak / Cache fallback) |
| `GET` | `/api/v1/objects` | List all tracked space objects |
| `GET` | `/api/v1/objects/{id}` | Single object details & real-time TEME state vector |
| `GET` | `/api/v1/objects/{id}/trajectory` | 90-minute trajectory array |
| `POST` | `/api/v1/screen` | Execute 2-stage screening engine |
| `GET` | `/api/v1/conjunctions` | List flagged `ConjunctionCandidate` records |
| `POST` | `/api/v1/demo/inject-synthetic` | Inject synthetic debris & register deterministic conjunction |

---

## 6. Multi-Agent Development Guide

Read [INTEGRATION_CONTRACT.md](file:///c:/Users/mayur/Desktop/Projects/GDG-SPACE-TECH/INTEGRATION_CONTRACT.md) for full schema definitions, field names, units, timestamps, coordinate systems, and step-by-step instructions for teammates building Risk, Maneuver, Optimizer, and Frontend components.
