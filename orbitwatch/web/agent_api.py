"""Agentic decision support, space weather and ground stations (features carried over from OrbitalGuard).

Anyone can read assessments; running the agent is an Analyst function and
runs on the analyst's own MySQL account (the role was granted INSERT on
agent_assessment / agent_step for exactly this).
"""
import json

from flask import Blueprint, Response, abort, request

from orbitwatch import db, decisions, orbital
from orbitwatch.agent import orchestrator
from orbitwatch.physics import ground, spaceweather
from orbitwatch.web.common import (account, body, clean, csv_response, current_user, int_arg, ok, require_analyst,
                                   role_required)

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


# What a side-by-side comparison shows, in order: (key, label, how to read the value from an assessment)
def _miss_after(a):
    m = a.get("maneuver")
    return m.get("miss_after_km") if isinstance(m, dict) else None


def _lead(a):
    m = a.get("maneuver")
    return m.get("lead_time_min") if isinstance(m, dict) else None


COMPARE_FIELDS = [
    ("assessment_id", "Assessment", lambda a: a["assessment_id"]),
    ("started_at", "Started", lambda a: a["started_at"]),
    ("engine", "Engine", lambda a: a["engine"]),
    ("status", "Status", lambda a: a["status"]),
    ("parent_assessment_id", "Re-plan of", lambda a: a.get("parent_assessment_id")),
    ("feedback", "Reviewer feedback it answers", lambda a: a.get("feedback")),
    ("min_miss_km", "Required miss distance (km)", lambda a: a.get("min_miss_km")),
    ("risk_tier", "Risk tier", lambda a: a["risk_tier"]),
    ("decision", "Recommendation", lambda a: a["decision"]),
    ("pc_before", "Probability of collision before", lambda a: a["pc_before"]),
    ("pc_after", "Probability of collision after", lambda a: a["pc_after"]),
    ("delta_v_mps", "Delta-v (m/s)", lambda a: a["delta_v_mps"]),
    ("burn_direction", "Burn direction", lambda a: a["burn_direction"]),
    ("burn_time", "Burn time", lambda a: a["burn_time"]),
    ("lead_time_min", "Lead time before TCA (min)", _lead),
    ("miss_after_km", "Miss distance after the burn (km)", _miss_after),
    ("review", "Human review", lambda a: (a["decision_record"] or {}).get("status")),
    ("review_reason", "Review reason", lambda a: (a["decision_record"] or {}).get("reason")),
    ("explanation", "Explanation", lambda a: a.get("explanation")),
]
LOWER_IS_BETTER = {"pc_before", "pc_after", "delta_v_mps"}
HIGHER_IS_BETTER = {"miss_after_km", "lead_time_min"}


def _change(key, va, vb):
    """How B differs from A, in words a report can quote ('' when equal or not comparable)."""
    if va == vb:
        return ""
    if isinstance(va, (int, float)) and isinstance(vb, (int, float)) and not isinstance(va, bool):
        if key in ("pc_before", "pc_after") and va > 0 and vb > 0:
            # Probabilities span orders of magnitude: a factor says more than "-100%".
            ratio = vb / va
            return f"{1 / ratio:.2g}x lower (better)" if ratio < 1 else f"{ratio:.2g}x higher (worse)"
        if key in LOWER_IS_BETTER | HIGHER_IS_BETTER and va:
            pct = (vb - va) / abs(va) * 100
            better = (vb < va) == (key in LOWER_IS_BETTER)
            return f"{pct:+.0f}% ({'better' if better else 'worse'})"
        return "changed"
    return "changed"


def _options(a):
    """The burn candidates the agent generated and how each fared against the constraints."""
    steps = a.get("steps") or []
    gen = next((st for st in reversed(steps) if st["tool_name"] == "generate_maneuver_candidates"
                and isinstance(st["payload"], dict)), None)
    evals = {st["payload"].get("candidate_id"): st["payload"] for st in steps
             if st["tool_name"] == "evaluate_maneuver_constraints" and isinstance(st["payload"], dict)}
    chosen = (a.get("maneuver") or {}).get("candidate_id") if isinstance(a.get("maneuver"), dict) else None
    out = []
    for c in (gen["payload"].get("candidates") or []) if gen else []:
        e = evals.get(c.get("candidate_id"))
        out.append({"candidate_id": c.get("candidate_id"), "chosen": c.get("candidate_id") == chosen,
                    "dv_mps": c.get("dv_mps"), "direction": c.get("direction"), "lead_time_min": c.get("lead_time_min"),
                    "pc_after": c.get("pc_after"), "miss_after_km": c.get("miss_after_km"),
                    "sma_drift_km": c.get("sma_drift_km"),
                    "verdict": None if e is None else ("feasible" if e.get("feasible") else
                                                       "rejected: " + ", ".join(e.get("violations") or []))})
    return out


def _trace(a):
    return [{"step_no": st["step_no"], "actor": st["actor"], "tool_name": st["tool_name"], "summary": st["summary"]}
            for st in a.get("steps") or []]


