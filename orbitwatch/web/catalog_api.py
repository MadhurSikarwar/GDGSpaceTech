"""/api: dashboard statistics, filter lookups, catalogue search and object detail (SRS 3.3)."""
import re

from flask import Blueprint, abort, request

from orbitwatch import db
from orbitwatch.web.common import (account, clean, csv_response, current_user, int_arg, ok, page_args,
                                   require_analyst, wants_csv)

bp = Blueprint("catalog_api", __name__, url_prefix="/api")

OBJECT_TYPES = ("Payload", "Rocket Body", "Debris", "Unknown")
SORTS = {"name": "name", "norad": "norad_id", "launch": "launch_date DESC", "perigee": "perigee_km",
         "type": "object_type, name"}


@bp.get("/stats")
def stats():
    acct = account()
    counts = db.query_one(acct, """
        SELECT COUNT(*) AS total,
               COUNT(CASE WHEN in_earth_orbit THEN 1 END) AS on_orbit,
               COUNT(CASE WHEN decay_date IS NOT NULL THEN 1 END) AS decayed,
               COUNT(CASE WHEN decay_date IS NULL AND NOT in_earth_orbit THEN 1 END) AS beyond_earth_orbit,
               COUNT(CASE WHEN in_earth_orbit AND data_status = 'NEA' THEN 1 END) AS no_elements_published,
               COUNT(CASE WHEN in_earth_orbit AND object_type = 'Debris' THEN 1 END) AS debris_on_orbit,
               COUNT(CASE WHEN in_earth_orbit AND object_type = 'Payload' THEN 1 END) AS payloads_on_orbit
          FROM space_object""")
    counts["with_current_orbit"] = db.query_one(acct, "SELECT COUNT(*) AS n FROM current_orbit")["n"]
    counts["watchlist"] = db.query_one(acct, "SELECT COUNT(*) AS n FROM watchlist")["n"]
    upcoming = db.query(acct, """
        SELECT risk_level, COUNT(*) AS n FROM conjunction_event
         WHERE time_of_closest_approach BETWEEN UTC_TIMESTAMP() AND UTC_TIMESTAMP() + INTERVAL 7 DAY
         GROUP BY risk_level""")
    threshold = db.query_one(acct, "SELECT config_value FROM system_config WHERE config_key = 'screening_threshold_km'")
    return ok({
        "counts": counts,
        "upcoming_by_risk": {r["risk_level"]: r["n"] for r in upcoming},
        "regions": clean(db.query(acct, "SELECT * FROM v_report_region_counts ORDER BY min_altitude_km")),
        "last_update": {r["job_name"]: clean(r)["last_success"] for r in db.query(acct, "SELECT * FROM v_last_update")},
        "screening_threshold_km": float(threshold["config_value"]) if threshold else None,
    })


@bp.get("/lookups")
def lookups():
    acct = account()
    countries = db.query(acct, """
        SELECT c.country_code, c.name, COUNT(*) AS objects
          FROM v_current_owner cw JOIN country c ON c.country_code = cw.country_code
         GROUP BY c.country_code, c.name ORDER BY objects DESC""")
    orgs = db.query(acct, """
        SELECT o.org_id, o.name, COUNT(*) AS objects
          FROM object_ownership oo JOIN organisation o ON o.org_id = oo.org_id
         WHERE oo.to_date IS NULL
         GROUP BY o.org_id, o.name ORDER BY objects DESC LIMIT 400""")
    regions = db.query(acct, "SELECT region_id, name, min_altitude_km, max_altitude_km FROM orbit_region "
                             "ORDER BY min_altitude_km")
    return ok({"object_types": OBJECT_TYPES, "countries": countries, "organisations": orgs,
               "regions": clean(regions), "risk_levels": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]})


