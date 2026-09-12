import sys
import os
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from dotenv import load_dotenv
load_dotenv(root_dir / ".env")

# Ensure UTF-8 console output for Windows terminals
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import json
from fastapi.testclient import TestClient
from services.maneuver.app.main import app as maneuver_app
from services.optimizer.app.main import app as optimizer_app

def run_demo():
    print("=" * 70)
    print("[OrbitalGuard] Block 2 & Block 3 Live Pipeline Demonstration")
    print("=" * 70)

    maneuver_client = TestClient(maneuver_app)
    optimizer_client = TestClient(optimizer_app)

    # 1. Simulated High-Risk Conjunction (from Risk Agent)
    risk_assessment = {
        "conjunction_id": "CONJ-20260912-ISS-01",
        "risk_score": 88.5,
        "risk_level": "CRITICAL",
        "factors": {
            "closest_approach_km": 1.8,
            "time_to_tca_minutes": 55.0,
            "relative_velocity_km_s": 7.6
        },
        "uncertainty": {}
    }

    print("\n[Step 1] Inbound Hazard from Risk Agent:")
    print(f"  * Conjunction ID: {risk_assessment['conjunction_id']}")
    print(f"  * Miss Distance : {risk_assessment['factors']['closest_approach_km']} km")
    print(f"  * Time to TCA   : {risk_assessment['factors']['time_to_tca_minutes']} mins")
    print(f"  * Risk Tier     : {risk_assessment['risk_level']}")

    # 2. Maneuver Agent (Block 2)
    print("\n[Step 2] Maneuver Agent Simulating Avoidance Burns...")
    m_resp = maneuver_client.post("/generate-maneuvers", json=risk_assessment)
    candidates_payload = m_resp.json()

    print(f"\n{'ID':<5} {'Direction':<12} {'Delta-V (m/s)':<15} {'Separation (km)':<18} {'Resulting Risk':<12}")
    print("-" * 65)
    for c in candidates_payload["candidates"]:
        print(f"{c['maneuver_id']:<5} {c['burn_direction']:<12} {c['delta_v_m_s']:<15.2f} {c['new_separation_km']:<18.2f} {c['resulting_risk']:<12}")

    # 3. Optimizer Agent (Block 3)
    print("\n[Step 3] Optimizer Agent Selecting Optimal Trade-off & Calling Groq LLM...")
    o_resp = optimizer_client.post("/optimize-decision", json=candidates_payload)
    decision = o_resp.json()

    print("\n" + "=" * 70)
    print("OPTIMIZER DECISION CONTRACT (Block 3 Output)")
    print("=" * 70)
    print(f"  * Recommended Burn ID : {decision['decision']['recommended_maneuver_id']}")
    print(f"  * Post-Burn Distance  : {decision['simulation']['new_tca_distance_km']} km")
    print(f"  * Human Gate Required : {decision['human_approval_required']}")
    print(f"  * LLM Justification   :\n    \"{decision['decision']['reason']}\"")
    print("=" * 70)

if __name__ == "__main__":
    run_demo()
