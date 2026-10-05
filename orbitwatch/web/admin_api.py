"""/api/admin: users and roles, reference data, watchlist, configuration, jobs and logs (SRS 3.7).

Administrator only; every statement runs on the ow_admin MySQL account
(DML on all tables, no DDL).
"""
import threading

from apscheduler.triggers.cron import CronTrigger
from flask import Blueprint, abort, request

from orbitwatch import auth, db, logging_setup, orbital
from orbitwatch.web.common import body, clean, current_user, int_arg, ok, page_args, role_required

bp = Blueprint("admin_api", __name__, url_prefix="/api/admin")

# Reference tables an administrator may edit. Column names come only from
# this whitelist (they cannot be parameterised); values are always parameters.
REF_TABLES = {
    "country": {"pk": "country_code", "auto": False, "cols": ["country_code", "name"],
                "search": ["country_code", "name"], "order": "name"},
    "organisation": {"pk": "org_id", "auto": False, "cols": ["org_id", "name", "org_type", "country_code"],
                     "search": ["org_id", "name"], "order": "name"},
    "launch_site": {"pk": "site_id", "auto": False, "cols": ["site_id", "name", "country_code", "latitude", "longitude"],
                    "search": ["site_id", "name"], "order": "name"},
    "launch_vehicle": {"pk": "vehicle_id", "auto": True, "cols": ["name", "org_id"],
                       "search": ["name"], "order": "name"},
    "mission": {"pk": "mission_id", "auto": True, "cols": ["name", "purpose", "org_id"],
                "search": ["name", "purpose"], "order": "name"},
    "orbit_region": {"pk": "region_id", "auto": True, "cols": ["name", "min_altitude_km", "max_altitude_km"],
                     "search": ["name"], "order": "min_altitude_km"},
}

# Configuration keys and their validation.
def _positive(lo, hi):
    def check(v):
        x = float(v)
        if not lo <= x <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return f"{x:g}"
    return check


def _cron(v):
    CronTrigger.from_crontab(v)
    return v.strip()


def _groups(v):
    items = [i.strip() for i in v.split(",") if i.strip()]
    if not items or any(not all(c.isalnum() or c in "-_:" for c in i) for i in items):
        raise ValueError("comma-separated CelesTrak group names")
    return ",".join(items)


CONFIG_RULES = {
    "screening_threshold_km": _positive(0.1, 200), "screening_horizon_hours": _positive(1, 168),
    "screening_step_seconds": _positive(10, 120), "screening_max_epoch_age_days": _positive(1, 90),
    "ingest_interval_hours": _positive(2, 48), "celestrak_groups": _groups, "catalog_refresh_cron": _cron,
    "aggregation_cron": _cron, "reentry_cron": _cron, "backup_cron": _cron, "backup_keep": _positive(1, 60),
}


# ---- users --------------------------------------------------------------

@bp.get("/users")
@role_required("admin")
def users():
    q = (request.args.get("q") or "").strip()
    where, params = "", []
    if q:
        where, params = "WHERE name LIKE %s OR email LIKE %s", [f"%{q}%", f"%{q}%"]
    rows = db.query("admin", f"""
        SELECT u.user_id, u.name, u.email, u.role, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM subscription s WHERE s.user_id = u.user_id) AS subscriptions,
               (SELECT COUNT(*) FROM alert a WHERE a.user_id = u.user_id AND NOT a.acknowledged) AS open_alerts
          FROM app_user u {where} ORDER BY u.created_at DESC LIMIT 500""", params)
    return ok({"items": clean(rows)})


@bp.post("/users")
@role_required("admin")
def create_user():
    data = body()
    try:
        uid = auth.create_user(data.get("name"), data.get("email"), data.get("password"), data.get("role", "viewer"))
    except auth.AuthError as exc:
        abort(400, description=str(exc))
    return ok({"user_id": uid}, 201)


@bp.patch("/users/<int:user_id>")
@role_required("admin")
def update_user(user_id):
    data = body()
    me = current_user()
    sets, params = [], []
    if "role" in data:
        if data["role"] not in auth.ROLES:
            abort(400, description="unknown role")
        if user_id == me["user_id"] and data["role"] != "admin":
            abort(400, description="You cannot remove your own administrator role.")
        sets.append("role = %s")
        params.append(data["role"])
    if "is_active" in data:
        if user_id == me["user_id"] and not data["is_active"]:
            abort(400, description="You cannot deactivate yourself.")
        sets.append("is_active = %s")
        params.append(bool(data["is_active"]))
    if not sets:
        abort(400, description="nothing to change")
    count, _ = db.execute("admin", f"UPDATE app_user SET {', '.join(sets)} WHERE user_id = %s", params + [user_id])
    if not count and not db.query_one("admin", "SELECT 1 AS x FROM app_user WHERE user_id = %s", (user_id,)):
        abort(404)
    return ok()


