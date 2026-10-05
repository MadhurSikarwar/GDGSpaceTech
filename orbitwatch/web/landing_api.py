"""/api/landing: everything the landing page shows, in one public request.

Runs on the Public Viewer account like any anonymous request, and is cached
for a minute (it is identical for every visitor).
"""
import threading
import time

import numpy as np
from flask import Blueprint

from orbitwatch import db, orbital
from orbitwatch.web.common import clean, ok

bp = Blueprint("landing_api", __name__, url_prefix="/api")

_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()
TTL_S = 60
HIST_BINS_PER_DECADE = 50


def _tca_location(acct, events):
    """Sub-satellite point of the watched object at each event's TCA (where the encounter happens)."""
    ids = sorted({e["primary_norad"] for e in events})
    if not ids:
        return
    rows = db.query(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)} FROM current_orbit "
                          f"WHERE norad_id IN ({','.join(['%s'] * len(ids))})", ids)
    sats = {r["norad_id"]: orbital.satrec_from_elements(r) for r in rows}
    for e in events:
        sat = sats.get(e["primary_norad"])
        if sat is None:
            continue
        jd, fr = orbital.julian(e["time_of_closest_approach"])
        err, r, _ = sat.sgp4(jd, fr)
        if err:
            continue
        ecef = orbital.teme_to_ecef(np.array(r), np.array(jd), np.array(fr))
        lat, lon, alt = orbital.ecef_to_geodetic(ecef)
        e["tca_lat"], e["tca_lon"], e["tca_alt_km"] = float(lat), float(lon), float(alt)


