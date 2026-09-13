# 🛰️ OrbitalGuard — Autonomous Space Domain Awareness (SDA) Platform

<div align="center">

**TRACK · PREDICT · AVOID**

*An Industry-Grade Multi-Agent Decision-Support & Autonomous Collision Avoidance System for Low Earth Orbit (LEO)*

[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Three.js](https://img.shields.io/badge/Three.js-r160-black.svg?style=flat&logo=threedotjs&logoColor=white)](https://threejs.org)
[![Astrodynamics](https://img.shields.io/badge/Astrodynamics-Skyfield%20%7C%20SGP4-0B3D91.svg?style=flat&logo=nasa&logoColor=white)](https://rhodesmill.org/skyfield/)
[![Optimization](https://img.shields.io/badge/Optimization-SciPy%20SLSQP-8CAAE6.svg?style=flat&logo=scipy&logoColor=white)](https://scipy.org)
[![AI Reasoning](https://img.shields.io/badge/AI%20Reasoning-Groq%20LLM-F55036.svg?style=flat)](https://groq.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

---
## 🎥 Demo Video

Watch the complete OrbitalGuard demo showcasing the platform's orbital tracking, conjunction detection, risk assessment, maneuver optimization, agentic decision pipeline, and human-in-the-loop approval.

▶️ **[Watch the OrbitalGuard Demo](https://www.youtube.com/watch?v=Be4tBh7HUjg)**

---

## 📌 Executive Summary

**OrbitalGuard** is a next-generation **Space Domain Awareness (SDA)** and **Autonomous Space Traffic Management (STM)** platform. As orbital congestion in Low Earth Orbit (LEO) accelerates with mega-constellations and fragmentation debris, mission operators require millisecond-level orbital screening, mathematically rigorous collision probability assessment, and fuel-optimal avoidance decisions.

OrbitalGuard bridges the gap between raw orbital telemetry (TLE/OMM) and actionable operator command through:
- Continuous tracking of **10,880+ space objects** from CelesTrak in real-time TEME coordinates.
- **2-Stage Coarse & Fine Conjunction Screening** with adaptive 90-minute ephemeris propagation.
- **Foster (1992) 2D Probability of Collision ($P_c$)** integration over empirical anisotropic covariance ellipsoids.
- **Live NOAA Space Weather Integration** pulling real-time Solar Flux (F10.7) and Geomagnetic Indices ($K_p$ / $A_p$) to dynamically model thermospheric atmospheric drag.
- **Clohessy-Wiltshire (CW) Relative Motion Physics & SciPy SLSQP Numerical Optimization** for minimum-$\Delta v$ collision avoidance maneuvers that preserve satellite operational slots.
- **Groq LLM AI Decision Justifications** producing human-interpretable rationale for space flight directors.
- **High-Performance 3D WebGL Mission Console (Three.js)** featuring Earth occlusion, orbital trails, timeline scrubbing, ground station visibility windows, and a closed-loop **Human Approval Gate** with post-maneuver mitigation visualization.

---

## 🏗️ System Architecture & Multi-Agent Pipeline

OrbitalGuard is built as a distributed, decoupled multi-agent microservice suite. All services communicate via typed Pydantic contracts and can be launched concurrently using the unified orchestrator:

```text
                                CelesTrak / Space-Track / NOAA SWPC
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [AGENT 1] TRACKING & PROPAGATION SERVICE (:8000)                                                │
│ ├─ SGP4 Orbital Propagator (Skyfield TEME Frame)                                                │
│ ├─ High-Throughput Propagation Cache (DB Versioned, 5000+ Objects)                             │
│ └─ Polar Ground Station AOS/LOS Pass Geometry (Svalbard, Fairbanks, McMurdo)                   │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │ Real-Time Ephemeris & State Vectors
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [AGENT 2] CONJUNCTION SCREENING ENGINE (:8000)                                                  │
│ ├─ Stage 1: Coarse Altitude & Apogee/Perigee Spherical Envelope Filter                          │
│ ├─ Stage 2: SGP4 Fine Screening (TCA, Miss Distance, Relative Velocity Vector)                  │
│ ├─ Foster (1992) 2D Probability of Collision (Pc) Numerical Quadrature                          │
│ └─ Synthetic Debris Injection & Deterministic Scenario Generator                                │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │ ConjunctionCandidate Event
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [AGENT 3] RISK ASSESSMENT AGENT (:8001)                                                         │
│ ├─ Multi-Factor Hazard Scoring Model (55% Separation, 30% Time-to-TCA, 15% Relative Velocity)    │
│ ├─ Risk Tier Categorization (CRITICAL, HIGH, MEDIUM, LOW)                                       │
│ └─ Conjunction Event History & Database State Tracking                                          │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │ RiskAssessment Payload
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [AGENT 4] MANEUVER OPTIONS & PHYSICS OPTIMIZER AGENT (:8002)                                    │
│ ├─ Clohessy-Wiltshire (Hill-CW) Linearized Relative Motion State Transition                     │
│ ├─ SciPy SLSQP Minimum-Delta-V Cost Function Solver (RIC Burn Decomposition)                    │
│ ├─ Hard Constraints: Pc < Critical Threshold & Semi-Major Axis Drift Bound                      │
│ └─ Multi-Candidate Generation (Posigrade, Retrograde, Normal, Optimal Burn)                     │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │ ManeuverCandidates Catalog
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [AGENT 5] DECISION OPTIMIZER AGENT (:8003)                                                      │
│ ├─ Multi-Attribute Trade-Off Optimization (Delta-V vs. Risk Reduction Margin)                   │
│ └─ Groq LLM (Llama 3.1) Natural Language Operational Justification Reasoning                    │
└───────────────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                │ Recommended ManeuverDecision
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [MISSION CONSOLE] INTERACTIVE 3D DASHBOARD & HUMAN APPROVAL GATE (:6931)                        │
│ ├─ Real-Time 3D WebGL Earth & Orbit Visualization (Three.js, 60 FPS)                            │
│ ├─ Interactive 90-Minute Scrubber Timeline with 1x / 5x / 10x Simulation Multipliers            │
│ ├─ Human-in-the-Loop APPROVE / REJECT Gate (Rejection Banner & Candidate Switching)             │
│ └─ Post-Maneuver Closed-Loop Mitigation Visualization (Safe Divergent Trajectory)               │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Newest Features & Core Innovations

### 1. 🌌 Advanced Astrodynamics & Physics Engine

* **Foster (1992) 2D Probability of Collision ($P_c$)**:
  - Eliminates reliance on naive "miss distance" scalar alerts.
  - Projects the 3D relative position and combined covariance ellipsoid onto the 2D **Encounter Plane** perpendicular to the relative velocity vector at Time of Closest Approach (TCA).
  - Performs adaptive 2D polar numerical quadrature (`scipy.integrate.dblquad`) over the combined hard-body radius (HBR) disk centered at the encounter origin.
* **Empirical Anisotropic Covariance Realism (RIC Frame)**:
  - Generates realistic position uncertainty ellipsoids in the **Radial, In-track, Cross-track (RIC / RSW)** reference frame based on TLE data age.
  - Models secular along-track error growth (which dominates due to unmodeled thermospheric drag) and applies object-type inflation factors (active satellites vs. radar-dim debris and rocket bodies).
  - Rotates covariance dynamically into the TEME inertial frame for screening.
* **Clohessy-Wiltshire (CW) Relative Motion Dynamics**:
  - Implements the linearized Hill-Clohessy-Wiltshire state transition equations to analytically predict the target satellite's relative displacement at TCA induced by an impulsive burn applied at $t_0$.
* **Constrained Numerical $\Delta v$ Optimization (SciPy SLSQP)**:
  - Formulates collision avoidance as a constrained minimization problem:
    $$\min_{\Delta \mathbf{v}} \|\Delta \mathbf{v}\| \quad \text{subject to} \quad P_c(\Delta \mathbf{v}) \le 10^{-4}, \quad |\Delta a| \le 5\text{ km}$$
  - Finds the exact fuel-optimal burn direction in the RIC frame while preserving orbital slot retention.

### 2. ☀️ Live NOAA Space Weather Integration
* Direct, asynchronous streaming of real-time space weather feeds from the **NOAA Space Weather Prediction Center (SWPC)**:
  - **Solar Radio Flux (F10.7 cm)**: 2800 MHz solar emissions driving upper-atmosphere heating.
  - **Planetary $K_p$ & Running $A_p$ Indices**: Linear geomagnetic activity measurements.
* Dynamically calculates a **Thermospheric Drag Activity Scalar** that scales LEO along-track covariance uncertainty and trajectory propagation accuracy during geomagnetic storm events.
* Status badge displayed live in the Mission Console topbar (`QUIET`, `MODERATE`, `ELEVATED`, `STORM`).

### 3. 📡 Polar Ground Station Line-of-Sight (AOS/LOS) Windows
* Computes real-time topocentric line-of-sight elevation geometry using WGS84 coordinates for operational polar ground stations:
  - **Svalbard Satellite Station (SvalSat)**, Norway (78.23° N, 15.39° E)
  - **Fairbanks Gilmore Creek Station**, Alaska (64.98° N, 147.50° W)
  - **McMurdo Ground Station**, Antarctica (77.85° S, 166.67° E)
* Detects Acquisition of Signal (AOS), Loss of Signal (LOS), duration, and maximum elevation angle ($\ge 10^\circ$ horizon mask) for scheduled telemetry downlinks and emergency command uplinks.

### 4. 🤖 AI-Powered Maneuver Decision Reasoning
* Integrates **Groq LLM** (`llama-3.1-8b-instant` / `groq/compound-mini`) inside the Decision Optimizer Agent.
* Automatically synthesizes orbital encounter geometry, $\Delta v$ fuel budget, resulting separation distance, and residual risk into an operator-ready natural language justification.
* Features automatic analytical fallback reasoning if external LLM APIs are unreachable.

### 5. 🎮 3D Mission Console & Operator Experience (UX)
* **High-Performance WebGL Orbit View (Three.js)**:
  - Renders 5,000+ active satellites and orbital debris pieces at a smooth 60 FPS in the TEME coordinate frame.
  - Features photorealistic Earth surface textures, atmospheric rim scattering, Earth occlusion (depth-tested orbit paths that realistically disappear behind the planetary sphere), and orbital trails.
  - Color-coded classification nodes:
    - 🛰️ **Satellites**: Cyan (`#56e3d1`)
    - 🪨 **Debris**: Amber (`#f0a94e`)
    - 🚀 **Rocket Bodies**: Purple (`#a99bf2`)
    - 💥 **Synthetic Debris**: Flame Red (`#f0616e`)
* **90-Minute Interactive Timeline Scrubber**:
  - Play, pause, reverse, and scrub through future orbital trajectories at 1x, 5x, and 10x speeds with instantaneous multi-object position updates.
* **Telemetry Inspection HUD & Explore Card**:
  - Click any satellite or debris node to reveal instant altitude, data age, TLE quality metrics, 3D Cartesian position/velocity vectors, and polar ground station passes.
  - Internal scrolling and height management to prevent timeline UI collisions.
  - Dual mission clocks displaying both UTC (`Z`) and Indian Standard Time (`IST`).
* **Interactive Human Approval Gate**:
  - Complete operator-in-the-loop governance: no automated burn executes without explicit human approval.
  - **Maneuver Rejection State**: Clicking **REJECT** transforms the approval gate into a visual rejection alert banner, allowing flight operators to switch candidates or evaluate alternate delta-V solutions.
  - **Post-Maneuver Closed-Loop Mitigation**: Clicking **APPROVE** computes and plots the safe divergent post-burn trajectory directly on the 3D globe alongside the pre-burn collision path.
* **Targeted Synthetic Debris Injection**:
  - One-click demo trigger (`POST /api/v1/demo/inject-synthetic`) that injects synthetic debris engineered to intersect target satellites (such as the ISS or any selected catalog object) at realistic close-approach velocities.
  - Uses targeted 2-object screening to execute within milliseconds without catalog timeouts, instantly populating the Conjunctions view and auto-advancing into the Pipeline.

### 6. 💾 High-Throughput Dual Database Architecture
* **PostgreSQL (Supabase) + Local SQLite**:
  - Shipped with an optimized SQLite database (`orbitalguard.db`) pre-seeded with **10,880+ space objects** and verified conjunctions for instant, zero-configuration local execution.
  - Seamless environment switch to PostgreSQL/Supabase via `DATABASE_URL`.
* **In-Memory Propagation Cache**:
  - SGP4 propagation cache keyed by database write-version, eliminating redundant orbital propagations on repeat page visits and catalog filtering.
* **Paginated REST API**:
  - Priority ordering ensures synthetic debris and primary demo assets (e.g., ISS) are always returned on initial page queries.

---

## 💻 Tech Stack & Microservices

| Service / Layer | Technology | Default Port | Role & Capabilities |
| :--- | :--- | :--- | :--- |
| **Tracking & Screening** | Python, FastAPI, Skyfield, SGP4, SciPy | `8000` | SGP4 propagation, 2-stage screening, Foster 2D $P_c$, NOAA space weather, ground stations. |
| **Risk Assessment Agent** | Python, FastAPI, Pydantic | `8001` | Multi-factor deterministic risk scoring (0-100), hazard classification, urgency analysis. |
| **Maneuver Options Agent**| Python, FastAPI, SciPy SLSQP, NumPy | `8002` | Clohessy-Wiltshire relative motion dynamics, fuel-optimal $\Delta v$ optimization, burn candidates. |
| **Decision Optimizer Agent**| Python, FastAPI, Groq LLM | `8003` | Multi-attribute decision trade-offs, Groq LLM natural language justification synthesis. |
| **Mission Console** | Vanilla HTML5 / CSS3 / ES Modules, Three.js | `6931` | 3D WebGL globe, orbit rendering, timeline scrubber, 5-stage pipeline UI, human approval gate. |
| **Unified Orchestrator** | Python Subprocess & Signal Manager | — | Single-command launcher (`run_backend.py`) for all 4 microservices with auto-restart and venv detection. |
| **Database** | SQLite (Local fallback) / PostgreSQL (Supabase)| — | Persistent storage for objects, TLEs, conjunction records, and maneuver candidates. |

---

## 🚀 Quick Start Guide

### Prerequisites
- **Python 3.11+** or **Python 3.12+**
- Git

### 1. Clone & Set Up Virtual Environment

```bash
git clone https://github.com/MadhurSikarwar/GDGSpaceTech.git
cd "Space Tech - GDG"

# Create and activate a virtual environment
python -m venv .venv

# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables (Optional)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
*To enable AI-generated natural language decision justifications, add your free Groq API key in `.env`:*
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
```
*(If omitted, OrbitalGuard automatically uses high-accuracy deterministic analytical explanations).*

### 3. Launch the Backend Multi-Agent System

Start all 4 microservices concurrently with the unified launcher:

```bash
python run_backend.py
```

The launcher will auto-resolve your `.venv`, spin up the 4 microservices on their designated ports, monitor their health, and handle clean shutdowns on `Ctrl+C`:
```text
============================================================
      ORBITALGUARD MULTI-AGENT BACKEND SYSTEM       
============================================================
[*] Starting Tracking & Screening     on http://localhost:8000
[*] Starting Risk Assessment Agent    on http://localhost:8001
[*] Starting Maneuver Options Agent   on http://localhost:8002
[*] Starting Decision Optimizer Agent on http://localhost:8003
============================================================
All 4 services online! Press Ctrl+C to shut down all agents.
```

### 4. Launch the 3D Mission Console

In a second terminal window, run the frontend server:

```bash
python -m http.server 6931 --directory frontend
```

Open your browser and navigate to:
👉 **[http://localhost:6931](http://localhost:6931)**

---

## 📡 API Reference Overview

Interactive OpenAPI (Swagger) documentation is available for each microservice:
- **Tracking & Screening API**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Risk Assessment API**: [http://localhost:8001/docs](http://localhost:8001/docs)
- **Maneuver Options API**: [http://localhost:8002/docs](http://localhost:8002/docs)
- **Decision Optimizer API**: [http://localhost:8003/docs](http://localhost:8003/docs)

### Primary Endpoints

| Method | Endpoint | Port | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/health` | `8000` | Health status, database connection, and tracked object count. |
| `GET` | `/api/v1/space-weather` | `8000` | Live NOAA solar flux (F10.7), Kp, Ap, and drag activity scalar. |
| `GET` | `/api/v1/objects` | `8000` | Paginated catalog with real-time TEME position and velocity states. |
| `GET` | `/api/v1/objects/{id}/trajectory` | `8000` | 90-minute future ephemeris trajectory points for 3D orbit lines. |
| `GET` | `/api/v1/objects/{id}/ground-station-passes`| `8000` | AOS/LOS pass windows for Svalbard, Fairbanks, and McMurdo. |
| `POST` | `/api/v1/screen` | `8000` | Executes 2-stage coarse and fine screening on catalog pairs. |
| `GET` | `/api/v1/conjunctions` | `8000` | List detected close-approach conjunction candidates with $P_c$. |
| `POST` | `/api/v1/demo/inject-synthetic` | `8000` | Injects synthetic debris with guaranteed target encounter geometry. |
| `WS` | `/api/v1/telemetry/ws` | `8000` | Real-time WebSocket feed broadcasting discrete catalog updates and conjunction alerts. |
| `POST` | `/assess-risk` | `8001` | Calculates deterministic hazard score (0-100) and risk tier. |
| `POST` | `/generate-maneuvers` | `8002` | Computes CW-propagated SLSQP fuel-optimal and directional burns. |
| `POST` | `/optimize-decision` | `8003` | Evaluates maneuver trade-offs and generates Groq LLM justification. |

---

## 🧪 Verification & Testing Suite

OrbitalGuard includes comprehensive unit, integration, and cross-agent contract tests:

```bash
# Run entire test suite across all agents and contracts
python -m pytest tests/ services/ -v
```

Test coverage verifies:
- Mathematical accuracy of Foster 2D encounter-plane $P_c$ quadrature.
- SGP4 ephemeris propagation against reference orbital data.
- Clohessy-Wiltshire state transition matrices and RIC burn coordinate transformations.
- SciPy SLSQP optimization convergence and constraint satisfaction.
- Pydantic v2 schema serializations and cross-service JSON contracts.
- Database operations and repository fallback behaviors.

---

## 📂 Repository Structure

```text
Space Tech - GDG/
├── frontend/                        # Interactive 3D Mission Console
│   ├── css/
│   │   └── main.css                 # Aerospace HUD theme tokens, layouts & animations
│   ├── js/
│   │   ├── api.js                   # Client API with live connectivity & graceful fallbacks
│   │   ├── globe.js                 # Three.js 3D Earth, orbits, depth occlusion & lighting
│   │   ├── main.js                  # Application lifecycle, boot sequence & HUD events
│   │   ├── state.js                 # Reactive state store & event pub/sub bus
│   │   ├── icons.js                 # Hand-crafted SVG aerospace iconography
│   │   └── panels/                  # Modular UI panel renderers
│   │       ├── topbar.js            # Dual clocks (UTC/IST), status strip & NOAA badge
│   │       ├── catalog.js           # Object list, live filters, search & selection
│   │       ├── conjunctions.js      # Conjunction candidate cards & risk badges
│   │       └── pipeline.js          # 5-stage decision suite & Human Approval Gate
│   ├── data/                        # Offline fallback fixtures
│   └── index.html                   # Mission Console DOM structure & Three.js imports
├── services/
│   ├── propagation/                 # Agent 1 & 2: Tracking & Screening Service (:8000)
│   │   ├── app/
│   │   │   ├── api/routes.py        # REST endpoints with caching & pagination
│   │   │   ├── database/            # SQLAlchemy models, sessions & migrations
│   │   │   ├── ingestion/           # CelesTrak TLE/OMM HTTP client & parser
│   │   │   ├── physics/             # Foster 2D Pc, RIC covariance & ground stations
│   │   │   ├── propagation/         # Skyfield/SGP4 TEME propagation engine
│   │   │   ├── screening/           # 2-Stage coarse/fine trajectory screening
│   │   │   ├── spaceweather/        # NOAA SWPC live solar flux & geomagnetic client
│   │   │   └── synthetic/           # Deterministic synthetic debris orbital generator
│   │   └── tests/                   # Unit & integration tests for physics & screening
│   ├── risk/                        # Agent 3: Risk Assessment Agent (:8001)
│   │   ├── app/                     # Hazard scoring formula & risk classification
│   │   └── tests/                   # Risk model unit tests
│   ├── maneuver/                    # Agent 4: Maneuver Options Agent (:8002)
│   │   ├── app/                     # CW relative motion & SciPy SLSQP delta-v optimizer
│   │   └── tests/                   # Maneuver physics & optimization tests
│   └── optimizer/                   # Agent 5: Decision Optimizer Agent (:8003)
│       └── app/                     # Decision trade-off analysis & Groq LLM integration
├── shared/
│   └── schemas/                     # Cross-agent canonical Pydantic v2 data contracts
│       ├── conjunction.py           # ConjunctionCandidate schema with Pc & relative vectors
│       ├── decision.py              # ManeuverDecision & operator rationale schema
│       ├── ground_station.py        # GroundStation & pass visibility window schema
│       ├── maneuver.py              # ManeuverCandidates & RIC delta-v vector schema
│       ├── object.py                # OrbitalObject & TLE metadata schema
│       ├── risk.py                  # RiskAssessment & hazard tier schema
│       ├── space_weather.py         # NOAA SpaceWeatherSnapshot schema
│       ├── state.py                 # StateVector (Cartesian position/velocity) schema
│       └── trajectory.py            # 90-minute Trajectory & Ephemeris points schema
├── orbitalguard.db                  # Local SQLite database pre-seeded with 10,880+ objects
├── run_backend.py                   # Unified multi-agent concurrent backend orchestrator
├── requirements.txt                 # Core Python dependencies
├── docker-compose.yml               # Container deployment specification
├── .env.example                     # Environment template configuration
└── README.md                        # Master Project Documentation
```

---

## 👥 Contributors & Acknowledgements

Developed with passion for the **Space Domain Awareness & Traffic Management Community**. Special thanks to **CelesTrak**, **Space-Track**, **NOAA Space Weather Prediction Center**, and the **Skyfield Astrodynamics** community for open data feeds and foundational orbital mechanics tooling.

---

<div align="center">
<b>OrbitalGuard — Keeping the Orbital Highway Safe for Humanity's Future.</b>
</div>
