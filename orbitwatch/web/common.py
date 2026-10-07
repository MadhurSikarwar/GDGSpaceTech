"""Shared helpers for the API: current user, role checks, database account choice, CSV export."""
import csv
import io
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import Response, abort, g, jsonify, request, session

from orbitwatch import auth, config

ROLE_RANK = {"viewer": 1, "analyst": 2, "admin": 3}


def current_user():
    """The logged-in user (role re-read from MySQL once per request), or None for an anonymous visitor."""
    if "user" in g:
        return g.user
    g.user = None
    uid = session.get("user_id")
    if uid:
        row = auth.refresh(uid)
        # A password change or reset bumps session_version: every older session ends here.
        if row and row["is_active"] and session.get("sv", 1) == row["session_version"]:
            g.user = {"user_id": row["user_id"], "name": row["name"], "email": row["email"], "role": row["role"]}
        else:
            session.clear()
    return g.user


def account():
    """MySQL account for this request: Public Viewer for visitors, otherwise the user's role."""
    user = current_user()
    return user["role"] if user else "viewer"


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            abort(401)
        return fn(*args, **kwargs)
    return wrapper


def role_required(role):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                abort(401)
            if ROLE_RANK[user["role"]] < ROLE_RANK[role]:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


def require_analyst():
    """Exports are an analyst function (SRS 3.7)."""
    user = current_user()
    if user is None:
        abort(401)
    if ROLE_RANK[user["role"]] < ROLE_RANK["analyst"]:
        abort(403, description="Exporting results requires the Analyst role.")


def to_json(value):
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def clean(rows):
    """JSON-ready copy of a row, a list of rows, or any nesting of them (ISO 8601 UTC datetimes throughout)."""
    if isinstance(rows, dict):
        return {k: clean(v) if isinstance(v, (dict, list, tuple)) else to_json(v) for k, v in rows.items()}
    if isinstance(rows, (list, tuple)):
        return [clean(v) if isinstance(v, (dict, list, tuple)) else to_json(v) for v in rows]
    return to_json(rows)


_memo = {}
_memo_gates = {}
_memo_lock = threading.Lock()
_memo_generation = 0
_MEMO_MAX = 300          # results kept at most: some keys come from the request (a search term, a date), so they must not pile up


def _memo_prune():
    """Drop what has expired and the locks of keys that hold no result. Call with _memo_lock held."""
    now = time.monotonic()
    for k in [k for k, h in _memo.items() if now - h[0] >= h[2]]:
        del _memo[k]
    for k in [k for k in _memo_gates if k not in _memo]:
        del _memo_gates[k]


def memo(key, build, factor=1.0):
    """build()'s result, reused for READ_CACHE_S * factor seconds (0 = always build).

    The dashboard counts, the filter lookups and the data coverage each scan tens of thousands of rows (0.2 to 0.6 s)
    but change only when a job or an administrator changes data. One build runs at a time per key: requests that
    arrive meanwhile wait and share its result instead of repeating the scan. memo_clear() drops everything. The
    returned object is shared between requests: copy it before changing it.
    """
    ttl = config.READ_CACHE_S * factor
    if ttl <= 0:
        return build()
    hit = _memo.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    with _memo_lock:
        if len(_memo_gates) > 2 * _MEMO_MAX:
            _memo_prune()
        gate = _memo_gates.setdefault(key, threading.Lock())
    with gate:
        hit = _memo.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        generation = _memo_generation
        value = build()
        if generation == _memo_generation:        # not if a write cleared the memo while this was being built
            with _memo_lock:
                if len(_memo) >= _MEMO_MAX and key not in _memo:
                    _memo_prune()
                if len(_memo) < _MEMO_MAX or key in _memo:      # a full memo of fresh results keeps them: the newcomer is just not kept
                    _memo[key] = (time.monotonic(), value, ttl)
        return value


def memo_clear():
    global _memo_generation
    with _memo_lock:
        _memo_generation += 1
        _memo.clear()


def ok(payload=None, status=200):
    return jsonify(payload if payload is not None else {"ok": True}), status


def csv_response(rows, filename, columns=None):
    buf = io.StringIO()
    rows = clean(rows)
    columns = columns or (list(rows[0].keys()) if rows else [])
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def wants_csv():
    return request.args.get("format") == "csv"


def page_args(default_size=50, max_size=500):
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(max_size, max(1, int(request.args.get("page_size", default_size))))
    except ValueError:
        abort(400)
    return page, size


def parse_dt(value, field):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        abort(400, description=f"{field}: expected an ISO date/time")


def int_arg(name, default=None):
    value = request.args.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        abort(400, description=f"{name}: expected an integer")


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="expected a JSON object")
    return data