def _build():
    acct = "viewer"
    counts = db.query_one(acct, """
        SELECT COUNT(*) AS total,
               COUNT(CASE WHEN in_earth_orbit THEN 1 END) AS on_orbit,
               COUNT(CASE WHEN decay_date IS NOT NULL THEN 1 END) AS decayed,
               COUNT(CASE WHEN decay_date IS NULL AND NOT in_earth_orbit THEN 1 END) AS beyond_earth_orbit,
               COUNT(CASE WHEN in_earth_orbit AND object_type = 'Debris' THEN 1 END) AS debris_on_orbit,
               COUNT(CASE WHEN in_earth_orbit AND object_type = 'Payload' THEN 1 END) AS payloads_on_orbit,
               COUNT(CASE WHEN in_earth_orbit AND object_type = 'Rocket Body' THEN 1 END) AS rocket_bodies_on_orbit
          FROM space_object""")
    counts["with_current_orbit"] = db.query_one(acct, "SELECT COUNT(*) AS n FROM current_orbit")["n"]
    counts["watchlist"] = db.query_one(acct, "SELECT COUNT(*) AS n FROM watchlist")["n"]
    counts["countries"] = db.query_one(acct, "SELECT COUNT(DISTINCT country_code) AS n FROM v_current_owner")["n"]

    upcoming = db.query(acct, """
        SELECT * FROM v_conjunction_detail
         WHERE time_of_closest_approach >= UTC_TIMESTAMP()
         ORDER BY time_of_closest_approach LIMIT 30""")
    hot = db.query(acct, """
        SELECT * FROM v_conjunction_detail
         WHERE time_of_closest_approach >= UTC_TIMESTAMP() AND risk_level IN ('CRITICAL', 'HIGH')
         ORDER BY FIELD(risk_level, 'CRITICAL', 'HIGH'), time_of_closest_approach LIMIT 16""")
    _tca_location(acct, hot)
    week = db.query(acct, """
        SELECT risk_level, COUNT(*) AS n FROM conjunction_event
         WHERE time_of_closest_approach BETWEEN UTC_TIMESTAMP() AND UTC_TIMESTAMP() + INTERVAL 7 DAY
         GROUP BY risk_level""")

    # Log-binned altitude histogram of every object with a current orbit, by type.
    hist = db.query(acct, f"""
        SELECT FLOOR(LOG10(co.mean_altitude_km) * {HIST_BINS_PER_DECADE}) AS bin, so.object_type, COUNT(*) AS n
          FROM current_orbit co JOIN space_object so ON so.norad_id = co.norad_id
         WHERE co.mean_altitude_km BETWEEN 100 AND 100000
         GROUP BY bin, so.object_type""")
    regions = db.query(acct, "SELECT * FROM v_report_region_counts ORDER BY min_altitude_km")
    reentries = db.query(acct, """
        SELECT YEAR(decay_date) AS year, COUNT(*) AS n FROM space_object
         WHERE decay_date IS NOT NULL GROUP BY YEAR(decay_date) ORDER BY year""")
    fragmentations = db.query(acct, """
        SELECT p.norad_id, p.name, p.intl_designator, p.object_type, COUNT(*) AS fragments,
               COUNT(CASE WHEN c.in_earth_orbit THEN 1 END) AS on_orbit
          FROM space_object c JOIN space_object p ON p.norad_id = c.parent_norad_id
         WHERE c.object_type = 'Debris'
         GROUP BY p.norad_id, p.name, p.intl_designator, p.object_type
         ORDER BY fragments DESC LIMIT 8""")
    spotlight = db.query_one(acct, """
        SELECT c.norad_id, c.name, c.intl_designator, c.object_type, c.status, c.org_name, c.country_name,
               c.launch_date, c.region_name, co.perigee_km, co.apogee_km, co.inclination, co.period_min,
               co.mean_altitude_km, co.epoch, lv.name AS vehicle, ls.name AS site
          FROM v_object_catalog c
          LEFT JOIN current_orbit co ON co.norad_id = c.norad_id
          LEFT JOIN launch l ON l.launch_id = c.launch_id
          LEFT JOIN launch_vehicle lv ON lv.vehicle_id = l.vehicle_id
          LEFT JOIN launch_site ls ON ls.site_id = l.site_id
         WHERE c.norad_id = 25544""")
    # Orbits drawn on the hero globe: the ISS plus one sun-synchronous and one geostationary watched satellite.
    featured = [25544] if spotlight and spotlight.get("epoch") else []
    for where in ("co.inclination BETWEEN 96 AND 100 AND co.mean_altitude_km < 900",
                  "co.mean_altitude_km BETWEEN 35586 AND 35986"):
        row = db.query_one(acct, f"""
            SELECT w.norad_id, so.name FROM watchlist w
              JOIN current_orbit co ON co.norad_id = w.norad_id
              JOIN space_object so ON so.norad_id = w.norad_id
             WHERE {where} ORDER BY co.epoch DESC LIMIT 1""")
        if row:
            featured.append(row["norad_id"])
    # Agentic decision support: the most recent completed assessment and its trace.
    ai = db.query_one(acct, """
        SELECT a.assessment_id, a.event_id, a.engine, a.risk_tier, a.decision, a.delta_v_mps, a.pc_before, a.pc_after,
               a.burn_direction, a.burn_time, a.explanation, a.finished_at, d.primary_name, d.secondary_name,
               d.miss_distance_km, d.time_of_closest_approach
          FROM agent_assessment a JOIN v_conjunction_detail d ON d.event_id = a.event_id
         WHERE a.status = 'complete' ORDER BY a.assessment_id DESC LIMIT 1""")
    if ai:
        ai["steps"] = db.query(acct, "SELECT step_no, actor, tool_name, summary FROM agent_step "
                                     "WHERE assessment_id = %s ORDER BY step_no", (ai["assessment_id"],))
    ai_count = db.query_one(acct, "SELECT COUNT(*) AS n FROM agent_assessment WHERE status = 'complete'")["n"]
    sw = db.query_one(acct, "SELECT observed_at, kp, ap, f107, activity, drag_scalar, live FROM space_weather "
                            "ORDER BY observed_at DESC LIMIT 1")
    stations = db.query_one(acct, "SELECT COUNT(*) AS n FROM ground_station")["n"]
    threshold = db.query_one(acct, "SELECT config_value FROM system_config WHERE config_key = 'screening_threshold_km'")
    interval = db.query_one(acct, "SELECT config_value FROM system_config WHERE config_key = 'ingest_interval_hours'")
    last = {r["job_name"]: r["last_success"] for r in db.query(acct, "SELECT * FROM v_last_update")}

    return {
        "generated_at": orbital.now_utc().isoformat() + "Z",
        "counts": counts,
        "upcoming": clean(upcoming),
        "hot": clean(hot),
        "week_by_risk": {r["risk_level"]: r["n"] for r in week},
        "histogram": {"bins_per_decade": HIST_BINS_PER_DECADE,
                      "rows": [{"bin": int(r["bin"]), "type": r["object_type"], "n": r["n"]} for r in hist]},
        "regions": clean(regions),
        "reentries": reentries,
        "fragmentations": fragmentations,
        "spotlight": clean(spotlight) if spotlight else None,
        "featured": featured,
        "screening_threshold_km": float(threshold["config_value"]) if threshold else None,
        "ingest_interval_hours": float(interval["config_value"]) if interval else None,
        "last_update": clean(last),
        "ai": ({**clean({k: v for k, v in ai.items() if k != "steps"}), "steps": clean(ai["steps"])} if ai else None),
        "ai_assessments": ai_count,
        "space_weather": clean(sw) if sw else None,
        "ground_stations": stations,
    }


@bp.get("/landing")
def landing():
    with _lock:
        if _cache["data"] is None or time.time() - _cache["at"] > TTL_S:
            _cache["data"] = _build()
            _cache["at"] = time.time()
        return ok(_cache["data"])
