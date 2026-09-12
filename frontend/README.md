# 🖥️ OrbitalGuard Frontend Dashboard

**Role**: Interactive 3D Orbital Intelligence Dashboard & Multi-Agent Conjunction Decision Suite

## API Integration Guide for Teammate
The backend is running at `http://localhost:8000/api/v1`.

### Key Endpoints to Render
- `GET /api/v1/objects`: Render active satellites & debris on 3D globe / map.
- `GET /api/v1/objects/{id}/trajectory`: Render 90-minute orbital trajectory path polyline.
- `GET /api/v1/conjunctions`: Display flagged close approach alerts.
- `POST /api/v1/demo/inject-synthetic`: Trigger synthetic debris injection for live presentation demo.
