# Space Tech - GDG

This project is built using the following technology stack:

## Technology Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| **Orbital Data** | CelesTrak | TLE/OMM orbital data |
| **Backup Data** | Space-Track | Secondary orbital data source |
| **Orbital Propagation** | **Skyfield + SGP4** | Calculate satellite/debris positions & velocities |
| **Backend — Tracking** | **Python** | Tracking + screening logic |
| **Backend API** | **FastAPI** | Expose tracking/conjunction data |
| **Database** | **PostgreSQL** | Store objects, TLEs, conjunctions |
| **DB ORM** | SQLAlchemy | Python ↔ PostgreSQL |
| **Fallback DB** | SQLite | Local/offline hackathon mode |
| **HTTP Client** | Requests / HTTPX | Fetch CelesTrak data |
| **Risk Agent** | Python | Risk scoring |
| **Maneuver Agent** | Python | Generate/simulate avoidance options |
| **AI/LLM Layer** | Gemini | Agent reasoning / natural-language alerts |
| **Frontend** | Next.js / React | Dashboard |
| **Visualization** | 2D ground-track map | Orbital visualization |
| **Styling** | Tailwind CSS | UI |
| **Testing** | Pytest | Automated testing |
| **Deployment** | Vercel + Render/Railway | Frontend/backend deployment |
| **Containerization** | Docker | Consistent deployment environment |
