"""Users and passwords (SRS 3.1). Passwords are stored only as bcrypt hashes."""
import re

import bcrypt
import mysql.connector

from orbitwatch import db

ROLES = ("viewer", "analyst", "admin")
ROLE_LABELS = {"viewer": "Public Viewer", "analyst": "Analyst", "admin": "Administrator"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


def validate(name, email, password):
    name, email = (name or "").strip(), (email or "").strip().lower()
    if not name or len(name) > 100:
        raise AuthError("Name is required (up to 100 characters).")
    if not EMAIL_RE.match(email) or len(email) > 190:
        raise AuthError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if len(password.encode("utf-8")) > 72:
        raise AuthError("Password must be at most 72 bytes (bcrypt limit).")
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
    row = db.query_one("auth", "SELECT user_id, name, email, role, password_hash, is_active "
                               "FROM app_user WHERE email = %s", ((email or "").strip().lower(),))
    if row is None:
        check_password(password or "", _DUMMY_HASH)
        return None
    if not check_password(password or "", row["password_hash"]) or not row["is_active"]:
        return None
    return {"user_id": row["user_id"], "name": row["name"], "email": row["email"], "role": row["role"]}


def refresh(user_id):
    """Current role/active flag (an admin may have changed them since login)."""
    return db.query_one("auth", "SELECT user_id, name, email, role, is_active FROM app_user WHERE user_id = %s",
                        (user_id,))
