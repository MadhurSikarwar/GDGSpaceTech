"""/api: dashboard statistics, filter lookups, catalogue search and object detail (SRS 3.3)."""
import re

from flask import Blueprint, abort, request

from orbitwatch import db
from orbitwatch.web.common import (account, clean, csv_response, current_user, int_arg, memo, ok, page_args,
                                   require_analyst, wants_csv)

bp = Blueprint("catalog_api", __name__, url_prefix="/api")

OBJECT_TYPES = ("Payload", "Rocket Body", "Debris", "Unknown")
# sort key -> (extra join, ORDER BY) over space_object (alias so). Every order ends in the primary key, so the pages
# of one search never overlap or skip a row when names or dates tie.
SORTS = {
    "norad": ("", "so.norad_id"),
    "name": ("", "so.name, so.norad_id"),
    "launch": (" LEFT JOIN launch l ON l.launch_id = so.launch_id", "l.launch_date DESC, so.norad_id"),
    "perigee": (" LEFT JOIN current_orbit co ON co.norad_id = so.norad_id", "co.perigee_km, so.norad_id"),
    "type": ("", "so.object_type, so.name, so.norad_id"),
}
_VIEW_COLUMNS = re.compile(r"\b(?:so|l|co)\.")      # the same order, written for the columns of v_object_catalog


@bp.get("/stats")
def stats():
    acct = account()
    return ok(memo(("stats", acct), lambda: _stats(acct)))


def _stats(acct):
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
    # Objects whose current owner is India (ISRO, NSIL, Pixxel, ...): what the site's readers care most about.
    india = db.query_one(acct, """
        SELECT COUNT(*) AS owned,
               COUNT(CASE WHEN so.in_earth_orbit THEN 1 END) AS in_orbit,
               COUNT(CASE WHEN so.in_earth_orbit AND so.object_type = 'Payload' THEN 1 END) AS payloads_in_orbit,
               COUNT(CASE WHEN so.in_earth_orbit AND co.norad_id IS NOT NULL THEN 1 END) AS tracked
          FROM v_current_owner o JOIN space_object so ON so.norad_id = o.norad_id
          LEFT JOIN current_orbit co ON co.norad_id = so.norad_id
         WHERE o.country_code = 'IN'""")
    counts.update(india_owned=india["owned"], india_in_orbit=india["in_orbit"],
                  india_payloads_in_orbit=india["payloads_in_orbit"], india_tracked=india["tracked"])
    upcoming = db.query(acct, """
        SELECT risk_level, COUNT(*) AS n FROM conjunction_event
         WHERE time_of_closest_approach BETWEEN UTC_TIMESTAMP() AND UTC_TIMESTAMP() + INTERVAL 7 DAY
         GROUP BY risk_level""")
    threshold = db.query_one(acct, "SELECT config_value FROM system_config WHERE config_key = 'screening_threshold_km'")
    return {
        "counts": counts,
        "upcoming_by_risk": {r["risk_level"]: r["n"] for r in upcoming},
        "regions": clean(db.query(acct, "SELECT * FROM v_report_region_counts ORDER BY min_altitude_km")),
        "last_update": {r["job_name"]: clean(r)["last_success"] for r in db.query(acct, "SELECT * FROM v_last_update")},
        "screening_threshold_km": float(threshold["config_value"]) if threshold else None,
    }


@bp.get("/lookups")
def lookups():
    acct = account()
    return ok(memo(("lookups", acct), lambda: _lookups(acct), factor=6))      # changes only with an ingest


def _lookups(acct):
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
    return {"object_types": OBJECT_TYPES, "countries": countries, "organisations": orgs,
            "regions": clean(regions), "risk_levels": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]}


