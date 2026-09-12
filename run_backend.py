"""
OrbitalGuard Multi-Agent Unified Backend Launcher
Starts all 4 agent microservices concurrently and handles clean shutdown on Ctrl+C.
"""
import sys
import time
import subprocess
import signal
from typing import List

SERVICES = [
    {"name": "Tracking & Screening", "module": "services.propagation.app.main:app", "port": 8000},
    {"name": "Risk Assessment Agent", "module": "services.risk.app.main:app", "port": 8001},
    {"name": "Maneuver Options Agent", "module": "services.maneuver.app.main:app", "port": 8002},
    {"name": "Decision Optimizer Agent", "module": "services.optimizer.app.main:app", "port": 8003},
]

processes: List[subprocess.Popen] = []


def stop_all(sig=None, frame=None):
    print("\n[OrbitalGuard] Stopping all agent services...")
    for p in processes:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    time.sleep(1)
    for p in processes:
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
        proc = subprocess.Popen(cmd)
        processes.append(proc)
        time.sleep(0.5)

    print("=" * 60)
    print("All 4 services online! Press Ctrl+C to shut down all agents.")
    print("=" * 60)

    try:
        while True:
            # Monitor child processes; exit if any unexpected crash occurs
            for p in processes:
                if p.poll() is not None:
                    print(f"[!] A service terminated unexpectedly (exit code {p.returncode}).")
                    stop_all()
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()


if __name__ == "__main__":
    main()
