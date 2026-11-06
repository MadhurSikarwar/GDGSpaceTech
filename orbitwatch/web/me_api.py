"""/api/me: subscriptions and alerts of the logged-in user (SRS 3.5).

These run on the user's role account. The Public Viewer role may only
INSERT/DELETE subscriptions and UPDATE the two acknowledgement columns of
alert, so the database itself limits what this blueprint can change.
"""
from flask import Blueprint, abort, request

from orbitwatch import db
from orbitwatch.web.common import account, body, clean, current_user, int_arg, login_required, ok

bp = Blueprint("me_api", __name__, url_prefix="/api/me")


@bp.get("/subscriptions")
@login_required
def subscriptions():
    uid = current_user()["user_id"]
    rows = db.query(account(), """
        SELECT s.norad_id, s.subscribed_on, so.name, so.object_type, so.status,
               (SELECT COUNT(*) FROM alert a JOIN conjunction_event e ON e.event_id = a.event_id
                 WHERE a.user_id = s.user_id AND NOT a.acknowledged
                   AND s.norad_id IN (e.primary_norad, e.secondary_norad)) AS unacknowledged,
               (SELECT MIN(e.time_of_closest_approach) FROM conjunction_event e
                 WHERE s.norad_id IN (e.primary_norad, e.secondary_norad)
                   AND e.time_of_closest_approach >= UTC_TIMESTAMP()) AS next_approach
          FROM subscription s JOIN space_object so ON so.norad_id = s.norad_id
         WHERE s.user_id = %s ORDER BY s.subscribed_on DESC""", (uid,))
    return ok({"items": clean(rows)})


@bp.get("/watchlist")
@login_required
def watchlist():
    """Every object the user follows, with its orbit and each close approach from `days` back to `days` ahead
    (default 7): the data of the My watchlist page. One query for the objects, one for all their events."""
    days = min(max(int_arg("days", 7), 1), 14)
    acct, uid = account(), current_user()["user_id"]
    objects = db.query(acct, """
        SELECT s.norad_id, s.subscribed_on, so.name, so.object_type, so.status,
               co.perigee_km, co.apogee_km, co.inclination, co.epoch
          FROM subscription s JOIN space_object so ON so.norad_id = s.norad_id
          LEFT JOIN current_orbit co ON co.norad_id = s.norad_id
         WHERE s.user_id = %s ORDER BY so.name""", (uid,))
    events = []
    if objects:
        ids = [o["norad_id"] for o in objects]
        marks = ",".join(["%s"] * len(ids))
        events = db.query(acct, f"""
            SELECT event_id, time_of_closest_approach, miss_distance_km, relative_velocity, risk_level,
                   probability_of_collision, primary_norad, primary_name, primary_type,
                   secondary_norad, secondary_name, secondary_type
              FROM v_conjunction_detail
             WHERE (primary_norad IN ({marks}) OR secondary_norad IN ({marks}))
               AND time_of_closest_approach BETWEEN UTC_TIMESTAMP() - INTERVAL %s DAY
                                                AND UTC_TIMESTAMP() + INTERVAL %s DAY
             ORDER BY time_of_closest_approach LIMIT 5000""", (*ids, *ids, days, days))
    by_object = {o["norad_id"]: [] for o in objects}
    for e in events:
        for role, other in (("primary", "secondary"), ("secondary", "primary")):
            n = e[f"{role}_norad"]
            if n in by_object:
                by_object[n].append({"event_id": e["event_id"], "tca": e["time_of_closest_approach"],
                                     "miss_km": e["miss_distance_km"], "rel_vel_kms": e["relative_velocity"],
                                     "risk": e["risk_level"], "pc": e["probability_of_collision"],
                                     "other_norad": e[f"{other}_norad"], "other_name": e[f"{other}_name"],
                                     "other_type": e[f"{other}_type"]})
    items = [{**o, "events": by_object[o["norad_id"]]} for o in objects]
    return ok({"days": days, "items": clean(items)})


@bp.post("/subscriptions")
@login_required
def subscribe():
    try:
        norad_id = int(body().get("norad_id"))
    except (TypeError, ValueError):
        abort(400, description="norad_id must be an integer")
    acct = account()
    if not db.query_one(acct, "SELECT 1 AS x FROM space_object WHERE norad_id = %s", (norad_id,)):
        abort(404, description="No such object")
    db.execute(acct, "INSERT IGNORE INTO subscription (user_id, norad_id) VALUES (%s, %s)",
               (current_user()["user_id"], norad_id))
    return ok({"subscribed": True, "norad_id": norad_id}, 201)


