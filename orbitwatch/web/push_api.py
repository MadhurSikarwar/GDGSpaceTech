"""/api/me/push: push notifications on the logged-in user's devices (see orbitwatch/push.py).

Device rows are read and written through the auth account: the public viewer account, which also serves anonymous
visitors, has no access to push endpoints. A device is identified to the browser by the SHA-256 of its endpoint,
never by the endpoint itself.
"""
from flask import Blueprint, abort, request

from orbitwatch import db, push, ratelimit
from orbitwatch.web.common import account, body, clean, current_user, login_required, ok

bp = Blueprint("push_api", __name__, url_prefix="/api/me/push")


def _min_risk(uid):
    row = db.query_one(account(), "SELECT push_min_risk FROM notification_pref WHERE user_id = %s", (uid,))
    return row["push_min_risk"] if row else "HIGH"


@bp.get("")
@login_required
def overview():
    uid = current_user()["user_id"]
    devices = db.query("auth", """SELECT subscription_id, endpoint_hash, user_agent, created_at, last_success_at,
                                         last_failure_at, last_error FROM push_subscription WHERE user_id = %s
                                   ORDER BY created_at""", (uid,))
    return ok({"public_key": push.public_key(), "min_risk_level": _min_risk(uid), "risk_levels": list(push.RISK_ORDER),
               "devices": clean(devices)})


@bp.put("")
@login_required
def set_level():
    level = body().get("min_risk_level")
    if level not in push.RISK_ORDER:
        abort(400, description="min_risk_level must be one of " + ", ".join(push.RISK_ORDER))
    db.execute(account(), """INSERT INTO notification_pref (user_id, push_min_risk) VALUES (%s, %s) AS n
                             ON DUPLICATE KEY UPDATE push_min_risk = n.push_min_risk""",
               (current_user()["user_id"], level))
    return ok({"min_risk_level": level})


@bp.post("/subscriptions")
@login_required
def subscribe():
    data = body()
    try:
        endpoint, p256dh, auth = push.validate_subscription(data)
    except ValueError as exc:
        abort(400, description=str(exc))
    agent = str(data.get("user_agent") or request.headers.get("User-Agent") or "")[:200]
    h = push.endpoint_hash(endpoint)
    # The same browser subscribing again (or after another user logged in on it) takes the row over.
    db.execute("auth", """INSERT INTO push_subscription (user_id, endpoint, endpoint_hash, p256dh, auth_secret, user_agent)
                          VALUES (%s, %s, %s, %s, %s, %s) AS n
                          ON DUPLICATE KEY UPDATE user_id = n.user_id, endpoint = n.endpoint, p256dh = n.p256dh,
                                                  auth_secret = n.auth_secret, user_agent = n.user_agent,
                                                  last_error = NULL""",
               (current_user()["user_id"], endpoint, h, p256dh, auth, agent))
    row = db.query_one("auth", "SELECT subscription_id FROM push_subscription WHERE endpoint_hash = %s", (h,))
    return ok({"subscription_id": row["subscription_id"], "endpoint_hash": h}, 201)


@bp.delete("/subscriptions/<int:subscription_id>")
@login_required
def remove(subscription_id):
    count, _ = db.execute("auth", "DELETE FROM push_subscription WHERE subscription_id = %s AND user_id = %s",
                          (subscription_id, current_user()["user_id"]))
    return ok({"removed": count})


@bp.delete("/subscriptions/hash/<string:h>")
@login_required
def remove_this_browser(h):
    """A browser turning notifications off (or logging out) names itself by the SHA-256 of its endpoint."""
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        abort(400, description="expected a SHA-256 hex digest")
    count, _ = db.execute("auth", "DELETE FROM push_subscription WHERE endpoint_hash = %s AND user_id = %s",
                          (h, current_user()["user_id"]))
    return ok({"removed": count})


@bp.post("/test")
@login_required
def test():
    user = current_user()
    if ratelimit.exceeded("push_test", user["user_id"]):
        return ok({"error": "Too many test notifications. Try again in a few minutes."}, 429)
    ratelimit.hit("push_test", user["user_id"])
    results = push.send_test(user["user_id"], user["name"])
    if not results:
        return ok({"error": "No device has push notifications turned on yet."}, 400)
    delivered = sum(r["outcome"] == "sent" for r in results)
    return ok({"results": results, "message": f"Sent to {delivered} of {len(results)} device"
                                              f"{'' if len(results) == 1 else 's'}."})
