"""Agentic decision support, space weather and ground stations (features carried over from OrbitalGuard).

Anyone can read assessments; running the agent is an Analyst function and
runs on the analyst's own MySQL account (the role was granted INSERT on
agent_assessment / agent_step for exactly this).
"""
import json

from flask import Blueprint, abort, request

from orbitwatch import db, decisions, orbital
from orbitwatch.agent import orchestrator
from orbitwatch.physics import ground, spaceweather
from orbitwatch.web.common import account, body, clean, current_user, int_arg, ok, role_required

bp = Blueprint("agent_api", __name__, url_prefix="/api")


def _json(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _assessment(acct, assessment_id, with_steps=True):
    row = db.query_one(acct, "SELECT * FROM agent_assessment WHERE assessment_id = %s", (assessment_id,))
    if row is None:
        return None
    row["maneuver"] = _json(row["maneuver"])
    out = clean(row)
    try:
        full = decisions.load(acct, assessment_id)
        out["subject"] = clean(full["subject"])
        out["synthetic"] = full["synthetic"]
        out["decision_record"] = clean(full["decision_record"]) if full["decision_record"] else None
    except decisions.DecisionError:
        out["subject"], out["synthetic"], out["decision_record"] = {}, bool(row.get("demo_event_id")), None
    out["replans"] = clean(db.query(acct, "SELECT assessment_id, status, decision, started_at FROM agent_assessment "
                                          "WHERE parent_assessment_id = %s ORDER BY assessment_id", (assessment_id,)))
    user = current_user()
    burn = (row["maneuver"] or {}).get("burn_time_utc") if isinstance(row["maneuver"], dict) else None
    out["can_decide"] = bool(user and user["role"] in ("analyst", "admin") and row["status"] == "complete"
                             and row.get("origin") == "orbitwatch" and not out["decision_record"])
    out["burn_time_utc"] = burn
    if with_steps:
        steps = db.query(acct, "SELECT step_no, actor, tool_name, arguments, summary, payload, created_at "
                               "FROM agent_step WHERE assessment_id = %s ORDER BY step_no", (assessment_id,))
        for s in steps:
            s["arguments"], s["payload"] = _json(s["arguments"]), _json(s["payload"])
        out["steps"] = clean(steps)
    return out


@bp.get("/agent/status")
def agent_status():
    cfg = orchestrator.llm_config()
    return ok({"llm_enabled": cfg["enabled"], "configured_model": cfg["configured_model"],
               "mode": "Groq LLM with tool calling" if cfg["enabled"] else "deterministic workflow",
               "can_run": bool(current_user()) and current_user()["role"] in ("analyst", "admin")})


@bp.post("/conjunctions/<int:event_id>/assess")
@role_required("analyst")
def assess(event_id):
    acct = account()
    if not db.query_one(acct, "SELECT 1 AS x FROM conjunction_event WHERE event_id = %s", (event_id,)):
        abort(404, description="No such event")
    running = db.query_one(acct, "SELECT assessment_id FROM agent_assessment WHERE event_id = %s AND status = 'running' "
                                 "AND started_at > UTC_TIMESTAMP(3) - INTERVAL 5 MINUTE", (event_id,))
    if running:
        return ok({"assessment_id": running["assessment_id"], "already_running": True}, 202)
    aid = orchestrator.start(event_id, current_user()["user_id"], acct)
    return ok({"assessment_id": aid}, 202)


@bp.get("/conjunctions/<int:event_id>/assessments")
def event_assessments(event_id):
    rows = db.query(account(), "SELECT assessment_id, status, engine, risk_tier, decision, delta_v_mps, pc_before, "
                               "pc_after, started_at, finished_at FROM agent_assessment WHERE event_id = %s "
                               "ORDER BY assessment_id DESC LIMIT 20", (event_id,))
    return ok({"items": clean(rows)})


@bp.get("/assessments/<int:assessment_id>")
def assessment(assessment_id):
    a = _assessment(account(), assessment_id)
    if a is None:
        abort(404, description="No such assessment")
    return ok(a)


@bp.get("/assessments")
def recent_assessments():
    """Recent agent runs on real and synthetic close approaches (synthetic ones are flagged)."""
    archived = "" if request.args.get("archived") == "1" else "AND a.origin = 'orbitwatch'"
    rows = db.query(account(), f"""
        SELECT a.assessment_id, a.event_id, a.demo_event_id, a.demo_event_id IS NOT NULL AS synthetic, a.origin,
               a.status, a.engine, a.risk_tier, a.decision, a.delta_v_mps, a.pc_before, a.pc_after, a.burn_direction,
               a.burn_time, a.started_at, a.finished_at, a.parent_assessment_id,
               COALESCE(d.primary_name, tso.name) AS primary_name, COALESCE(d.secondary_name, dob.name) AS secondary_name,
               COALESCE(d.time_of_closest_approach, de.time_of_closest_approach) AS time_of_closest_approach,
               COALESCE(d.miss_distance_km, de.miss_distance_km) AS miss_distance_km,
               md.status AS decision_status, md.execution
          FROM agent_assessment a
          LEFT JOIN v_conjunction_detail d ON d.event_id = a.event_id
          LEFT JOIN demo_event de ON de.demo_event_id = a.demo_event_id
          LEFT JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
          LEFT JOIN space_object tso ON tso.norad_id = de.target_norad
          LEFT JOIN maneuver_decision md ON md.assessment_id = a.assessment_id
         WHERE TRUE {archived}
         ORDER BY a.assessment_id DESC LIMIT %s""", (min(int_arg("limit", 10), 100),))
    for r in rows:
        r["synthetic"] = bool(r["synthetic"])
    return ok({"items": clean(rows)})


@bp.post("/assessments/<int:assessment_id>/decision")
@role_required("analyst")
def decide(assessment_id):
    """Approve (simulated execution) or reject the recommended manoeuvre."""
    data = body()
    try:
        out = decisions.decide(account(), current_user(), assessment_id, data.get("action"),
                               reason=data.get("reason"), confirm=data.get("confirm"), replan=bool(data.get("replan")),
                               min_miss_km=data.get("min_miss_km"))
    except decisions.DecisionError as exc:
        return ok({"error": str(exc)}, exc.status)
    return ok(clean(out) if isinstance(out, dict) else out, 201)


@bp.get("/assessments/<int:assessment_id>/simulation")
def simulation(assessment_id):
    """Before/after trajectories of the recommended burn (labelled SIMULATED)."""
    acct = account()
    try:
        return ok(decisions.simulate(acct, decisions.load(acct, assessment_id)))
    except decisions.DecisionError as exc:
        return ok({"error": str(exc)}, exc.status)
    except ValueError as exc:
        return ok({"error": str(exc)}, 422)


@bp.get("/space-weather")
def space_weather():
    from orbitwatch.jobs.spaceweather import latest
    row = latest(account(), max_age_hours=12)
    if row:
        return ok({**clean(row), "live": bool(row["live"]), "stored": True})
    live = spaceweather.fetch()
    return ok({**clean(live), "stored": False})


@bp.get("/ground-stations")
def ground_stations():
    return ok({"items": clean(db.query(account(), "SELECT * FROM ground_station ORDER BY latitude DESC"))})


@bp.get("/objects/<int:norad_id>/passes")
def passes(norad_id):
    acct = account()
    el = db.query_one(acct, f"SELECT norad_id, epoch, {', '.join(orbital.ELEMENT_FIELDS)} FROM current_orbit "
                            "WHERE norad_id = %s", (norad_id,))
    if el is None:
        abort(404, description="No current orbit for this object")
    hours = min(max(int_arg("hours", 24), 1), 72)
    stations = db.query(acct, "SELECT * FROM ground_station")
    items = ground.passes(el, stations, orbital.now_utc(), hours=hours)
    return ok({"norad_id": norad_id, "hours": hours, "items": clean(items)})
