"""/api/conjunctions: close-approach events (SRS 3.4) and their 3D replay data."""
from datetime import timedelta

import numpy as np
from flask import Blueprint, abort, request

from orbitwatch import db, orbital
from orbitwatch.web.common import (account, clean, csv_response, int_arg, memo, ok, page_args, parse_dt, require_analyst,
                                   wants_csv)

bp = Blueprint("conjunctions_api", __name__, url_prefix="/api/conjunctions")

RISKS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def _filters(skip=()):
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
    if risks and "risk" not in skip:
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
    out = {"total": total, "page": page, "page_size": size, "items": clean(rows)}
    if request.args.get("facets") == "risk":
        out["facets"] = {"risk": _risk_facets(acct)}
    return ok(out)


def _risk_facets(acct):
    """How many events there are of each risk level under every other filter of this request: the numbers on the risk chips."""
    fwhere, fparams = _filters(skip=("risk",))
    return {r["risk_level"]: r["n"] for r in db.query(
        acct, f"SELECT risk_level, COUNT(*) AS n FROM v_conjunction_detail{fwhere} GROUP BY risk_level", fparams)}


@bp.get("/facets")
def facets():
    """The risk counts alone, in a request of their own so that the table does not wait for them (see /api/objects/facets).
    A window of time is part of the key to the minute: the pages ask for "now" to the millisecond."""
    acct = account()
    key = ("conjunction_facets", acct, *(request.args.get(k) or "" for k in ("q", "norad", "when")),
           *((request.args.get(k) or "")[:16] for k in ("from", "to")))
    return ok({"risk": memo(key, lambda: _risk_facets(acct))})


@bp.get("/upcoming")
def upcoming():
    """Every close approach of the next `hours` hours (default 72), compact, for the dashboard's timeline and radar.

    `back` (hours, default 0) also reaches into the past: the globe's time scrubber shows a day either way.

    One query without paging or sorting by anything but time, kept for a few seconds (see common.memo): the page that
    draws it needs all of them at once, and the paged list needs a COUNT and a second request for each 500.
    """
    acct = account()
    hours = min(max(int_arg("hours", 72), 1), 168)
    back = min(max(int_arg("back", 0), 0), 168)
    risks = [r for r in (request.args.get("risk") or "CRITICAL,HIGH,MEDIUM").split(",") if r in RISKS] or ["CRITICAL", "HIGH", "MEDIUM"]

    def build():
        rows = db.query(acct, f"""
            SELECT event_id, time_of_closest_approach, risk_level, miss_distance_km, probability_of_collision,
                   primary_norad, primary_name, secondary_norad, secondary_name
              FROM v_conjunction_detail
             WHERE origin = 'orbitwatch' AND time_of_closest_approach BETWEEN UTC_TIMESTAMP() - INTERVAL %s HOUR AND UTC_TIMESTAMP() + INTERVAL %s HOUR
               AND risk_level IN ({','.join(['%s'] * len(risks))})
             ORDER BY time_of_closest_approach""", [back, hours, *risks])
        return {"hours": hours, "back": back, "items": [
            {"event_id": r["event_id"], "tca": clean(r["time_of_closest_approach"]), "risk": r["risk_level"],
             "miss_km": float(r["miss_distance_km"]), "pc": clean(r["probability_of_collision"]),
             "primary": {"norad_id": r["primary_norad"], "name": r["primary_name"]},
             "secondary": {"norad_id": r["secondary_norad"], "name": r["secondary_name"]}} for r in rows]}

    return ok(memo(("upcoming", acct, hours, back, tuple(risks)), build))


def _orbit(acct, norad_id):
    return db.query_one(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)}, perigee_km, apogee_km, "
                              "inclination, period_min, source, fetched_at FROM current_orbit WHERE norad_id = %s",
                        (norad_id,))


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
    archived = db.query_one(acct, "SELECT * FROM archive_risk_assessment WHERE event_id = %s", (event_id,)) \
        if ev.get("origin") == "orbitalguard-archive" else None
    return ok({"event": clean(ev), **objs, "archive_risk": clean(archived) if archived else None})


def track_window():
    """Replay window from the query string: seconds before and after TCA and the sample step.
    The defaults are the original 30 min either side every 10 s; the globe asks for a longer lead-in
    so a whole orbit can be watched before the encounter. Capped so one request stays small."""
    before = min(max(request.args.get("before", 1800, type=float), 60.0), 6 * 3600.0)
    after = min(max(request.args.get("after", 1800, type=float), 60.0), 3600.0)
    step = min(max(request.args.get("step", 10, type=float), 5.0), 60.0)
    while (before + after) / step > 2500:
        step *= 2
    return np.arange(-before, after + step / 2, step)


@bp.get("/<int:event_id>/track")
def event_track(event_id):
    """Positions of both objects around TCA (Earth-fixed, km): by default 30 min either side, every 10 s;
    ?before=&after=&step= (seconds) widen the window."""
    acct = account()
    ev = db.query_one(acct, "SELECT primary_norad, secondary_norad, time_of_closest_approach "
                            "FROM conjunction_event WHERE event_id = %s", (event_id,))
    if ev is None:
        abort(404, description="No such event")
    tca = ev["time_of_closest_approach"]
    offsets = track_window()
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
