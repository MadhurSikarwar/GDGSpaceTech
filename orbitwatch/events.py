"""The event log: one row per notable thing that happened (MySQL event_log).

It is what the event-log drawer shows and what the live stream (/api/stream)
pushes to open pages. Rows are visible per role through the views
v_event_log_public / v_event_log_analyst; administrators see the table.
Writing an event must never break the operation that caused it.
"""
import json
import logging

from orbitwatch import db

log = logging.getLogger(__name__)

CATEGORIES = ("data", "screening", "alert", "agent", "maneuver", "demo", "auth", "admin", "system", "job")


def record(category, action, message, *, severity="info", visibility="public", actor=None, entity_type=None,
           entity_id=None, detail=None, account="jobs"):
    try:
        _, log_id = db.execute(account, """
            INSERT INTO event_log (category, severity, visibility, actor_user_id, action, entity_type, entity_id,
                                   message, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (category, severity, visibility, actor, action[:60], entity_type, None if entity_id is None else str(entity_id),
             message[:500], json.dumps(detail, default=str) if detail is not None else None))
        return log_id
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write event_log (%s %s): %s", category, action, exc)
        return None


def recent(role, after_id=0, limit=100, categories=None):
    """Events visible to a role, newest first (after_id > 0: only newer ones, oldest first)."""
    if role == "admin":
        source, cols, account = "event_log", ("log_id, occurred_at, category, severity, visibility, action, entity_type, "
                                              "entity_id, message, detail, actor_user_id"), "admin"
    elif role == "analyst":
        source, cols, account = "v_event_log_analyst", "*", "analyst"
    else:
        source, cols, account = "v_event_log_public", "*", "viewer"
    where, params = ["log_id > %s"], [after_id]
    if categories:
        where.append(f"category IN ({','.join(['%s'] * len(categories))})")
        params += list(categories)
    order = "ASC" if after_id else "DESC"
    rows = db.query(account, f"SELECT {cols} FROM {source} WHERE {' AND '.join(where)} "
                             f"ORDER BY log_id {order} LIMIT %s", (*params, limit))
    for r in rows:
        if isinstance(r.get("detail"), str):
            try:
                r["detail"] = json.loads(r["detail"])
            except ValueError:
                pass
    return rows
