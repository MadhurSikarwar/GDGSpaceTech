"""Users and passwords (SRS 3.1). Passwords are stored only as bcrypt hashes.

Password change and reset go through stored procedures (the auth account cannot
UPDATE app_user); both bump the account's session_version, which signs out every
other session. Reset tokens are random, single-use, expire after RESET_TTL_MIN
minutes, and only their SHA-256 is stored.
"""
import hashlib
import re
import secrets

import bcrypt
import mysql.connector

from orbitwatch import db

ROLES = ("viewer", "analyst", "admin")
ROLE_LABELS = {"viewer": "Public Viewer", "analyst": "Analyst", "admin": "Administrator"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


RESET_TTL_MIN = 30


def validate_password(password):
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > 72:
        raise AuthError("Password must be at most 72 bytes (bcrypt limit).")


def validate(name, email, password):
    name, email = (name or "").strip(), (email or "").strip().lower()
    if not name or len(name) > 100:
        raise AuthError("Name is required (up to 100 characters).")
    if not EMAIL_RE.match(email) or len(email) > 190:
        raise AuthError("Enter a valid email address.")
    validate_password(password)
    return name, email


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def check_password(password, password_hash):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def register(name, email, password):
    """Self-registration through sp_register_user: the database always assigns Public Viewer."""
    name, email = validate(name, email, password)
    try:
        with db.mysql_conn("auth") as conn:
            cur = conn.cursor()
            cur.execute("CALL sp_register_user(%s, %s, %s)", (name, email, hash_password(password)))
            user_id = cur.fetchone()[0]
            while cur.nextset():  # drain the CALL status result
                pass
            conn.commit()
            return user_id
    except mysql.connector.IntegrityError:
        raise AuthError("An account with this email already exists.") from None


def create_user(name, email, password, role):
    """Administrator path (CLI / admin page): any role, via the admin account."""
    if role not in ROLES:
        raise AuthError("Unknown role.")
    name, email = validate(name, email, password)
    try:
        _, user_id = db.execute("admin", "INSERT INTO app_user (name, email, role, password_hash) "
                                         "VALUES (%s, %s, %s, %s)", (name, email, role, hash_password(password)))
        return user_id
    except mysql.connector.IntegrityError:
        raise AuthError("An account with this email already exists.") from None


# A constant-time-ish dummy check for unknown emails, so response time does not reveal which emails exist.
_DUMMY_HASH = hash_password("orbitwatch-dummy-password")


def authenticate(email, password):
    row = db.query_one("auth", "SELECT user_id, name, email, role, password_hash, is_active, session_version "
                               "FROM app_user WHERE email = %s", ((email or "").strip().lower(),))
    if row is None:
        check_password(password or "", _DUMMY_HASH)
        return None
    if not check_password(password or "", row["password_hash"]) or not row["is_active"]:
        return None
    try:
        db.execute("auth", "CALL sp_record_login(%s)", (row["user_id"],))
    except Exception:  # noqa: BLE001 - bookkeeping only
        pass
    return {"user_id": row["user_id"], "name": row["name"], "email": row["email"], "role": row["role"],
            "session_version": row["session_version"]}


def refresh(user_id):
    """Current role/active flag/session version (an admin may have changed them since login)."""
    return db.query_one("auth", "SELECT user_id, name, email, role, is_active, session_version FROM app_user "
                                "WHERE user_id = %s", (user_id,))


def verify_password(user_id, password):
    row = db.query_one("auth", "SELECT password_hash FROM app_user WHERE user_id = %s AND is_active", (user_id,))
    return bool(row) and check_password(password or "", row["password_hash"])


def change_password(user_id, new_password):
    """Set a new password (caller has verified the current one); returns the new session version."""
    validate_password(new_password)
    with db.mysql_conn("auth") as conn:
        cur = conn.cursor()
        cur.execute("CALL sp_change_password(%s, %s)", (user_id, hash_password(new_password)))
        while cur.nextset():
            pass
        conn.commit()
    return refresh(user_id)["session_version"]


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_reset(email, ip):
    """Create a reset token if the e-mail belongs to an active account. Returns (token, user_id, name) or None.

    The caller always answers the same way, so the response does not reveal whether the account exists.
    """
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return None
    token = secrets.token_urlsafe(32)
    with db.mysql_conn("auth") as conn:
        cur = conn.cursor()
        cur.execute("CALL sp_request_password_reset(%s, %s, %s, %s, @uid, @name)",
                    (email, token_hash(token), RESET_TTL_MIN, (ip or "")[:45]))
        while cur.nextset():
            pass
        cur.execute("SELECT @uid, @name")
        uid, name = cur.fetchone()
        conn.commit()
    return (token, uid, name) if uid else None


def reset_password(token, new_password):
    """Consume a reset token; returns the user id. Raises AuthError if the token is invalid or expired."""
    validate_password(new_password)
    if not token or len(token) > 200:
        raise AuthError("This reset link is invalid or has expired.")
    try:
        with db.mysql_conn("auth") as conn:
            cur = conn.cursor()
            cur.execute("CALL sp_reset_password(%s, %s, @uid)", (token_hash(token), hash_password(new_password)))
            while cur.nextset():
                pass
            cur.execute("SELECT @uid")
            uid = cur.fetchone()[0]
            conn.commit()
            return uid
    except mysql.connector.Error as exc:
        if getattr(exc, "errno", None) == 1644:   # SIGNAL: invalid / expired / used
            raise AuthError("This reset link is invalid or has expired.") from None
        raise
