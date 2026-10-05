# Legacy: OrbitalGuard

`orbitalguard/` is the original **OrbitalGuard** project (GDG space-tech hackathon), restored
file-for-file from git when the repository became OrbitWatch, so that nothing is lost:
FastAPI microservices (propagation, risk, maneuver, optimizer), the LLM decision agent, the
Three.js mission console and its tests.

Its capabilities are rebuilt inside OrbitWatch on OrbitWatch's own MySQL/MongoDB data: the agentic
decision support, Foster probability of collision, the Clohessy–Wiltshire delta-v optimizer, NOAA
space weather and ground-station passes (see the main README, "Beyond the SRS").

To run OrbitalGuard itself as before (it uses its own Supabase database and Python environment):

```bash
cd legacy/orbitalguard
pip install -r requirements.txt
python run_backend.py
```

It reads its settings from a `.env` in the directory it is started from (see `orbitalguard/.env.example`),
and serves its UI with `python -m http.server 6931 --directory frontend`.