@bp.delete("/users/<int:user_id>")
@role_required("admin")
def delete_user(user_id):
    if user_id == current_user()["user_id"]:
        abort(400, description="You cannot delete yourself.")
    db.execute("admin", "DELETE FROM app_user WHERE user_id = %s", (user_id,))
    return ok()


# ---- reference data -----------------------------------------------------

def _ref(table):
    spec = REF_TABLES.get(table)
    if spec is None:
        abort(404, description="unknown reference table")
    return spec


def _values(spec, data, partial=False):
    cols = [c for c in spec["cols"] if c in data] if partial else spec["cols"]
    values = []
    for c in cols:
        v = data.get(c)
        values.append(None if v == "" else v)
    return cols, values


@bp.get("/ref/<table>")
@role_required("admin")
def ref_list(table):
    spec = _ref(table)
    page, size = page_args(default_size=50)
    q = (request.args.get("q") or "").strip()
    where, params = "", []
    if q:
        where = "WHERE " + " OR ".join(f"{c} LIKE %s" for c in spec["search"])
        params = [f"%{q}%"] * len(spec["search"])
    total = db.query_one("admin", f"SELECT COUNT(*) AS n FROM {table} {where}", params)["n"]
    rows = db.query("admin", f"SELECT * FROM {table} {where} ORDER BY {spec['order']} LIMIT %s OFFSET %s",
                    params + [size, (page - 1) * size])
    return ok({"table": table, "pk": spec["pk"], "columns": ([spec["pk"]] if spec["auto"] else []) + spec["cols"],
               "total": total, "page": page, "page_size": size, "items": clean(rows)})


@bp.post("/ref/<table>")
@role_required("admin")
def ref_create(table):
    spec = _ref(table)
    cols, values = _values(spec, body())
    _, new_id = db.execute("admin", f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
                                    f"({', '.join(['%s'] * len(cols))})", values)
    return ok({"id": new_id if spec["auto"] else values[cols.index(spec["pk"])]}, 201)


@bp.put("/ref/<table>/<key>")
@role_required("admin")
def ref_update(table, key):
    spec = _ref(table)
    cols, values = _values(spec, body(), partial=True)
    if not cols:
        abort(400, description="nothing to change")
    count, _ = db.execute("admin", f"UPDATE {table} SET {', '.join(c + ' = %s' for c in cols)} "
                                   f"WHERE {spec['pk']} = %s", values + [key])
    return ok({"updated": count})


@bp.delete("/ref/<table>/<key>")
@role_required("admin")
def ref_delete(table, key):
    spec = _ref(table)
    count, _ = db.execute("admin", f"DELETE FROM {table} WHERE {spec['pk']} = %s", (key,))
    return ok({"deleted": count})


# ---- watchlist ----------------------------------------------------------

@bp.get("/watchlist")
@role_required("admin")
def watchlist():
    rows = db.query("admin", """
        SELECT w.norad_id, w.reason, w.added_on, u.name AS added_by, c.name, c.object_type, c.country_name,
               c.region_name, c.epoch
          FROM watchlist w
          JOIN v_object_catalog c ON c.norad_id = w.norad_id
          LEFT JOIN app_user u ON u.user_id = w.added_by
         ORDER BY c.name""")
    return ok({"items": clean(rows)})


@bp.post("/watchlist")
@role_required("admin")
def watchlist_add():
    data = body()
    try:
        norad = int(data.get("norad_id"))
    except (TypeError, ValueError):
        abort(400, description="norad_id must be an integer")
    obj = db.query_one("admin", "SELECT decay_date, in_earth_orbit FROM space_object WHERE norad_id = %s", (norad,))
    if obj is None:
        abort(404, description="No such object")
    if obj["decay_date"]:
        abort(400, description="That object has re-entered.")
    if not obj["in_earth_orbit"]:
        abort(400, description="That object is not in Earth orbit (deep-space or docked), so it cannot be screened.")
    db.execute("admin", "INSERT INTO watchlist (norad_id, reason, added_by) VALUES (%s, %s, %s) AS n "
                        "ON DUPLICATE KEY UPDATE reason = n.reason",
               (norad, (data.get("reason") or "")[:200] or None, current_user()["user_id"]))
    return ok({"norad_id": norad}, 201)


