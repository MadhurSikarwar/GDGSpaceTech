# 🖥️ OrbitalGuard Mission Console

Interactive 3D orbital intelligence dashboard and multi-agent conjunction decision suite. Static HTML/CSS/JS (ES modules) + Three.js — no build step, no framework, no Node dependency.

## Run it

```bash
python -m http.server 6931 --directory frontend
```

Then open **http://localhost:6931**. (Port 6931 is deliberately unusual so it won't collide with other local projects — change it in `.claude/launch.json` / the command above if you need to.)

For live data, also run the Tracking & Screening backend on its documented port:

```bash
python -m uvicorn services.propagation.app.main:app --host 0.0.0.0 --port 8000
```

The frontend works with **zero backend running** — it falls back to bundled fixtures in `frontend/data/` and clearly badges everything as offline/simulated. It automatically upgrades to live data the moment a service becomes reachable (checked on load and every 15s).

## What's here

- **Orbit view** — a real 3D globe (Three.js) driven by actual backend state vectors and 90-minute SGP4 trajectories, not a mockup. Drag to orbit, scroll to zoom, click any object to select it, scrub/play its real propagated trajectory on the timeline.
- **Conjunctions view** — flagged close-approach candidates from `/api/v1/conjunctions`, risk-badged as soon as the Risk Agent (or its fallback) scores them.
- **Pipeline view** — the actual multi-agent decision chain made visible and interactive: Screening → Risk → Maneuver → Optimizer → **human approval gate**. Approving a maneuver re-plots a projected divergent path on the globe. Nothing here ever claims to command real hardware — that's the point of the human gate.
- **Inject Synthetic Debris** button — calls `POST /api/v1/demo/inject-synthetic` and walks the whole pipeline automatically. Built for the live demo moment.

## Graceful degradation by design

Risk, Maneuver, and Optimizer are teammate-owned services (`:8001`/`:8002`/`:8003`) that may not be running, or may not have CORS configured yet. Every call to those three tries the live service first and falls back to a local heuristic in [`js/api.js`](js/api.js) that mirrors the exact contract shape from `shared/schemas/*.py` and the business rules in `INTEGRATION_CONTRACT.md`. Every result is honestly badged **LIVE** or **SIMULATED** — the UI never pretends a fallback is real agent output. This also means the frontend is fully demoable in isolation, before any teammate's service is finished.

## Design notes

Light dashboard shell wrapping a dark 3D viewport — the globe stays a "window into space" (like a dark canvas/map pane inside an otherwise light IDE), while the surrounding chrome (catalog, conjunctions, pipeline, topbar) is a restrained light theme: soft neutral surfaces, hairline borders, monospace telemetry (`JetBrains Mono`) paired with a geometric display face (`Space Grotesk`). Buttons are differentiated by *treatment*, not just hue, so hierarchy reads even in grayscale: ghost (utility), solid teal (primary), solid green (approve), outline red (reject), solid flame (the demo trigger).

All theme tokens live in [`css/main.css`](css/main.css) as CSS custom properties; the globe viewport re-declares them inside `.globe-stage` so it keeps its own dark palette regardless of the shell theme.

## Key files

```
frontend/
├── index.html            # shell + all view markup
├── css/main.css           # design tokens + full styling
├── js/
│   ├── main.js            # bootstrap & orchestration
│   ├── state.js           # tiny observable store
│   ├── api.js              # backend client + fallback heuristics
│   ├── globe.js            # Three.js scene: earth, objects, trajectories, camera
│   ├── icons.js            # hand-rolled inline SVG icon set
│   └── panels/             # topbar, catalog, conjunctions, pipeline renderers
└── data/                  # offline fixtures (copied from services/propagation/data)
```

Objects and conjunctions are keyed by `catalog_id` throughout the frontend (not the opaque DB `object_id`), since that's the identifier `ConjunctionCandidate.primary_object`/`secondary_object` and the trajectory route actually resolve by.
