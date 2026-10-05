"""/api/auth: registration, login, logout, current user."""
import time
from collections import defaultdict, deque

from flask import Blueprint, request, session

from orbitwatch import auth
from orbitwatch.web.common import body, current_user, ok

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

# Simple brute-force brake: 8 failed logins per email+address in 10 minutes.
_failures = defaultdict(deque)
_WINDOW, _LIMIT = 600, 8


def _throttled(key):
    q = _failures[key]
    now = time.time()
    while q and now - q[0] > _WINDOW:
        q.popleft()
    return len(q) >= _LIMIT


def _session_user(user):
    session.clear()  # new session id on login: no session fixation
    session.permanent = True
    session["user_id"] = user["user_id"]
    return {**user, "role_label": auth.ROLE_LABELS[user["role"]]}


@bp.post("/register")
def register():
    data = body()
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
    if _throttled(key):
        return ok({"error": "Too many failed attempts. Try again in a few minutes."}, 429)
    user = auth.authenticate(data.get("email"), data.get("password"))
    if user is None:
        _failures[key].append(time.time())
        return ok({"error": "Incorrect email or password."}, 401)
    _failures.pop(key, None)
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
