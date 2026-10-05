"""/api/me: subscriptions and alerts of the logged-in user (SRS 3.5).

These run on the user's role account. The Public Viewer role may only
INSERT/DELETE subscriptions and UPDATE the two acknowledgement columns of
alert, so the database itself limits what this blueprint can change.
"""
from flask import Blueprint, abort, request

from orbitwatch import db
from orbitwatch.web.common import account, body, clean, current_user, login_required, ok

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