def _object_filters(skip=()):
    """The catalogue filters of this request as WHERE conditions on space_object (alias so).

    Searching space_object alone finds the matching ids quickly; the wide v_object_catalog view (five joins, one of
    them a range join over every orbit) is then read for just those ids. Country, owner, orbit and region are
    semi-joins, so an object with two current owners still appears once.
    """
    where, params = [], []
    q = (request.args.get("q") or "").strip()
    if q:
        if q.isdigit():
            where.append("(so.norad_id = %s OR so.name LIKE %s)")
            params += [int(q), f"%{q}%"]
        elif re.match(r"^\d{4}-\d{3}", q):
            where.append("so.intl_designator LIKE %s")
            params.append(f"{q}%")
        else:
            where.append("so.name LIKE %s")
            params.append(f"%{q}%")
    norad = int_arg("norad")
    if norad is not None:
        where.append("so.norad_id = %s")
        params.append(norad)
    if "type" not in skip and request.args.get("type") in OBJECT_TYPES:
        where.append("so.object_type = %s")
        params.append(request.args["type"])
    if request.args.get("country"):
        where.append("EXISTS (SELECT 1 FROM v_current_owner cw WHERE cw.norad_id = so.norad_id AND cw.country_code = %s)")
        params.append(request.args["country"])
    if request.args.get("org"):
        where.append("EXISTS (SELECT 1 FROM v_current_owner cw WHERE cw.norad_id = so.norad_id AND cw.org_id = %s)")
        params.append(request.args["org"])
    region = int_arg("region")
    if region is not None:
        where.append("EXISTS (SELECT 1 FROM current_orbit co JOIN orbit_region r "
                     "ON co.mean_altitude_km >= r.min_altitude_km AND co.mean_altitude_km < r.max_altitude_km "
                     "WHERE co.norad_id = so.norad_id AND r.region_id = %s)")
        params.append(region)
    status = request.args.get("status", "onorbit")
    if status == "onorbit":
        where.append("so.in_earth_orbit")
    elif status == "decayed":
        where.append("so.decay_date IS NOT NULL")
    elif status == "beyond":  # probes and stages around the Sun, Moon, Mars, Lagrange points; docked objects
        where.append("so.decay_date IS NULL AND NOT so.in_earth_orbit")
    if request.args.get("has_orbit") == "1":
        where.append("EXISTS (SELECT 1 FROM current_orbit co WHERE co.norad_id = so.norad_id AND co.epoch IS NOT NULL)")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def _type_facets(acct):
    """How many objects there are of each type under every other filter of this request: the numbers on the type chips."""
    fwhere, fparams = _object_filters(skip=("type",))
    return {r["t"]: r["n"] for r in db.query(
        acct, f"SELECT so.object_type AS t, COUNT(*) AS n FROM space_object so{fwhere} GROUP BY so.object_type", fparams)}


FACET_ARGS = ("q", "norad", "country", "org", "region", "status", "has_orbit")


@bp.get("/objects/facets")
def object_facets():
    """The type counts alone. They are a GROUP BY over every matching row, so the catalogue asks for them in a request of
    its own, beside the one for the table, and the table never waits for them. Kept for a minute (see common.memo)."""
    acct = account()
    key = ("object_facets", acct, *(request.args.get(k) or "" for k in FACET_ARGS))
    return ok({"type": memo(key, lambda: _type_facets(acct), factor=3)})


@bp.get("/objects")
def objects():
    acct = account()
    where, params = _object_filters()
    join, order = SORTS.get(request.args.get("sort"), SORTS["norad"])
    order_params = []
    q = (request.args.get("q") or "").strip()
    if request.args.get("sort") == "relevance" and q:
        # the search box of the command palette: names that start with the term first, then objects still in orbit
        order, order_params = "(so.name LIKE %s) DESC, so.in_earth_orbit DESC, so.norad_id", [f"{q}%"]
    if wants_csv():
        require_analyst()
        rows = db.query(acct, "SELECT * FROM v_object_catalog WHERE norad_id IN (SELECT so.norad_id FROM space_object so"
                              f"{where}) ORDER BY {_VIEW_COLUMNS.sub('', order)} LIMIT 50000", params + order_params)
        return csv_response(rows, "orbitwatch_objects.csv")
    page, size = page_args()
    offset = (page - 1) * size
    ids = [r["norad_id"] for r in db.query(
        acct, f"SELECT so.norad_id FROM space_object so{join}{where} ORDER BY {order} LIMIT %s OFFSET %s",
        params + order_params + [size, offset])]
    if len(ids) < size and (ids or offset == 0):
        total = offset + len(ids)                      # a short page is the last one: nothing left to count
    else:
        total = db.query_one(acct, f"SELECT COUNT(*) AS n FROM space_object so{where}", params)["n"]
    facets = {"type": _type_facets(acct)} if request.args.get("facets") == "type" else None
    rows = {}
    if ids:
        for r in db.query(acct, "SELECT * FROM v_object_catalog WHERE norad_id IN (" + ",".join(["%s"] * len(ids)) + ")", ids):
            rows[r["norad_id"]] = r
    out = {"total": total, "page": page, "page_size": size, "items": clean([rows[i] for i in ids if i in rows])}
    if facets is not None:
        out["facets"] = facets
    return ok(out)


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
