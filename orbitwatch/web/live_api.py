"""Live updates, the event log, and system status / data provenance.

/api/stream is a Server-Sent Events stream. It pushes event-log rows visible to
the user's role as they are written (by the web process or the scheduler: the
stream reads MySQL, so it works across processes), the user's unacknowledged
alert count when it changes, and a heartbeat. EventSource reconnects on its own
and resumes from Last-Event-ID; each stream ends after STREAM_SECONDS so worker
threads are recycled. Pages fall back to polling /api/events if the stream fails.
"""
import json
import time

from flask import Blueprint, Response, request, stream_with_context

from orbitwatch import config, db, events
from orbitwatch.web.common import clean, current_user, int_arg, memo, ok, role_required

bp = Blueprint("live_api", __name__, url_prefix="/api")

STREAM_SECONDS = 300
POLL_SECONDS = 2.0
HEARTBEAT_SECONDS = 15


def _sse(event, data, event_id=None):
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _latest_id(role):
    rows = events.recent(role, limit=1)
    return rows[0]["log_id"] if rows else 0


@bp.get("/stream")
def stream():
    user = current_user()
    role = user["role"] if user else "viewer"
    uid = user["user_id"] if user else None
    try:
        last = int(request.headers.get("Last-Event-ID") or request.args.get("after") or 0)
    except ValueError:
        last = 0
    if last <= 0:
        last = _latest_id(role)
    acct = role

    def gen():
        nonlocal last
        yield "retry: 4000\n\n"
        yield _sse("hello", {"server_time": time.time(), "role": role, "after": last})
        deadline, ping, alerts = time.time() + STREAM_SECONDS, time.time(), None
        while time.time() < deadline:
            try:
                for r in events.recent(role, after_id=last, limit=50):
                    last = r["log_id"]
                    yield _sse("log", clean(r), last)
                if uid:
                    n = db.query_one(acct, "SELECT COUNT(*) AS n FROM alert WHERE user_id = %s AND NOT acknowledged",
                                     (uid,))["n"]
                    if n != alerts:
                        alerts = n
                        yield _sse("alerts", {"unacknowledged": n})
            except Exception as exc:  # noqa: BLE001 - tell the page, let EventSource reconnect
                yield _sse("error", {"message": f"stream interrupted: {type(exc).__name__}"})
                return
            if time.time() - ping >= HEARTBEAT_SECONDS:
                ping = time.time()
                yield ": heartbeat\n\n"
            time.sleep(POLL_SECONDS)
        yield _sse("bye", {"reconnect": True})

    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


@bp.get("/events")
def event_log():
    """The event log visible to this user's role (newest first, or after=<id> for newer ones)."""
    user = current_user()
    role = user["role"] if user else "viewer"
    cats = [c for c in request.args.get("category", "").split(",") if c in events.CATEGORIES]
    rows = events.recent(role, after_id=int_arg("after", 0), limit=min(int_arg("limit", 100), 500), categories=cats)
    return ok({"items": clean(rows), "role": role})


def coverage(account="viewer"):
    return memo(("coverage", account), lambda: _coverage(account))


def _coverage(account):
    row = db.query_one(account, """
        SELECT COUNT(*) AS objects_in_orbit,
               SUM(co.norad_id IS NOT NULL) AS with_orbit,
               SUM(co.source = 'CelesTrak') AS from_celestrak,
               SUM(co.source = 'Space-Track') AS from_spacetrack,
               SUM(co.epoch > UTC_TIMESTAMP() - INTERVAL 3 DAY) AS fresh_3d,
               SUM(co.epoch <= UTC_TIMESTAMP() - INTERVAL 3 DAY AND co.epoch > UTC_TIMESTAMP() - INTERVAL 14 DAY) AS aging,
               SUM(co.epoch <= UTC_TIMESTAMP() - INTERVAL 14 DAY) AS stale_14d,
               SUM(co.norad_id IS NULL AND so.data_status = 'NEA') AS no_elements_published,
               SUM(co.norad_id IS NULL AND COALESCE(so.data_status, '') <> 'NEA') AS elements_missing,
               MAX(co.fetched_at) AS last_fetched, MAX(co.epoch) AS newest_epoch
          FROM space_object so LEFT JOIN current_orbit co ON co.norad_id = so.norad_id
         WHERE so.in_earth_orbit""")
    by_type = db.query(account, """
        SELECT so.object_type, COUNT(*) AS total, COUNT(co.norad_id) AS with_orbit
          FROM space_object so LEFT JOIN current_orbit co ON co.norad_id = so.norad_id
         WHERE so.in_earth_orbit GROUP BY so.object_type ORDER BY total DESC""")
    out = clean(row)
    out["by_type"] = clean(by_type)
    total = out["objects_in_orbit"] or 0
    out["coverage_pct"] = round(100.0 * (out["with_orbit"] or 0) / total, 1) if total else 0.0
    return out


def sources_status(account="viewer"):
    from orbitwatch.jobs import spacetrack
    rows = db.query(account, """
        SELECT source_key, name, provider, url, description, expected_interval_hours, requires_credentials,
               last_attempt_at, last_success_at, last_status, last_records, last_message,
               TIMESTAMPDIFF(MINUTE, last_success_at, NOW(3)) AS age_min
          FROM data_source ORDER BY requires_credentials, source_key""")
    for r in rows:
        exp = r["expected_interval_hours"]
        if r["source_key"].startswith("spacetrack") and not spacetrack.configured():
            r["freshness"] = "not configured"
        elif r["last_success_at"] is None:
            r["freshness"] = "never"
        elif exp is None:
            r["freshness"] = "archive"
        elif r["age_min"] <= float(exp) * 60 * 1.5:
            r["freshness"] = "fresh"
        else:
            r["freshness"] = "stale"
    return clean(rows)


@bp.get("/provenance")
def provenance():
    return ok({"sources": sources_status(), "coverage": coverage()})


@bp.get("/system/status")
def system_status():
    """Everything the dashboard's health panel needs, from the canonical tables."""
    from orbitwatch.jobs import reentry, scheduler, spacetrack
    user = current_user()
    sched = scheduler.status()
    out = {
        "server_time": time.time(),
        "scheduler": clean({"running": sched["running"], "heartbeat": sched["heartbeat"] or {}}),
        "jobs": clean(sched["jobs"]),
        "sources": sources_status(),
        "coverage": coverage(),
        "reentry": clean({k: v for k, v in reentry.status().items() if k != "models"}),
        "spacetrack_configured": spacetrack.configured(),
        "email_delivery": config.smtp_configured(),
        "history_element_sets": _history_count(),
    }
    if user and user["role"] == "admin":
        from orbitwatch import notify
        out["email_queue"] = clean(notify.due_summary() or {})
    return ok(out)


def _history_count():
    try:
        return db.mongo_db("analyst").orbit_history.estimated_document_count()
    except Exception:  # noqa: BLE001 - status must load even if MongoDB is down
        return None


@bp.get("/reentry/models")
def reentry_models():
    from orbitwatch.jobs import reentry
    return ok(clean(reentry.status()))


@bp.post("/admin/email/test")
@role_required("admin")
def email_test():
    """Queue a test message to the administrator's own address (verifies SMTP end to end)."""
    from orbitwatch import notify
    user = current_user()
    notify.enqueue("test", user["email"], "[OrbitWatch] Test e-mail",
                   f"Hello {user['name']},\n\nThis is a test message from OrbitWatch. E-mail delivery works.\n",
                   user_id=user["user_id"], account="admin")
    notify.dispatch_soon()
    return ok({"queued": True, "email_delivery": config.smtp_configured()}, 202)
