"""/api/conjunctions: close-approach events (SRS 3.4) and their 3D replay data."""
from datetime import timedelta

import numpy as np
from flask import Blueprint, abort, request

from orbitwatch import db, orbital
from orbitwatch.web.common import (account, clean, csv_response, int_arg, ok, page_args, parse_dt, require_analyst,
                                   wants_csv)

bp = Blueprint("conjunctions_api", __name__, url_prefix="/api/conjunctions")

RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _filters():
    where, params = [], []
    norad = int_arg("norad")
    if norad is not None:
        where.append("(primary_norad = %s OR secondary_norad = %s)")
        params += [norad, norad]
    start, end = parse_dt(request.args.get("from"), "from"), parse_dt(request.args.get("to"), "to")
    if start:
        where.append("time_of_closest_approach >= %s")
        params.append(start)
    if end:
        where.append("time_of_closest_approach <= %s")
        params.append(end)
    if not start and not end and request.args.get("when", "upcoming") == "upcoming":
        where.append("time_of_closest_approach >= UTC_TIMESTAMP() - INTERVAL 15 MINUTE")
    risks = [r for r in (request.args.get("risk") or "").split(",") if r in RISKS]
    if risks:
        where.append(f"risk_level IN ({','.join(['%s'] * len(risks))})")
        params += risks
    q = (request.args.get("q") or "").strip()
    if q:
        where.append("(primary_name LIKE %s OR secondary_name LIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    return (" WHERE " + " AND ".join(where)) if where else "", params


@bp.get("")
def list_events():
    acct = account()
    where, params = _filters()
    order = "time_of_closest_approach" if request.args.get("when", "upcoming") == "upcoming" else \
        "time_of_closest_approach DESC"
    if request.args.get("sort") == "miss":
        order = "miss_distance_km"
    if wants_csv():
        require_analyst()
        rows = db.query(acct, f"SELECT * FROM v_conjunction_detail{where} ORDER BY {order} LIMIT 50000", params)
        return csv_response(rows, "orbitwatch_conjunctions.csv")
    page, size = page_args()
    total = db.query_one(acct, f"SELECT COUNT(*) AS n FROM v_conjunction_detail{where}", params)["n"]
    rows = db.query(acct, f"SELECT * FROM v_conjunction_detail{where} ORDER BY {order} LIMIT %s OFFSET %s",
                    params + [size, (page - 1) * size])
    return ok({"total": total, "page": page, "page_size": size, "items": clean(rows)})


def _orbit(acct, norad_id):
    return db.query_one(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)}, perigee_km, apogee_km, "
                              "inclination, period_min FROM current_orbit WHERE norad_id = %s", (norad_id,))


@bp.get("/<int:event_id>")
def event_detail(event_id):
    acct = account()
    ev = db.query_one(acct, "SELECT * FROM v_conjunction_detail WHERE event_id = %s", (event_id,))
    if ev is None:
        abort(404, description="No such event")
    objs = {}
    for role in ("primary", "secondary"):
        n = ev[f"{role}_norad"]
        info = db.query_one(acct, "SELECT norad_id, name, object_type, status, country_name, org_name, region_name "
                                  "FROM v_object_catalog WHERE norad_id = %s", (n,))
        objs[role] = {**clean(info), "orbit": clean(_orbit(acct, n) or {})}
    return ok({"event": clean(ev), **objs})


@bp.get("/<int:event_id>/track")
def event_track(event_id):
    """Positions of both objects from 30 min before to 30 min after TCA (Earth-fixed, km), every 10 s."""
    acct = account()
    ev = db.query_one(acct, "SELECT primary_norad, secondary_norad, time_of_closest_approach "
                            "FROM conjunction_event WHERE event_id = %s", (event_id,))
    if ev is None:
        abort(404, description="No such event")
    tca = ev["time_of_closest_approach"]
    offsets = np.arange(-1800, 1801, 10.0)
    jd, fr = orbital.time_grid(tca, offsets)
    out = {"tca": clean({"t": tca})["t"], "offsets_s": offsets.tolist(), "objects": []}
    for n in (ev["primary_norad"], ev["secondary_norad"]):
        el = _orbit(acct, n)
        if el is None:
            continue
        err, r, v = orbital.propagate([orbital.satrec_from_elements(el)], jd, fr)
        ecef = orbital.teme_to_ecef(r[0], jd, fr)
        ok_mask = err[0] == 0
        out["objects"].append({"norad_id": n, "ecef_km": np.round(ecef[ok_mask], 3).tolist(),
                               "offsets_s": offsets[ok_mask].tolist()})
    return ok(out)
