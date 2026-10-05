# Legacy: OrbitalGuard

`orbitalguard/` is the original **OrbitalGuard** project (GDG space-tech hackathon), restored
file-for-file from git when the repository became OrbitWatch, so that nothing is lost:
FastAPI microservices (propagation, risk, maneuver, optimizer), the LLM decision agent, the
Three.js mission console and its tests.

Its capabilities are rebuilt inside OrbitWatch on OrbitWatch's own MySQL/MongoDB data: the agentic
decision support, Foster probability of collision, the Clohessy–Wiltshire delta-v optimizer, NOAA
space weather, ground-station passes, manoeuvre approval with the simulated burn, the synthetic-debris
demo, live updates, the guided tour, the glossary and the event log (see the main README, "Beyond the SRS").

The team's onboarding document `HANDOFF_CONTEXT.md` stays on the `tracking-screening` branch (the
repository's `.gitignore` excludes it on purpose); every other branch's work is contained in this one.

OrbitalGuard's Supabase project no longer exists (its domain does not resolve). `orbitalguard.db` in the
repository root and its earlier versions in git history are what survives of its data; `ow reconcile`
imports the real parts of it into OrbitWatch and reports the rest.

To run OrbitalGuard itself as before (it uses its own Supabase database and Python environment):

```bash
cd legacy/orbitalguard
pip install -r requirements.txt
python run_backend.py
```

It reads its settings from a `.env` in the directory it is started from (see `orbitalguard/.env.example`),
and serves its UI with `python -m http.server 6931 --directory frontend`.
