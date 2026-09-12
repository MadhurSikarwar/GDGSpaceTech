# OrbitalGuard — Tracking & Screening Service (Agent 1 & Agent 2)

**Tagline**: TRACK. PREDICT. AVOID.  
**Role**: Core Orbital Intelligence Foundation & Multi-Agent Contract Platform

## Architecture & Responsibilities
This service ingests orbital elements (TLE / OMM), propagates satellite state vectors $(\mathbf{r}, \mathbf{v})$ using SGP4 in the TEME reference frame, computes 90-minute trajectories, executes a two-stage screening process (altitude-band coarse filter + SGP4 fine separation scan with local refinement), and outputs standardized `ConjunctionCandidate` records for Risk, Maneuver, and Frontend agents.

## Quick Start

### 1. Installation
```bash
pip install -r services/propagation/requirements.txt
```

### 2. Running Locally
Set `PYTHONPATH` to repository root and start Uvicorn:
```bash
python -m uvicorn services.propagation.app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive OpenAPI documentation will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

### 3. API Contract Routes (`/api/v1/`)
- `GET /api/v1/health` — System status, DB connection mode, offline mode.
- `POST /api/v1/ingest` — Ingest orbital data from CelesTrak (with automatic offline cache fallback).
- `GET /api/v1/objects` — List tracked objects (canonical `OrbitalObject` schema).
- `GET /api/v1/objects/{id}` — Single object details & real-time propagated state vector.
- `GET /api/v1/objects/{id}/trajectory` — 90-minute trajectory array.
- `POST /api/v1/screen` — Run 2-stage screening engine.
- `GET /api/v1/conjunctions` — List potential close approach candidates (`ConjunctionCandidate`).
- `POST /api/v1/demo/inject-synthetic` — Inject `SYNTHETIC_DEBRIS` targeting ISS to guarantee a reproducible conjunction.

### 4. Running Unit Tests
```bash
pytest services/propagation/tests/
```
