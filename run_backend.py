"""
OrbitalGuard Multi-Agent Unified Backend Launcher
Starts all 4 agent microservices concurrently and handles clean shutdown on Ctrl+C.
"""
import os
import sys
import time
import subprocess
import signal
from typing import List

# Auto-detect .venv in project root and switch to it if running under system Python
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
if os.path.exists(VENV_PYTHON) and os.path.normcase(sys.executable) != os.path.normcase(VENV_PYTHON):
    sys.exit(subprocess.call([VENV_PYTHON] + sys.argv))

SERVICES = [
    {"name": "Tracking & Screening", "module": "services.propagation.app.main:app", "port": 8000},
    {"name": "Risk Assessment Agent", "module": "services.risk.app.main:app", "port": 8001},
    {"name": "Maneuver Options Agent", "module": "services.maneuver.app.main:app", "port": 8002},
    {"name": "Decision Optimizer Agent", "module": "services.optimizer.app.main:app", "port": 8003},
]

processes = {}


def start_service(svc):
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        svc["module"],
        "--host",
        "0.0.0.0",
        "--port",
        str(svc["port"])
    ]
    print(f"[*] Starting {svc['name']:<25} on http://localhost:{svc['port']}")
    return subprocess.Popen(cmd)


def stop_all(sig=None, frame=None):
    print("\n[OrbitalGuard] Stopping all agent services...")
    for name, p in processes.items():
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    time.sleep(1)
    for name, p in processes.items():
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    print("[OrbitalGuard] All agent services stopped cleanly.")
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    print("=" * 60)
    print("      ORBITALGUARD MULTI-AGENT BACKEND SYSTEM       ")
    print("=" * 60)

    for svc in SERVICES:
        proc = start_service(svc)
        processes[svc["name"]] = proc
        time.sleep(0.5)

    print("=" * 60)
    print("All 4 services online! Press Ctrl+C to shut down all agents.")
    print("=" * 60)

    try:
        while True:
            for svc in SERVICES:
                name = svc["name"]
                p = processes.get(name)
                if p and p.poll() is not None:
                    print(f"[!] {name} on port {svc['port']} exited (code {p.returncode}). Restarting in 2s...")
                    time.sleep(2)
                    processes[name] = start_service(svc)
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()


if __name__ == "__main__":
    main()
