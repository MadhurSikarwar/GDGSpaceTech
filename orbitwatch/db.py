"""Database access: MySQL (one pooled account per role) and MongoDB.

All SQL goes through parameterised queries (%s placeholders), never string
formatting of user input, which is what protects against SQL injection.
"""
import logging
import threading
from contextlib import contextmanager

import mysql.connector
from mysql.connector import errors as mysql_errors
from mysql.connector import pooling
from pymongo import MongoClient

from orbitwatch import config

log = logging.getLogger(__name__)

_pools = {}
_pool_lock = threading.Lock()
_mongo_clients = {}


def _mysql_params(account):
    user, password_var = config.MYSQL_ACCOUNTS[account]
    return dict(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=user,
        password=config.require_env(password_var),
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        collation="utf8mb4_0900_ai_ci",
        time_zone="+00:00",
        autocommit=False,
    )


def _get_pool(account):
    with _pool_lock:
        if account not in _pools:
            _pools[account] = pooling.MySQLConnectionPool(
                pool_name=f"ow_{account}", pool_size=8, pool_reset_session=True, **_mysql_params(account)
            )
        return _pools[account]


@contextmanager
def mysql_conn(account):
    """A MySQL connection for one role. Rolled back unless the caller commits."""
    try:
        conn = _get_pool(account).get_connection()
    except mysql_errors.PoolError:
        # Pool exhausted under a burst of requests: fall back to a direct connection.
        conn = mysql.connector.connect(**_mysql_params(account))
    try:
        yield conn
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
        finally:
            conn.close()


def query(account, sql, params=None):
    """Run a SELECT and return a list of dict rows."""
    with mysql_conn(account) as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cur.close()
        return rows


def query_one(account, sql, params=None):
    rows = query(account, sql, params)
    return rows[0] if rows else None


def execute(account, sql, params=None):
    """Run one write statement in its own transaction; returns (rowcount, lastrowid)."""
    with mysql_conn(account) as conn:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        result = (cur.rowcount, cur.lastrowid)
        conn.commit()
        cur.close()
        return result


def bulk_upsert(cursor, sql_head, rows, sql_tail="", chunk=1000):
    """Multi-row INSERT in chunks; sql_head ends with 'VALUES', each row is a tuple."""
    if not rows:
        return 0
    width = len(rows[0])
    placeholder = "(" + ",".join(["%s"] * width) + ")"
    total = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        sql = f"{sql_head} {','.join([placeholder] * len(part))} {sql_tail}"
        cursor.execute(sql, [v for row in part for v in row])
        total += len(part)
    return total


def get_config(account="jobs"):
    rows = query(account, "SELECT config_key, config_value FROM system_config")
    return {r["config_key"]: r["config_value"] for r in rows}


# ---- MongoDB ------------------------------------------------------------

def mongo_uri(account):
    user, password_var = config.MONGO_ACCOUNTS[account]
    password = config.require_env(password_var)
    from urllib.parse import quote_plus
    return (f"mongodb://{quote_plus(user)}:{quote_plus(password)}@{config.MONGO_HOST}:{config.MONGO_PORT}/"
            f"?authSource=admin")


def mongo_client(account):
    if account not in _mongo_clients:
        _mongo_clients[account] = MongoClient(
            mongo_uri(account), serverSelectionTimeoutMS=5000, tz_aware=True, appname=f"orbitwatch-{account}"
        )
    return _mongo_clients[account]


def mongo_db(account):
    return mongo_client(account)[config.MONGO_DATABASE]
