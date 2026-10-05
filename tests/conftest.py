import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session", autouse=True)
def _fresh_rate_limits():
    """Rate limits live in MySQL across runs: earlier runs from the test client's address must not throttle
    this one. (Skipped silently when no database is reachable; the DB tests skip themselves then.)"""
    try:
        from orbitwatch import db
        db.execute("admin", "DELETE FROM rate_limit_event WHERE bucket LIKE %s OR bucket LIKE %s OR bucket LIKE %s "
                            "OR bucket LIKE %s", ("register:127.0.0.1%", "forgot_ip:127.0.0.1%", "reset:127.0.0.1%",
                                                 "forgot_email:%@example.test"))
    except Exception:  # noqa: BLE001
        pass
    yield
