"""Create the OrbitWatch MySQL database from database/mysql/*.sql.

Needs MYSQL_ROOT_PASSWORD in .env once. The six application accounts get
random passwords that are written to .env (never printed, never in SQL files).
"""
import logging
import os
import re

import mysql.connector

from orbitwatch import config

log = logging.getLogger(__name__)

SCHEMA_FILES = ["01_schema.sql", "02_routines.sql", "03_views.sql", "04_security.sql", "05_seed.sql",
                "06_extensions.sql", "07_operations.sql"]
# Safe on an existing database; 06 adds the OrbitalGuard-derived features and 07 the operations
# layer (provenance, approvals, demo, notifications, model registry, event log) to an older install.
REAPPLY_FILES = ["02_routines.sql", "03_views.sql", "04_security.sql", "06_extensions.sql", "07_operations.sql"]


def split_sql(text):
    """Split a script into statements, honouring DELIMITER lines like the mysql client.

    A statement ends at a line that ends with the active delimiter; '--' comment
    lines are dropped.
    """
    delimiter = ";"
    statements, buf = [], []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        m = re.match(r"(?i)^DELIMITER\s+(\S+)\s*$", stripped)
        if m:
            delimiter = m.group(1)
            continue
        if stripped.startswith("--"):
            continue
        if stripped.endswith(delimiter):
            buf.append(line[: line.rstrip().rfind(delimiter)])
            stmt = "\n".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(line)
    if "".join(buf).strip():
        statements.append("\n".join(buf).strip())
    return statements


def _root_connection():
    password = os.getenv("MYSQL_ROOT_PASSWORD")
    if password is None:
        raise RuntimeError("Add MYSQL_ROOT_PASSWORD=<your MySQL root password> to .env, then rerun.")
    return mysql.connector.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                                   user=os.getenv("MYSQL_ROOT_USER", "root"), password=password,
                                   autocommit=True, charset="utf8mb4")


def run(reset=False):
    secrets = {var: config.ensure_secret(var) for _, var in config.MYSQL_ACCOUNTS.values()}
    config.ensure_secret("FLASK_SECRET_KEY", 32)

    conn = _root_connection()
    cur = conn.cursor()
    if reset:
        cur.execute(f"DROP DATABASE IF EXISTS {config.MYSQL_DATABASE}")
    cur.execute("SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (config.MYSQL_DATABASE,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute(f"CREATE DATABASE {config.MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    cur.execute(f"USE {config.MYSQL_DATABASE}")

    for name in (REAPPLY_FILES if exists else SCHEMA_FILES):
        text = (config.SQL_DIR / name).read_text(encoding="utf-8")
        for key, value in secrets.items():
            text = text.replace("{{" + key + "}}", value.replace("'", "''"))
        statements = split_sql(text)
        for stmt in statements:
            try:
                cur.execute(stmt)
            except mysql.connector.Error as exc:
                # Never echo statements from the security file: they contain passwords.
                shown = "<security statement>" if name == "04_security.sql" else stmt[:300]
                raise RuntimeError(f"{name}: {exc.msg}\n{shown}") from None
        print(f"  applied {name} ({len(statements)} statements)")
    cur.close()
    conn.close()
    print("MySQL ready: database", config.MYSQL_DATABASE, "(existing schema kept)" if exists else "(created)")
