"""/api/auth: registration, login, logout, current user, password change and reset.

Rate limits are kept in MySQL (orbitwatch/ratelimit.py), so they hold across
restarts and processes. Forgot-password always answers the same way, whether
or not the e-mail has an account.
"""
import hashlib
import time

from flask import Blueprint, current_app, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

from orbitwatch import auth, challenge, config, db, events, notify, ratelimit
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


CHALLENGE_AFTER_EMAIL = 3    # failed logins on an account (any address) before the browser must solve a challenge
CHALLENGE_AFTER_IP = 5       # failed logins from an address (any account) before the same
NOTICE_AFTER = 10            # failed logins on an account before its owner is e-mailed (at most once an hour)
DEVICE_COOKIE = "orbitwatch_device"
DEVICE_DAYS = 90


def _device_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="orbitwatch-trusted-device")


def _email_tag(email):
    return hashlib.sha256(f"device:{email}".encode()).hexdigest()[:32]


def _trusted_device(email):
    """This browser logged in to this account before (signed cookie, 90 days). Its owner is then neither locked out
    by failures other people caused on the account, nor asked to solve a challenge."""
    raw = request.cookies.get(DEVICE_COOKIE)
    if not raw or not email:
        return False
    try:
        return _device_serializer().loads(raw, max_age=DEVICE_DAYS * 86400) == _email_tag(email)
    except BadSignature:
        return False


def _notify_owner(email, ip):
    """Repeated failed logins on a real account: tell its owner, at most once an hour."""
    if ratelimit.count("login_email", email) < NOTICE_AFTER or ratelimit.exceeded("login_notice", email):
        return
    user = db.query_one("auth", "SELECT user_id, name FROM app_user WHERE email = %s AND is_active", (email,))
    if user is None:
        return
    ratelimit.hit("login_notice", email)
    subject, text = notify.failed_logins_email(user["name"], ratelimit.count("login_email", email), ip)
    notify.enqueue("security", email, subject, text, user_id=user["user_id"], account="auth",
                   dedupe_key=f"login-notice:{user['user_id']}:{int(time.time() // 3600)}")
    notify.dispatch_soon()
    events.record("auth", "login_attempts", f"Repeated failed logins on {user['name']}'s account", severity="warning",
                  visibility="admin", entity_type="user", entity_id=user["user_id"], account="auth")


@bp.post("/login")
def login():
    data = body()
    email = (data.get("email") or "").strip().lower()
    ip = request.remote_addr or "-"
    key = f"{email}|{ip}"
    trusted = _trusted_device(email)
    # Three buckets: this e-mail from this address, this address for any e-mail (password spraying), and this
    # e-mail from any address (one account attacked from many addresses). A trusted device skips the last one, so an
    # attacker cannot lock the real owner out of their own account.
    if (ratelimit.exceeded("login", key) or ratelimit.exceeded("login_ip", ip)
            or (not trusted and ratelimit.exceeded("login_email", email))):
        return ok({"error": "Too many failed attempts. Try again in a few minutes."}, 429)
    if not trusted and (ratelimit.count("login_email", email) >= CHALLENGE_AFTER_EMAIL
                        or ratelimit.count("login_ip", ip) >= CHALLENGE_AFTER_IP):
        if not challenge.verify(data.get("challenge")):
            # Not counted as a failed login: the password was not even checked.
            return ok({"error": "Please wait while your browser confirms you are a person, then try again.",
                       "challenge": challenge.issue()}, 401)
    user = auth.authenticate(data.get("email"), data.get("password"))
    if user is None:
        ratelimit.hit("login", key)
        ratelimit.hit("login_ip", ip)
        ratelimit.hit("login_email", email)
        _notify_owner(email, ip)
        return ok({"error": "Incorrect email or password."}, 401)
    ratelimit.clear("login", key)
    # The account's owner proved the password, so the per-account count starts again. The per-address count is not
    # cleared: an attacker could otherwise reset it by logging in to an account of their own between guesses.
    ratelimit.clear("login_email", email)
    resp, status = ok({"user": _session_user(user)})
    resp.set_cookie(DEVICE_COOKIE, _device_serializer().dumps(_email_tag(email)), max_age=DEVICE_DAYS * 86400,
                    httponly=True, samesite="Strict", secure=current_app.config["SESSION_COOKIE_SECURE"], path="/api/auth")
    return resp, status


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
