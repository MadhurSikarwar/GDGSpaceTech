"""/api/auth: registration, login, logout, current user, password change and reset.

Rate limits are kept in MySQL (orbitwatch/ratelimit.py), so they hold across
restarts and processes. Forgot-password always answers the same way, whether
or not the e-mail has an account.
"""
from flask import Blueprint, request, session

from orbitwatch import auth, config, db, events, notify, ratelimit
from orbitwatch.web.common import body, current_user, login_required, ok

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

FORGOT_REPLY = ("If an account exists for that address, a reset link is on its way. "
                "It expires in 30 minutes and works once.")


def _session_user(user):
    session.clear()  # new session id on login: no session fixation
    session.permanent = True
    session["user_id"] = user["user_id"]
    session["sv"] = user.get("session_version", 1)
    return {k: v for k, v in user.items() if k != "session_version"} | {"role_label": auth.ROLE_LABELS[user["role"]]}


@bp.post("/register")
def register():
    data = body()
    ip = request.remote_addr or "-"
    if ratelimit.exceeded("register", ip):
        return ok({"error": "Too many registrations from this address. Try again later."}, 429)
    ratelimit.hit("register", ip)
    try:
        user_id = auth.register(data.get("name"), data.get("email"), data.get("password"))
    except auth.AuthError as exc:
        return ok({"error": str(exc)}, 400)
    user = auth.authenticate(data.get("email"), data.get("password"))
    return ok({"user": _session_user(user)}, 201) if user else ok({"user_id": user_id}, 201)


@bp.post("/login")
def login():
    data = body()
    key = f"{(data.get('email') or '').strip().lower()}|{request.remote_addr}"
    if ratelimit.exceeded("login", key):
        return ok({"error": "Too many failed attempts. Try again in a few minutes."}, 429)
    user = auth.authenticate(data.get("email"), data.get("password"))
    if user is None:
        ratelimit.hit("login", key)
        return ok({"error": "Incorrect email or password."}, 401)
    ratelimit.clear("login", key)
    return ok({"user": _session_user(user)})


@bp.post("/logout")
def logout():
    session.clear()
    return ok()


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return ok({"user": None})
    return ok({"user": {**user, "role_label": auth.ROLE_LABELS[user["role"]]}})


@bp.post("/password")
@login_required
def change_password():
    """Change the password (current one required). Other sessions are signed out; this one stays."""
    user = current_user()
    data = body()
    if ratelimit.exceeded("password", user["user_id"]):
        return ok({"error": "Too many wrong attempts. Try again in a few minutes."}, 429)
    if not auth.verify_password(user["user_id"], data.get("current_password")):
        ratelimit.hit("password", user["user_id"])
        return ok({"error": "Your current password is not correct."}, 400)
    if data.get("current_password") == data.get("new_password"):
        return ok({"error": "Choose a password different from the current one."}, 400)
    try:
        session["sv"] = auth.change_password(user["user_id"], data.get("new_password"))
    except auth.AuthError as exc:
        return ok({"error": str(exc)}, 400)
    ratelimit.clear("password", user["user_id"])
    subject, text = notify.password_changed_email(user["name"], "changed")
    notify.enqueue("password_changed", user["email"], subject, text, user_id=user["user_id"], account="auth")
    notify.dispatch_soon()
    events.record("auth", "password_changed", f"{user['name']} changed their password", visibility="admin",
                  actor=user["user_id"], entity_type="user", entity_id=user["user_id"], account="auth")
    return ok({"ok": True, "message": "Password changed. Other sessions have been signed out."})


@bp.post("/forgot")
def forgot():
    data = body()
    ip = request.remote_addr or "-"
    email = (data.get("email") or "").strip().lower()
    if ratelimit.exceeded("forgot_ip", ip) or ratelimit.exceeded("forgot_email", email):
        return ok({"error": "Too many reset requests. Try again later."}, 429)
    ratelimit.hit("forgot_ip", ip)
    ratelimit.hit("forgot_email", email)
    issued = auth.request_reset(email, ip)
    if issued:
        token, uid, name = issued
        link = f"{config.APP_BASE_URL}/#/reset?token={token}"
        subject, text = notify.reset_email(name, link, auth.RESET_TTL_MIN)
        notify.enqueue("password_reset", email, subject, text, user_id=uid, account="auth")
        notify.dispatch_soon()
        events.record("auth", "password_reset_requested", "Password reset requested", visibility="admin",
                      entity_type="user", entity_id=uid, account="auth")
    return ok({"ok": True, "message": FORGOT_REPLY, "email_delivery": config.smtp_configured()})


@bp.post("/reset")
def reset():
    data = body()
    ip = request.remote_addr or "-"
    if ratelimit.exceeded("reset", ip):
        return ok({"error": "Too many attempts. Try again later."}, 429)
    ratelimit.hit("reset", ip)
    try:
        uid = auth.reset_password(data.get("token"), data.get("new_password"))
    except auth.AuthError as exc:
        return ok({"error": str(exc)}, 400)
    full = db.query_one("auth", "SELECT name, email FROM app_user WHERE user_id = %s", (uid,))
    if full:
        subject, text = notify.password_changed_email(full["name"], "reset with an e-mailed link")
        notify.enqueue("password_changed", full["email"], subject, text, user_id=uid, account="auth")
        notify.dispatch_soon()
    events.record("auth", "password_reset", "Password reset with an e-mailed link", visibility="admin",
                  entity_type="user", entity_id=uid, account="auth")
    session.clear()
    return ok({"ok": True, "message": "Password reset. Log in with your new password."})
