"""Rate limits shared by every web process (MySQL rate_limit_event, written through the auth account).

A limit is "at most N hits in the last W seconds" per bucket, e.g. failed logins
per e-mail+address. Counting in MySQL means a restart or a second web process
does not reset anybody's allowance.
"""
import logging

from orbitwatch import db

log = logging.getLogger(__name__)

LIMITS = {                   # name: (max hits, window seconds)
    "login": (8, 600),       # failed logins per e-mail + address
    "login_ip": (30, 900),   # failed logins per address, any e-mail (one password tried against many accounts)
    "login_email": (20, 900),  # failed logins per e-mail, any address (one account tried from many addresses)
    "register": (10, 3600),  # registrations per address
    "forgot_ip": (5, 3600),  # reset requests per address
    "forgot_email": (3, 3600),
    "reset": (10, 3600),     # reset attempts (token submissions) per address
    "password": (5, 900),    # wrong current password per user
    "push_test": (5, 600),   # test notifications per user (each one is an outbound request)
    "challenge_used": (1, 900),  # a solved login challenge is accepted once (see challenge.py)
    "login_notice": (1, 3600),   # at most one "someone is trying your account" e-mail per account and hour
}


def _bucket(name, key):
    return f"{name}:{key}"[:190]


def count(name, key):
    """Hits in the bucket's window (0 when the check itself fails: fail open, but say so)."""
    _, window = LIMITS[name]
    try:
        row = db.query_one("auth", "SELECT COUNT(*) AS n FROM rate_limit_event WHERE bucket = %s "
                                   "AND occurred_at > NOW(3) - INTERVAL %s SECOND", (_bucket(name, key), window))
        return row["n"]
    except Exception as exc:  # noqa: BLE001
        log.warning("rate-limit check failed (%s): %s", name, exc)
        return 0


def exceeded(name, key):
    return count(name, key) >= LIMITS[name][0]


def hit(name, key):
    try:
        db.execute("auth", "INSERT INTO rate_limit_event (bucket) VALUES (%s)", (_bucket(name, key),))
    except Exception as exc:  # noqa: BLE001
        log.warning("rate-limit hit failed (%s): %s", name, exc)


def clear(name, key):
    try:
        db.execute("auth", "DELETE FROM rate_limit_event WHERE bucket = %s", (_bucket(name, key),))
    except Exception as exc:  # noqa: BLE001
        log.warning("rate-limit clear failed (%s): %s", name, exc)