@bp.delete("/watchlist/<int:norad_id>")
@role_required("admin")
def watchlist_remove(norad_id):
    db.execute("admin", "DELETE FROM watchlist WHERE norad_id = %s", (norad_id,))
    return ok()


# ---- configuration ------------------------------------------------------

@bp.get("/config")
@role_required("admin")
def get_config():
    rows = db.query("admin", "SELECT c.config_key, c.config_value, c.description, c.updated_on, u.name AS updated_by "
                             "FROM system_config c LEFT JOIN app_user u ON u.user_id = c.updated_by "
                             "ORDER BY c.config_key")
    return ok({"items": clean(rows)})


@bp.put("/config")
@role_required("admin")
def put_config():
    data = body()
    updates = []
    for key, value in data.items():
        rule = CONFIG_RULES.get(key)
        if rule is None:
            abort(400, description=f"unknown setting {key}")
        try:
            updates.append((rule(str(value)), current_user()["user_id"], key))
        except ValueError as exc:
            abort(400, description=f"{key}: {exc}")
    with db.mysql_conn("admin") as conn:
        cur = conn.cursor()
        cur.executemany("UPDATE system_config SET config_value = %s, updated_by = %s WHERE config_key = %s", updates)
        conn.commit()
    return ok({"updated": len(updates)})


# ---- jobs and logs ------------------------------------------------------

def _job_functions():
    from orbitwatch.jobs import aggregate, backup, catalog, ingest, reentry, screening, spacetrack, spaceweather
    return {
        "space_weather": (spaceweather.run, {}),
        "catalog": (catalog.run, {}), "ingest": (ingest.run, {}), "screening": (screening.run, {}),
        "aggregation": (aggregate.run, {}), "reentry_train": (reentry.train, {}),
        "reentry_predict": (reentry.predict, {}), "backup": (backup.run, {}),
        "spacetrack_import": (spacetrack.run, {"mode": "watchlist", "days": 730, "limit": 200}),
    }


@bp.get("/jobs")
@role_required("admin")
def jobs():
    from orbitwatch.jobs.runner import is_running
    latest = db.query("admin", "SELECT * FROM v_job_latest ORDER BY job_name")
    recent = db.query("admin", "SELECT * FROM job_run ORDER BY run_id DESC LIMIT %s", (int_arg("limit", 60),))
    names = sorted(_job_functions())
    return ok({"jobs": names, "running": {n: is_running(n) for n in names},
               "latest": clean(latest), "recent": clean(recent)})


@bp.post("/jobs/<name>/run")
@role_required("admin")
def run_job_now(name):
    from orbitwatch.jobs.runner import is_running, run_job
    fns = _job_functions()
    if name not in fns:
        abort(404, description="unknown job")
    if is_running(name):
        abort(409, description="That job is already running.")
    fn, kwargs = fns[name]
    data = request.get_json(silent=True) or {}
    if name == "spacetrack_import":
        kwargs = {"mode": data.get("mode", "watchlist") if data.get("mode") in ("watchlist", "decayed") else "watchlist",
                  "days": int(data.get("days", 730)), "limit": int(data.get("limit", 200))}
    threading.Thread(target=run_job, args=(name, fn), kwargs={"triggered_by": "manual", "retries": 0, **kwargs},
                     daemon=True, name=f"job-{name}").start()
    return ok({"started": name, "at": orbital.now_utc().isoformat() + "Z"}, 202)


@bp.get("/logs")
@role_required("admin")
def logs():
    source = request.args.get("source", "download")
    limit = min(int_arg("limit", 200), 1000)
    if source == "download":
        docs = list(db.mongo_db("analyst").download_log.find({}, {"_id": 0}).sort("started_at", -1).limit(limit))
        for d in docs:
            for k in ("started_at", "finished_at"):
                if d.get(k):
                    d[k] = d[k].replace(tzinfo=None)
        return ok({"source": source, "items": clean(docs)})
    if source in logging_setup.LOG_FILES:
        return ok({"source": source, "lines": logging_setup.tail(source, limit)})
    abort(400, description="unknown log source")


@bp.get("/mongo-status")
@role_required("admin")
def mongo_status():
    from orbitwatch import mongo_cluster
    return ok(mongo_cluster.status())
