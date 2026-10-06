import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The suite changes the database and reads it straight back, so the web process memo of dashboard aggregates is off
# here (tests/test_catalogue_queries.py tests the memo itself).
os.environ.setdefault("ORBITWATCH_READ_CACHE_S", "0")

# The suite must never log in to the real mail account in .env. With the credentials set, every password-change and
# reset message of its @example.test accounts went out through Gmail and bounced into the owner's inbox. (The tests that
# exercise delivery point the sender at a local sink themselves.)
os.environ["SMTP_USERNAME"] = ""
os.environ["SMTP_PASSWORD"] = ""


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


@pytest.fixture(scope="session", autouse=True)
def _no_real_mail():
    """A second lock after the empty credentials: opening an SMTP connection to any machine but this one fails the test."""
    import smtplib
    real = smtplib.SMTP.connect

    def guarded(self, host="localhost", port=0, source_address=None):
        if str(host).strip().lower() not in ("127.0.0.1", "localhost", "::1", ""):
            raise AssertionError(f"the test suite tried to open an SMTP connection to {host}")
        return real(self, host, port, source_address)

    smtplib.SMTP.connect = guarded
    yield
    smtplib.SMTP.connect = real
