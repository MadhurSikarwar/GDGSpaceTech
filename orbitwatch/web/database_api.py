"""/api/database: the shape and size of the project's databases, for the Database page.

Public and read-only, on the Public Viewer account like the landing page. MySQL's information_schema only lists
what the account may see, so the page shows exactly the tables, views and relationships a visitor can query (no
user accounts, sessions or credentials). Row counts come from COUNT(*); the MongoDB side lists collection names,
document counts and the field names of one sample document, never values.
"""
from flask import Blueprint

from orbitwatch import config, db
from orbitwatch.web.common import clean, memo, ok

bp = Blueprint("database_api", __name__, url_prefix="/api")


def _mysql():
    acct, schema = "viewer", config.MYSQL_DATABASE
    objects = db.query(acct, "SELECT TABLE_NAME AS name, TABLE_TYPE AS kind, "
                             "COALESCE(DATA_LENGTH, 0) + COALESCE(INDEX_LENGTH, 0) AS bytes "
                             "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME", (schema,))
    columns = {}
    for c in db.query(acct, "SELECT TABLE_NAME AS t, COLUMN_NAME AS name, COLUMN_TYPE AS type, COLUMN_KEY AS k, "
                            "IS_NULLABLE AS nullable FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s "
                            "ORDER BY TABLE_NAME, ORDINAL_POSITION", (schema,)):
        columns.setdefault(c["t"], []).append({"name": c["name"], "type": c["type"], "key": c["k"] or None,
                                               "nullable": c["nullable"] == "YES"})
    visible = {o["name"] for o in objects}
    fks = [{"from": r["t"], "column": r["c"], "to": r["rt"], "to_column": r["rc"]}
           for r in db.query(acct, "SELECT TABLE_NAME AS t, COLUMN_NAME AS c, REFERENCED_TABLE_NAME AS rt, "
                                   "REFERENCED_COLUMN_NAME AS rc FROM information_schema.KEY_COLUMN_USAGE "
                                   "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
                                   "ORDER BY TABLE_NAME, COLUMN_NAME", (schema,))
           if r["t"] in visible and r["rt"] in visible]
    tables, views = [], []
    for o in objects:
        entry = {"name": o["name"], "columns": columns.get(o["name"], [])}
        if o["kind"] == "VIEW":
            views.append(entry)
            continue
        # The name comes from information_schema, never from the request.
        entry["rows"] = db.query_one(acct, f"SELECT COUNT(*) AS n FROM `{o['name']}`")["n"]
        entry["bytes"] = int(o["bytes"])
        tables.append(entry)
    return {"schema": schema, "tables": tables, "views": views, "relations": fks}


def _mongo():
    try:
        mdb = db.mongo_db("analyst")
        out = []
        for name in sorted(mdb.list_collection_names()):
            coll = mdb[name]
            sample = coll.find_one({}, {"_id": 0}) or {}
            out.append({"name": name, "documents": coll.estimated_document_count(), "fields": sorted(sample.keys())})
        return {"database": config.MONGO_DATABASE, "available": True, "collections": out}
    except Exception:  # noqa: BLE001 - the MySQL half of the page must still show when MongoDB is down
        return {"database": config.MONGO_DATABASE, "available": False, "collections": []}


@bp.get("/database")
def database():
    return ok(clean(memo("database_overview", lambda: {"mysql": _mysql(), "mongo": _mongo()})))
