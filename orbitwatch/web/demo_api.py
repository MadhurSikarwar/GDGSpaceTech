"""/api/demo: the synthetic-debris demo (restored from OrbitalGuard).

Anyone can see active demo scenarios (they are labelled SYNTHETIC everywhere);
creating, assessing and clearing them is an Analyst function. Synthetic data
lives only in the demo tables and never reaches catalogue statistics.
"""
from flask import Blueprint, abort, request

from orbitwatch import db, demo, orbital
from orbitwatch.agent import orchestrator
from orbitwatch.web.common import account, body, clean, current_user, ok, parse_dt, role_required

bp = Blueprint("demo_api", __name__, url_prefix="/api/demo")


@bp.get("")
def scenarios():
    acct = account()
    rows = demo.list_scenarios(acct, include_archived=request.args.get("archived") == "1")
    items = []
    for r in rows:
        sc = demo.get(acct, r["scenario_id"]) if r["status"] == "active" else r
        items.append(clean({k: v for k, v in sc.items() if k not in ("objects", "events")}) |
                     ({"objects": clean(sc["objects"]), "events": clean(sc["events"])} if r["status"] == "active" else {}))
    return ok({"items": items, "synthetic": True, "max_active": demo.MAX_ACTIVE_SCENARIOS})


@bp.post("")
@role_required("analyst")
def create():
    data = body()
    try:
        norad = int(data.get("target_norad"))
    except (TypeError, ValueError):
        abort(400, description="target_norad must be a NORAD catalogue number")
    try:
        sc = demo.create(norad, current_user()["user_id"], account(),
                         lead_min=float(data.get("lead_min", demo.DEFAULT_LEAD_MIN)),
                         miss_km=float(data.get("miss_km", demo.DEFAULT_MISS_KM)),
                         crossing_deg=float(data.get("crossing_deg", demo.DEFAULT_CROSSING_DEG)))
    except (TypeError, ValueError) as exc:   # DemoError is a ValueError
        return ok({"error": str(exc)}, 400)
    return ok(clean({k: v for k, v in sc.items() if k not in ("objects", "events")})
              | {"objects": clean(sc["objects"]), "events": clean(sc["events"])}, 201)


@bp.delete("/<int:scenario_id>")
@role_required("analyst")
def clear_one(scenario_id):
    return ok({"cleared": demo.clear(account(), current_user()["user_id"], scenario_id)})


@bp.post("/clear")
@role_required("analyst")
def clear_all():
    return ok({"cleared": demo.clear(account(), current_user()["user_id"])})


@bp.get("/positions")
def positions():
    t = parse_dt(request.args.get("t"), "t") or orbital.now_utc()
    span = request.args.get("span", type=float)
    span = min(max(span, 1.0), 600.0) if span else None
    return ok({"t": t.isoformat() + "Z", **demo.positions(account(), t, span)})


@bp.post("/events/<int:demo_event_id>/assess")
@role_required("analyst")
def assess(demo_event_id):
    acct = account()
    if not db.query_one(acct, "SELECT 1 AS x FROM demo_event WHERE demo_event_id = %s", (demo_event_id,)):
        abort(404, description="No such synthetic close approach")
    running = db.query_one(acct, "SELECT assessment_id FROM agent_assessment WHERE demo_event_id = %s AND status = "
                                 "'running' AND started_at > UTC_TIMESTAMP(3) - INTERVAL 5 MINUTE", (demo_event_id,))
    if running:
        return ok({"assessment_id": running["assessment_id"], "already_running": True}, 202)
    aid = orchestrator.start(None, current_user()["user_id"], acct, demo_event_id=demo_event_id)
    return ok({"assessment_id": aid, "synthetic": True}, 202)


@bp.get("/events/<int:demo_event_id>/assessments")
def event_assessments(demo_event_id):
    rows = db.query(account(), "SELECT assessment_id, status, engine, risk_tier, decision, delta_v_mps, pc_before, "
                               "pc_after, started_at, finished_at FROM agent_assessment WHERE demo_event_id = %s "
                               "ORDER BY assessment_id DESC LIMIT 20", (demo_event_id,))
    return ok({"items": clean(rows)})


@bp.get("/events/<int:demo_event_id>/track")
def event_track(demo_event_id):
    """Both objects from 30 min before to 30 min after TCA (Earth-fixed km, every 10 s); the secondary is SYNTHETIC."""
    import numpy as np
    acct = account()
    ev = db.query_one(acct, f"""
        SELECT de.time_of_closest_approach, de.target_norad, dob.designation, dob.name, dob.epoch,
               {', '.join('dob.' + f for f in orbital.ELEMENT_FIELDS)}
          FROM demo_event de JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
         WHERE de.demo_event_id = %s""", (demo_event_id,))
    if ev is None:
        abort(404, description="No such synthetic close approach")
    target = db.query_one(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)} FROM current_orbit "
                                "WHERE norad_id = %s", (ev["target_norad"],))
    tca = ev["time_of_closest_approach"]
    offsets = np.arange(-1800, 1801, 10.0)
    jd, fr = orbital.time_grid(tca, offsets)
    out = {"tca": clean({"t": tca})["t"], "offsets_s": offsets.tolist(), "objects": [], "synthetic": True}
    for el, ident in ((target, target["norad_id"] if target else None), ({**ev, "norad_id": 0}, ev["designation"])):
        if el is None:
            continue
        err, r, _ = orbital.propagate([orbital.satrec_from_elements(el)], jd, fr)
        ecef = orbital.teme_to_ecef(r[0], jd, fr)
        mask = err[0] == 0
        out["objects"].append({"norad_id": ident, "ecef_km": np.round(ecef[mask], 3).tolist(),
                               "offsets_s": offsets[mask].tolist()})
    return ok(out)