def _comparison_pdf(data, a_id, b_id):
    from orbitwatch import pdf
    from orbitwatch.notify import both_times
    from datetime import datetime

    def show(field, v):
        if v is None or v == "":
            return "-"
        if field in ("pc_before", "pc_after"):
            return f"{float(v):.1e}"
        if isinstance(v, str) and len(v) >= 16 and v[4] == "-" and v[10] == "T":
            try:
                return both_times(datetime.fromisoformat(v.replace("Z", "")), seconds=False)
            except ValueError:
                return v
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    subj = data["subject"] or {}
    doc = pdf.Document(f"OrbitWatch - assessments #{a_id} vs #{b_id}")
    doc.heading(f"AI decision support: assessment #{a_id} vs #{b_id}")
    doc.paragraph(f"{subj.get('primary_name') or ''} vs {subj.get('secondary_name') or ''}"
                  + (f"  -  TCA {show('', subj.get('time_of_closest_approach'))}" if subj.get("time_of_closest_approach") else "")
                  + ("  -  SYNTHETIC demo event" if data.get("synthetic") else "")
                  + ("" if data["same_event"] else "  -  the two assessments are of different close approaches"))
    rows = [r for r in data["rows"] if r["field"] != "assessment_id"]
    doc.table(["", f"#{a_id} (earlier)", f"#{b_id} (later)", "Change"],
              [[r["label"], show(r["field"], r["a"]), show(r["field"], r["b"]), r["change"]] for r in rows],
              [2.2, 3, 3, 1.2], highlight=lambda i: bool(rows[i]["change"]))
    for key, aid in (("a", a_id), ("b", b_id)):
        opts = data["options"][key]
        doc.heading(f"Burn options of #{aid}", 11)
        if not opts:
            doc.paragraph("No manoeuvre candidates were generated.")
            continue
        doc.table(["Option", "dv m/s", "Direction", "Lead", "Pc after", "Miss after km", "Drift km", "Constraints"],
                  [[f"{o['candidate_id']}{' (chosen)' if o['chosen'] else ''}", show("", o["dv_mps"]), o["direction"] or "-",
                    f"T-{o['lead_time_min']:.0f} min" if o["lead_time_min"] is not None else "-", show("pc_after", o["pc_after"]),
                    show("", o["miss_after_km"]), show("", o["sma_drift_km"]), o["verdict"] or "not evaluated"] for o in opts],
                  [1.3, 0.9, 1.1, 1, 1, 1.1, 0.9, 2.4], highlight=lambda i, opts=opts: opts[i]["chosen"])
    for key, aid in (("a", a_id), ("b", b_id)):
        doc.heading(f"Agent trace of #{aid}", 11)
        doc.table(["#", "Who", "What"], [[t["step_no"], f"{t['actor']}{' - ' + t['tool_name'] if t['tool_name'] else ''}",
                                          t["summary"]] for t in data["trace"][key]], [0.4, 2.2, 7.4], size=7.5)
    doc.paragraph("Manoeuvres are executed in simulation only: OrbitWatch has no command uplink.", 7.5)
    return doc.render()


@bp.get("/assessments/compare")
def compare_assessments():
    """Two assessments side by side (typically a re-plan and the recommendation it replaced), with each one's burn
    options and agent trace. ?format=csv or ?format=pdf exports it (Analyst role, like every export)."""
    acct = account()
    a_id, b_id = int_arg("a"), int_arg("b")
    if not a_id or not b_id:
        abort(400, description="give two assessments: ?a=<id>&b=<id>")
    if a_id == b_id:
        abort(400, description="pick two different assessments")
    a, b = _assessment(acct, a_id), _assessment(acct, b_id)
    if a is None or b is None:
        abort(404, description="No such assessment")
    subject = {k: (a.get("subject") or {}).get(k) for k in ("primary_name", "secondary_name", "time_of_closest_approach",
                                                             "miss_distance_km")}
    rows = []
    for key, label, read in COMPARE_FIELDS:
        va, vb = read(a), read(b)
        rows.append({"field": key, "label": label, "a": va, "b": vb, "change": _change(key, va, vb)})
    data = clean({"a": a_id, "b": b_id, "same_event": (a.get("event_id"), a.get("demo_event_id")) ==
                  (b.get("event_id"), b.get("demo_event_id")), "subject": subject, "synthetic": a.get("synthetic"),
                  "rows": rows, "options": {"a": _options(a), "b": _options(b)}, "trace": {"a": _trace(a), "b": _trace(b)}})
    fmt_ = request.args.get("format")
    if fmt_ == "csv":
        require_analyst()
        return csv_response([{"field": r["label"], f"assessment_{a_id}": r["a"], f"assessment_{b_id}": r["b"],
                              "change_b_vs_a": r["change"]} for r in data["rows"]],
                            f"orbitwatch_assessments_{a_id}_vs_{b_id}.csv",
                            ["field", f"assessment_{a_id}", f"assessment_{b_id}", "change_b_vs_a"])
    if fmt_ == "pdf":
        require_analyst()
        return Response(_comparison_pdf(data, a_id, b_id), mimetype="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="orbitwatch_assessments_{a_id}_vs_{b_id}.pdf"'})
    return ok(data)


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
    return ok({**clean(_live_space_weather()), "stored": False})


def _live_space_weather():
    """No recent stored reading (the scheduler is not running): NOAA directly, through the shared cache."""
    return spaceweather.cached_fetch()


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
