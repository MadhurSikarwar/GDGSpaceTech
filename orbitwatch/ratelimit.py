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
    "register": (10, 3600),  # registrations per address
    "forgot_ip": (5, 3600),  # reset requests per address
    "forgot_email": (3, 3600),
    "reset": (10, 3600),     # reset attempts (token submissions) per address
    "password": (5, 900),    # wrong current password per user
}


def _bucket(name, key):
    return f"{name}:{key}"[:190]


def exceeded(name, key):
    limit, window = LIMITS[name]
    try:
        row = db.query_one("auth", "SELECT COUNT(*) AS n FROM rate_limit_event WHERE bucket = %s "
                                   "AND occurred_at > NOW(3) - INTERVAL %s SECOND", (_bucket(name, key), window))
        return row["n"] >= limit
    except Exception as exc:  # noqa: BLE001 - fail open on a database hiccup, but say so
        log.warning("rate-limit check failed (%s): %s", name, exc)
        return False


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
