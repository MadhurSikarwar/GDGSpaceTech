"""Configuration. Every credential comes from .env, never from source code."""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
load_dotenv(ENV_FILE)

# Runtime files (MongoDB binaries and data, venv, logs, backups, certificates)
# live outside the repo: OneDrive must not sync live database files.
RUNTIME_DIR = Path(os.getenv("ORBITWATCH_RUNTIME", str(Path.home() / "orbitwatch-runtime")))
CACHE_DIR = RUNTIME_DIR / "cache"
LOG_DIR = RUNTIME_DIR / "logs"
BACKUP_DIR = RUNTIME_DIR / "backups"
CERT_DIR = RUNTIME_DIR / "certs"
MODEL_DIR = RUNTIME_DIR / "models"
MONGO_HOME = RUNTIME_DIR / "mongodb"
MONGO_DATA_DIR = RUNTIME_DIR / "data" / "mongo"

FRONTEND_DIR = REPO_ROOT / "frontend"
SQL_DIR = REPO_ROOT / "database" / "mysql"

# ---- MySQL --------------------------------------------------------------
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = "orbitwatch"
MYSQL_BIN_DIR = Path(os.getenv("MYSQL_BIN_DIR", r"C:\Program Files\MySQL\MySQL Server 8.4\bin"))

# One MySQL account per application role (see database/mysql/04_security.sql).
MYSQL_ACCOUNTS = {
    "auth": ("ow_auth", "MYSQL_AUTH_PASSWORD"),
    "viewer": ("ow_viewer", "MYSQL_VIEWER_PASSWORD"),
    "analyst": ("ow_analyst", "MYSQL_ANALYST_PASSWORD"),
    "admin": ("ow_admin", "MYSQL_ADMIN_PASSWORD"),
    "jobs": ("ow_jobs", "MYSQL_JOBS_PASSWORD"),
    "backup": ("ow_backup", "MYSQL_BACKUP_PASSWORD"),
}

# ---- MongoDB ------------------------------------------------------------
MONGO_HOST = os.getenv("MONGO_HOST", "127.0.0.1")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DATABASE = "orbitwatch"
MONGO_ACCOUNTS = {
    "root": ("ow_root", "MONGO_ROOT_PASSWORD"),
    "jobs": ("ow_jobs", "MONGO_JOBS_PASSWORD"),
    "analyst": ("ow_analyst", "MONGO_ANALYST_PASSWORD"),
}

# ---- Data sources -------------------------------------------------------
CELESTRAK_GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
CELESTRAK_SATCAT_URL = "https://celestrak.org/pub/satcat.csv"
GCAT_BASE_URL = "https://planet4589.org/space/gcat/tsv"
SPACETRACK_BASE_URL = "https://www.space-track.org"
HTTP_USER_AGENT = "OrbitWatch/1.0 (RVCE DBMS project; scheduled, rate-limited)"

# Development switch: reuse the downloads in CACHE_DIR instead of hitting
# CelesTrak again (CelesTrak blocks clients that re-download unchanged data).
USE_CACHE = os.getenv("ORBITWATCH_USE_CACHE", "0").lower() in ("1", "true", "yes")


def env(name, default=None):
    return os.getenv(name, default)


def require_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set in {ENV_FILE}")
    return value


def set_env_values(values):
    """Write or replace KEY=value lines in .env (used by setup for generated secrets)."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.extend(f"{k}={v}" for k, v in remaining.items())
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in values.items():
        os.environ[k] = v


def ensure_secret(name, nbytes=24):
    """Return the secret stored in .env under `name`, generating it first if absent."""
    value = os.getenv(name)
    if not value:
        value = secrets.token_urlsafe(nbytes)
        set_env_values({name: value})
    return value


for _d in (CACHE_DIR, LOG_DIR, BACKUP_DIR, CERT_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)