@bp.delete("/subscriptions/<int:norad_id>")
@login_required
def unsubscribe(norad_id):
    db.execute(account(), "DELETE FROM subscription WHERE user_id = %s AND norad_id = %s",
               (current_user()["user_id"], norad_id))
    return ok({"subscribed": False, "norad_id": norad_id})


@bp.get("/alerts")
@login_required
def alerts():
    uid = current_user()["user_id"]
    only_open = request.args.get("status", "unacknowledged") == "unacknowledged"
    rows = db.query(account(), f"""
        SELECT a.alert_id, a.sent_on, a.acknowledged, a.acknowledged_on, d.*
          FROM alert a JOIN v_conjunction_detail d ON d.event_id = a.event_id
         WHERE a.user_id = %s {"AND NOT a.acknowledged" if only_open else ""}
         ORDER BY a.sent_on DESC, d.time_of_closest_approach LIMIT 300""", (uid,))
    return ok({"items": clean(rows)})


@bp.get("/alerts/count")
@login_required
def alert_count():
    row = db.query_one(account(), "SELECT COUNT(*) AS n FROM alert WHERE user_id = %s AND NOT acknowledged",
                       (current_user()["user_id"],))
    return ok({"unacknowledged": row["n"]})


@bp.post("/alerts/<int:alert_id>/ack")
@login_required
def acknowledge(alert_id):
    count, _ = db.execute(account(), "UPDATE alert SET acknowledged = TRUE, acknowledged_on = UTC_TIMESTAMP() "
                                     "WHERE alert_id = %s AND user_id = %s AND NOT acknowledged",
                          (alert_id, current_user()["user_id"]))
    return ok({"acknowledged": count})


@bp.post("/alerts/ack-all")
@login_required
def acknowledge_all():
    count, _ = db.execute(account(), "UPDATE alert SET acknowledged = TRUE, acknowledged_on = UTC_TIMESTAMP() "
                                     "WHERE user_id = %s AND NOT acknowledged", (current_user()["user_id"],))
    return ok({"acknowledged": count})


# ---------------------------------------------------------------------------- notification preferences
DEFAULT_PREFS = {"email_alerts": True, "min_risk_level": "HIGH", "email_decisions": False}


@bp.get("/notifications")
@login_required
def notification_prefs():
    from orbitwatch import config, notify
    row = db.query_one(account(), "SELECT email_alerts, min_risk_level, email_decisions, updated_at "
                                  "FROM notification_pref WHERE user_id = %s", (current_user()["user_id"],))
    prefs = {k: (bool(row[k]) if isinstance(DEFAULT_PREFS[k], bool) else row[k]) for k in DEFAULT_PREFS} \
        if row else dict(DEFAULT_PREFS)
    return ok({"prefs": prefs, "email": current_user()["email"], "email_delivery": config.smtp_configured(),
               "risk_levels": list(notify.RISK_ORDER)})


@bp.put("/notifications")
@login_required
def set_notification_prefs():
    from orbitwatch import notify
    data = body()
    prefs = dict(DEFAULT_PREFS)
    row = db.query_one(account(), "SELECT email_alerts, min_risk_level, email_decisions FROM notification_pref "
                                  "WHERE user_id = %s", (current_user()["user_id"],))
    if row:
        prefs.update(email_alerts=bool(row["email_alerts"]), min_risk_level=row["min_risk_level"],
                     email_decisions=bool(row["email_decisions"]))
    for key in ("email_alerts", "email_decisions"):
        if key in data:
            if not isinstance(data[key], bool):
                abort(400, description=f"{key} must be true or false")
            prefs[key] = data[key]
    if "min_risk_level" in data:
        if data["min_risk_level"] not in notify.RISK_ORDER:
            abort(400, description="min_risk_level must be one of " + ", ".join(notify.RISK_ORDER))
        prefs["min_risk_level"] = data["min_risk_level"]
    db.execute(account(), """INSERT INTO notification_pref (user_id, email_alerts, min_risk_level, email_decisions)
                             VALUES (%s, %s, %s, %s) AS n
                             ON DUPLICATE KEY UPDATE email_alerts = n.email_alerts, min_risk_level = n.min_risk_level,
                                                     email_decisions = n.email_decisions""",
               (current_user()["user_id"], prefs["email_alerts"], prefs["min_risk_level"], prefs["email_decisions"]))
    return ok({"prefs": prefs})
