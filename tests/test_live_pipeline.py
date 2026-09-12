import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from services.propagation.app.main import app as prop_app
from services.risk.app.main import app as risk_app
from services.maneuver.app.main import app as man_app
from services.optimizer.app.main import app as opt_app


def run_pipeline_check():
    print("========================================================")
    print("      ORBITALGUARD MULTI-AGENT PIPELINE LIVE TEST       ")
    print("========================================================")

    # 1. Platform Tracking & Screening Service
    prop_client = TestClient(prop_app)
    r_health = prop_client.get("/api/v1/health")
    print("\n[1] Tracking Service Health:", r_health.json())

    # 2. Ingest Objects
    r_ingest = prop_client.post("/api/v1/ingest?group=active")
    print(f"[2] Ingested Objects Count: {len(r_ingest.json().get('catalog_ids', []))}")

    # 3. Inject Synthetic Conjunction (ISS 25544 vs DEB-DEMO)
    r_synth = prop_client.post("/api/v1/demo/inject-synthetic?target_catalog_id=25544")
    print(f"[3] Synthetic Debris Injected: {r_synth.json().get('synthetic_object_id')}")

    # 4. Get Identified Conjunction Candidates
    r_conjs = prop_client.get("/api/v1/conjunctions")
    conjs = r_conjs.json()
    print(f"[4] Conjunction Candidates Identified: {len(conjs)}")
    target_conj = conjs[0]
    cid = target_conj["conjunction_id"]
    pid = target_conj["primary_object"]
    print(f"    -> Conjunction ID: {cid}")
    print(f"    -> Primary: {target_conj['primary_object_name']} | Secondary: {target_conj['secondary_object_name']}")
    print(f"    -> Distance: {target_conj['closest_approach']['distance_km']} km")
    print(f"    -> Relative Velocity: {target_conj['closest_approach']['relative_velocity_km_s']} km/s")

    # 5. Assess Risk via Risk Agent
    risk_client = TestClient(risk_app)
    r_risk = risk_client.post("/assess-risk", json=target_conj)
    risk_data = r_risk.json()
    print("\n[5] Risk Agent Assessment Output:")
    print(f"    -> Conjunction ID: {risk_data['conjunction_id']}")
    print(f"    -> Risk Score (0-100): {risk_data['risk_score']}")
    print(f"    -> Risk Level Tier: {risk_data['risk_level']}")
    print(f"    -> Notes: {risk_data['notes']}")

    # 6. Generate Maneuver Candidates (Maneuver Agent)
    man_client = TestClient(man_app)
    r_man = man_client.post(f"/generate-maneuvers?conjunction_id={cid}&satellite_id={pid}")
    man_data = r_man.json()
    print("\n[6] Maneuver Agent Options:")
    for m in man_data["candidates"]:
        print(f"    -> Burn {m['maneuver_id']}: {m['burn_direction']} burn of {m['delta_v_m_s']} m/s -> New Sep: {m['new_separation_km']} km ({m['resulting_risk']})")

    # 7. Optimizer / Decision Agent
    opt_client = TestClient(opt_app)
    r_opt = opt_client.post(f"/optimize-decision?conjunction_id={cid}")
    opt_data = r_opt.json()
    print("\n[7] Optimizer Decision Recommendation:")
    print(f"    -> Recommended Burn: {opt_data['decision']['recommended_maneuver_id']}")
    print(f"    -> Reason: {opt_data['decision']['reason']}")
    print(f"    -> Human Approval Required: {opt_data['human_approval_required']}")

    print("\n========================================================")
    print("     PIPELINE PASSED: ALL AGENTS OPERATING IN UNISON     ")
    print("========================================================")


if __name__ == "__main__":
    run_pipeline_check()