def _object_filters():
    where, params = [], []
    q = (request.args.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("(norad_id = %s OR name LIKE %s)")
            params += [int(q), f"%{q}%"]
        elif re.match(r"^\d{4}-\d{3}", q):
            where.append("intl_designator LIKE %s")
            params.append(f"{q}%")
        else:
            where.append("name LIKE %s")
            params.append(f"%{q}%")
    norad = int_arg("norad")
    if norad is not None:
        where.append("norad_id = %s")
        params.append(norad)
    if request.args.get("type") in OBJECT_TYPES:
        where.append("object_type = %s")
        params.append(request.args["type"])
    if request.args.get("country"):
        where.append("country_code = %s")
        params.append(request.args["country"])
    if request.args.get("org"):
        where.append("org_id = %s")
        params.append(request.args["org"])
    region = int_arg("region")
    if region is not None:
        where.append("region_id = %s")
        params.append(region)
    status = request.args.get("status", "onorbit")
    if status == "onorbit":
        where.append("in_earth_orbit")
    elif status == "decayed":
        where.append("decay_date IS NOT NULL")
    elif status == "beyond":  # probes and stages around the Sun, Moon, Mars, Lagrange points; docked objects
        where.append("decay_date IS NULL AND NOT in_earth_orbit")
    if request.args.get("has_orbit") == "1":
        where.append("epoch IS NOT NULL")
    return (" WHERE " + " AND ".join(where)) if where else "", params


@bp.get("/objects")
def objects():
    acct = account()
    where, params = _object_filters()
    order = SORTS.get(request.args.get("sort"), "norad_id")
    if wants_csv():
        require_analyst()
        rows = db.query(acct, f"SELECT * FROM v_object_catalog{where} ORDER BY {order} LIMIT 50000", params)
        return csv_response(rows, "orbitwatch_objects.csv")
    page, size = page_args()
    total = db.query_one(acct, f"SELECT COUNT(*) AS n FROM v_object_catalog{where}", params)["n"]
    rows = db.query(acct, f"SELECT * FROM v_object_catalog{where} ORDER BY {order} LIMIT %s OFFSET %s",
                    params + [size, (page - 1) * size])
    return ok({"total": total, "page": page, "page_size": size, "items": clean(rows)})


@bp.get("/objects/<int:norad_id>")
def object_detail(norad_id):
    acct = account()
    obj = db.query_one(acct, "SELECT * FROM v_object_catalog WHERE norad_id = %s", (norad_id,))
    if obj is None:
        abort(404, description="No such object")
    launch = db.query_one(acct, """
        SELECT l.launch_id, l.launch_date, l.outcome,
               ls.site_id, ls.name AS site_name, ls.latitude, ls.longitude, sc.name AS site_country,
               lv.vehicle_id, lv.name AS vehicle_name, lvo.org_id AS vehicle_org_id, lvo.name AS vehicle_org
          FROM launch l
          LEFT JOIN launch_site ls   ON ls.site_id = l.site_id
          LEFT JOIN country sc       ON sc.country_code = ls.country_code
          LEFT JOIN launch_vehicle lv ON lv.vehicle_id = l.vehicle_id
          LEFT JOIN organisation lvo ON lvo.org_id = lv.org_id
         WHERE l.launch_id = %s""", (obj["launch_id"],)) if obj["launch_id"] else None
    ownership = db.query(acct, """
        SELECT oo.org_id, o.name AS org_name, o.org_type, c.name AS country_name, oo.from_date, oo.to_date
          FROM object_ownership oo
          JOIN organisation o ON o.org_id = oo.org_id
          LEFT JOIN country c ON c.country_code = o.country_code
         WHERE oo.norad_id = %s ORDER BY oo.from_date""", (norad_id,))
    missions = db.query(acct, """
        SELECT m.mission_id, m.name, m.purpose, o.name AS org_name,
               (SELECT COUNT(*) FROM object_mission x WHERE x.mission_id = m.mission_id) AS members
          FROM object_mission om
          JOIN mission m ON m.mission_id = om.mission_id
          LEFT JOIN organisation o ON o.org_id = m.org_id
         WHERE om.norad_id = %s""", (norad_id,))
    parent = db.query_one(acct, "SELECT norad_id, name, object_type, status FROM space_object WHERE norad_id = %s",
                          (obj["parent_norad_id"],)) if obj["parent_norad_id"] else None
    children_total = db.query_one(acct, "SELECT COUNT(*) AS n, COUNT(CASE WHEN decay_date IS NULL THEN 1 END) AS "
                                         "on_orbit FROM space_object WHERE parent_norad_id = %s", (norad_id,))
    children = db.query(acct, "SELECT norad_id, name, object_type, status, decay_date FROM space_object "
                              "WHERE parent_norad_id = %s ORDER BY decay_date IS NOT NULL, norad_id LIMIT 50",
                        (norad_id,))
    orbit = db.query_one(acct, "SELECT * FROM current_orbit WHERE norad_id = %s", (norad_id,))
    reentry = db.query_one(acct, "SELECT * FROM reentry_prediction WHERE norad_id = %s", (norad_id,))
    events = db.query(acct, """
        SELECT * FROM v_conjunction_detail
         WHERE primary_norad = %s OR secondary_norad = %s
         ORDER BY time_of_closest_approach DESC LIMIT 25""", (norad_id, norad_id))
    watch = db.query_one(acct, "SELECT reason, added_on FROM watchlist WHERE norad_id = %s", (norad_id,))
    user = current_user()
    subscribed = bool(user and db.query_one(acct, "SELECT 1 AS x FROM subscription WHERE user_id = %s AND "
                                                  "norad_id = %s", (user["user_id"], norad_id)))
    return ok({
        "object": clean(obj), "launch": clean(launch) if launch else None, "ownership": clean(ownership),
        "missions": clean(missions), "parent": clean(parent) if parent else None,
        "children": {"total": children_total["n"], "on_orbit": children_total["on_orbit"], "items": clean(children)},
        "current_orbit": clean(orbit) if orbit else None, "reentry": clean(reentry) if reentry else None,
        "conjunctions": clean(events), "watchlist": clean(watch) if watch else None, "subscribed": subscribed,
    })


@bp.get("/reentry")
def reentry_predictions():
    """Objects the re-entry model expects to decay, soonest first."""
    rows = db.query(account(), """
        SELECT p.norad_id, so.name, so.object_type, p.predicted_decay_date, p.days_remaining, p.lower_days,
               p.upper_days, p.predicted_on, p.model_version, co.mean_altitude_km, co.perigee_km
          FROM reentry_prediction p
          JOIN space_object so ON so.norad_id = p.norad_id
          LEFT JOIN current_orbit co ON co.norad_id = p.norad_id
         ORDER BY p.days_remaining LIMIT %s""", (min(int_arg("limit", 100), 1000),))
    return ok({"items": clean(rows)})
