"""/api/reports: analytical reports, orbital history queries and exports (SRS 3.6, 3.7).

Analyst or Administrator only. Relational reports come from MySQL views
and summary tables; history queries go to the sharded MongoDB collection
through the read-only ow_analyst MongoDB account.
"""
from datetime import timedelta

import numpy as np
from flask import Blueprint, abort, request

from orbitwatch import db, orbital
from orbitwatch.web.common import (account, clean, csv_response, int_arg, ok, parse_dt, role_required,
                                   wants_csv)

bp = Blueprint("reports_api", __name__, url_prefix="/api/reports")


def _report(rows, filename):
    return csv_response(rows, filename) if wants_csv() else ok({"items": clean(rows)})


@bp.get("/regions")
@role_required("analyst")
def regions():
    return _report(db.query(account(), "SELECT * FROM v_report_region_counts ORDER BY min_altitude_km"),
                   "objects_per_region.csv")


@bp.get("/debris-by-country")
@role_required("analyst")
def debris_by_country():
    order = "debris_in_leo DESC" if request.args.get("sort") == "leo" else "debris_on_orbit DESC"
    return _report(db.query(account(), f"SELECT * FROM v_report_debris_by_country ORDER BY {order}"),
                   "debris_by_country.csv")


@bp.get("/reentries-per-year")
@role_required("analyst")
def reentries_per_year():
    return _report(db.query(account(), "SELECT * FROM v_report_reentries_per_year ORDER BY year"),
                   "reentries_per_year.csv")


@bp.get("/region-year")
@role_required("analyst")
def region_year():
    return _report(db.query(account(), """
        SELECT s.year, r.region_id, r.name AS region_name, s.object_count, s.computed_at
          FROM summary_region_year s JOIN orbit_region r ON r.region_id = s.region_id
         ORDER BY s.year, r.min_altitude_km"""), "objects_per_region_per_year.csv")


@bp.get("/monthly-altitude")
@role_required("analyst")
def monthly_altitude():
    norad = int_arg("norad")
    if norad is None:
        abort(400, description="norad is required")
    return _report(db.query(account(), "SELECT * FROM summary_monthly_altitude WHERE norad_id = %s ORDER BY month",
                            (norad,)), f"monthly_altitude_{norad}.csv")


def _history(norad, start, end, limit=20000):
    query = {"norad_id": norad, "epoch": {"$gte": start, "$lte": end}}
    projection = {"_id": 0, "raw": 0, "download_id": 0, "run_id": 0}
    docs = list(db.mongo_db("analyst").orbit_history.find(query, projection).sort("epoch", 1).limit(limit))
    for d in docs:
        d["epoch"] = d["epoch"].replace(tzinfo=None)
        d["ingested_at"] = d["ingested_at"].replace(tzinfo=None) if d.get("ingested_at") else None
    return docs


def _period():
    end = parse_dt(request.args.get("to"), "to") or orbital.now_utc()
    start = parse_dt(request.args.get("from"), "from") or end - timedelta(days=730)
    if start >= end:
        abort(400, description="'from' must be before 'to'")
    return start, end


@bp.get("/history")
@role_required("analyst")
def history():
    norad = int_arg("norad")
    if norad is None:
        abort(400, description="norad is required")
    start, end = _period()
    docs = _history(norad, start, end)
    if wants_csv():
        return csv_response(docs, f"orbit_history_{norad}.csv",
                            ["norad_id", "epoch", "source", "mean_altitude_km", "perigee_km", "apogee_km",
                             "period_min", "inclination", "eccentricity", "raan", "arg_perigee", "mean_anomaly",
                             "mean_motion", "bstar", "mean_motion_dot", "mean_motion_ddot", "element_set_no"])
    return ok({"norad_id": norad, "from": start.isoformat(), "to": end.isoformat(), "count": len(docs),
               "items": clean(docs)})


@bp.get("/altitude-loss")
@role_required("analyst")
def altitude_loss():
    """Rate of altitude loss over a chosen period: least-squares slope of mean altitude vs time."""
    norad = int_arg("norad")
    if norad is None:
        abort(400, description="norad is required")
    start, end = _period()
    docs = _history(norad, start, end)
    name = db.query_one(account(), "SELECT name FROM space_object WHERE norad_id = %s", (norad,))
    result = {"norad_id": norad, "name": name["name"] if name else None, "from": start.isoformat(),
              "to": end.isoformat(), "samples": len(docs), "series": [
                  {"epoch": d["epoch"].isoformat() + "Z", "mean_altitude_km": round(d["mean_altitude_km"], 3),
                   "perigee_km": round(d["perigee_km"], 3), "apogee_km": round(d["apogee_km"], 3)} for d in docs]}
    if len(docs) >= 2:
        t = np.array([(d["epoch"] - docs[0]["epoch"]).total_seconds() / 86400.0 for d in docs])
        alt = np.array([d["mean_altitude_km"] for d in docs])
        if t[-1] > 0:
            slope = float(np.polyfit(t, alt, 1)[0])
            result.update(rate_km_per_day=slope, rate_km_per_year=slope * 365.25,
                          change_km=float(alt[-1] - alt[0]), span_days=float(t[-1]))
    if wants_csv():
        return csv_response(result["series"], f"altitude_loss_{norad}.csv")
    return ok(result)


@bp.get("/fastest-decaying")
@role_required("analyst")
def fastest_decaying():
    """Objects losing altitude fastest over the last N days (aggregation over the history)."""
    days = min(max(int_arg("days", 30), 2), 365)
    since = orbital.now_utc() - timedelta(days=days)
    pipeline = [
        {"$match": {"epoch": {"$gte": since}}},
        {"$sort": {"norad_id": 1, "epoch": 1}},
        {"$group": {"_id": "$norad_id", "first_alt": {"$first": "$mean_altitude_km"},
                    "last_alt": {"$last": "$mean_altitude_km"}, "first": {"$first": "$epoch"},
                    "last": {"$last": "$epoch"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": 3}}},
        {"$project": {"span_days": {"$divide": [{"$subtract": ["$last", "$first"]}, 86400000]},
                      "change_km": {"$subtract": ["$last_alt", "$first_alt"]}, "last_alt": 1, "n": 1}},
        {"$match": {"span_days": {"$gte": 1}}},
        {"$project": {"rate_km_per_day": {"$divide": ["$change_km", "$span_days"]}, "last_alt": 1, "n": 1,
                      "span_days": 1}},
        {"$sort": {"rate_km_per_day": 1}},
        {"$limit": 50},
    ]
    rows = list(db.mongo_db("analyst").orbit_history.aggregate(pipeline, allowDiskUse=True))
    names = {}
    if rows:
        ids = [r["_id"] for r in rows]
        for r in db.query(account(), f"SELECT norad_id, name, object_type FROM space_object WHERE norad_id IN "
                                     f"({','.join(['%s'] * len(ids))})", ids):
            names[r["norad_id"]] = r
    items = [{"norad_id": r["_id"], "name": names.get(r["_id"], {}).get("name"),
              "object_type": names.get(r["_id"], {}).get("object_type"),
              "rate_km_per_day": r["rate_km_per_day"], "current_altitude_km": r["last_alt"],
              "samples": r["n"], "span_days": r["span_days"]} for r in rows]
    return _report(items, f"fastest_decaying_{days}d.csv")
