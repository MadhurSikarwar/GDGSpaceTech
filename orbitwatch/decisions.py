"""Human approval of recommended avoidance manoeuvres (restored from OrbitalGuard) and their simulation.

An analyst approves or rejects the burn an assessment recommends:

* approve: needs an explicit confirmation, a recommended burn, and a burn time
  still in the future. OrbitWatch has no command uplink, so execution is a
  SIMULATION and is recorded and labelled as such (execution = 'SIMULATED');
* reject: needs a reason; optionally the agent re-plans immediately with the
  reviewer's feedback and a required minimum miss distance (OrbitalGuard's
  adaptive loop);
* both are written to maneuver_decision (one decision per assessment) and the
  event log, and e-mailed to analysts who asked for decision e-mails.

The simulation propagates the primary with SGP4 (nominal) and adds the
burn's effect with the same linearised Clohessy-Wiltshire relative motion the
optimiser used (valid for the hours between burn and TCA), so the miss distance
after the burn shown to the reviewer is the one the plan was built on.
"""
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from orbitwatch import db, events, orbital
from orbitwatch.agent.tools import AgentTools, DemoAgentTools
from orbitwatch.physics.collision import ric_basis
from orbitwatch.physics.maneuver import MU, phi_rv

SIM_MODEL = "SGP4 nominal orbit + linearised Clohessy-Wiltshire offset from the impulsive burn (SIMULATED)"
STEP_S = 20.0


class DecisionError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _json(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def load(account, assessment_id):
    a = db.query_one(account, "SELECT * FROM agent_assessment WHERE assessment_id = %s", (assessment_id,))
    if a is None:
        raise DecisionError("No such assessment", 404)
    a["maneuver"] = _json(a["maneuver"])
    if a["demo_event_id"]:
        subj = db.query_one(account, """
            SELECT de.target_norad AS primary_norad, so.name AS primary_name, dob.designation AS secondary_norad,
                   dob.name AS secondary_name, de.time_of_closest_approach, de.miss_distance_km
              FROM demo_event de JOIN space_object so ON so.norad_id = de.target_norad
              LEFT JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
             WHERE de.demo_event_id = %s""", (a["demo_event_id"],))
    else:
        subj = db.query_one(account, """SELECT primary_norad, primary_name, secondary_norad, secondary_name,
                                               time_of_closest_approach, miss_distance_km
                                          FROM v_conjunction_detail WHERE event_id = %s""", (a["event_id"],))
    a["subject"] = subj or {}
    a["synthetic"] = bool(a["demo_event_id"])
    # Analysts and administrators see who decided (through a view); the public sees the decision only.
    source = "v_maneuver_decision" if account in ("analyst", "admin") else "maneuver_decision"
    a["decision_record"] = db.query_one(account, f"SELECT * FROM {source} WHERE assessment_id = %s", (assessment_id,))
    if a["decision_record"]:
        a["decision_record"]["candidate"] = _json(a["decision_record"]["candidate"])
    return a

def _burn_time(cand):
    return orbital.parse_epoch(cand["burn_time_utc"]) if cand and cand.get("burn_time_utc") else None


def simulate(account, a):
    """Before/after trajectories of the primary around the burn and the TCA (Earth-fixed km, for the globe)."""
    cand = a["maneuver"]
    if not cand or not cand.get("dv_ric_mps"):
        raise DecisionError("This assessment has no recommended burn to simulate", 409)
    subject = a["demo_event_id"] or a["event_id"]
    tools = (DemoAgentTools if a["demo_event_id"] else AgentTools)(subject, account)
    _, objs = tools._event()
    st = tools._states()
    sa, sb = orbital.satrec_from_elements(objs["primary"]), orbital.satrec_from_elements(
        {**objs["secondary"], "norad_id": objs["secondary"]["norad_id"]})
    burn = _burn_time(cand)
    tca = st["tca"]
    start, end = burn - timedelta(minutes=10), tca + timedelta(minutes=15)
    offs = np.arange(0.0, (end - start).total_seconds() + STEP_S, STEP_S)
    jd, fr = orbital.time_grid(start, offs)
    ep, rp, vp = sa.sgp4_array(jd, fr)
    es, rs, _ = sb.sgp4_array(jd, fr)
    if ep.any() or es.any():
        raise DecisionError("SGP4 could not propagate the encounter", 422)
    dv = np.asarray(cand["dv_ric_mps"], float) / 1000.0
    # mean motion of the reference orbit, from the state at TCA (as the optimiser computed it)
    pos = st["p_state"]
    a_km = 1.0 / (2.0 / np.linalg.norm(pos[0]) - np.dot(pos[1], pos[1]) / MU)
    n = float(np.sqrt(MU / a_km ** 3))
    t_burn = (burn - start).total_seconds()
    after = rp.copy()
    for i, t in enumerate(offs):
        if t >= t_burn:
            after[i] = rp[i] + ric_basis(rp[i], vp[i]) @ (phi_rv(n, t - t_burn) @ dv)
    # The true closest approach after the burn: at ~9 km/s the objects move ~180 km between
    # 20 s display samples, so search a 0.02 s grid around the TCA instead.
    fine = np.arange(-60.0, 60.0 + 0.02, 0.02)
    jdf, frf = orbital.time_grid(tca, fine)
    _, rpf, vpf = sa.sgp4_array(jdf, frf)
    _, rsf, _ = sb.sgp4_array(jdf, frf)
    t_from_burn = (tca - burn).total_seconds() + fine
    post = np.array([rpf[i] + ric_basis(rpf[i], vpf[i]) @ (phi_rv(n, t_from_burn[i]) @ dv) for i in range(len(fine))])
    d_after = np.linalg.norm(post - rsf, axis=1)
    # exact distances at the (pre-burn) TCA, from the same linear model the optimiser used
    jd1, fr1 = orbital.julian(tca)
    _, p_tca, pv_tca = sa.sgp4(jd1, fr1)
    _, s_tca, _ = sb.sgp4(jd1, fr1)
    shift = ric_basis(p_tca, pv_tca) @ (phi_rv(n, (tca - burn).total_seconds()) @ dv)
    miss_before = float(np.linalg.norm(np.subtract(p_tca, s_tca)))
    miss_after = float(np.linalg.norm(np.add(np.subtract(p_tca, s_tca), shift)))
    i_after = int(np.argmin(d_after))
    ecef = lambda arr: np.round(orbital.teme_to_ecef(arr, jd, fr), 2).tolist()
    return {
        "model": SIM_MODEL, "simulated": True, "synthetic": a["synthetic"],
        "start": start.isoformat() + "Z", "step_s": STEP_S, "offsets_s": offs.tolist(),
        "burn": {"time": burn.isoformat() + "Z", "index": int(np.searchsorted(offs, t_burn)),
                 "dv_ric_mps": cand["dv_ric_mps"], "dv_mps": cand.get("dv_mps"), "direction": cand.get("direction")},
        "tca": {"time": tca.isoformat() + "Z", "index": int(np.argmin(np.abs(offs - (tca - start).total_seconds())))},
        "miss_before_km": round(miss_before, 4), "miss_after_km": round(miss_after, 4),
        "closest_after": {"time": (tca + timedelta(seconds=float(fine[i_after]))).isoformat() + "Z",
                          "distance_km": round(float(d_after[i_after]), 4)},
        "planned_miss_after_km": cand.get("miss_after_km"),
        "primary_before_ecef_km": ecef(rp), "primary_after_ecef_km": ecef(after), "secondary_ecef_km": ecef(rs),
        "primary_name": a["subject"].get("primary_name"), "secondary_name": a["subject"].get("secondary_name"),
    }


def decide(account, user, assessment_id, action, reason=None, confirm=False, replan=False, min_miss_km=None):
    from orbitwatch.agent import orchestrator
    a = load(account, assessment_id)
    if a["status"] != "complete":
        raise DecisionError("The assessment has not finished")
    if a["decision_record"]:
        raise DecisionError(f"Already {a['decision_record']['status'].lower()} on "
                            f"{a['decision_record']['decided_at']:%Y-%m-%d %H:%M} UTC")
    if a["origin"] != "orbitwatch":
        raise DecisionError("Archived OrbitalGuard assessments are read-only")
    reason = (reason or "").strip()[:1000] or None
    subj = a["subject"]
    out = {"assessment_id": assessment_id}
    if action == "approve":
        cand = a["maneuver"]
        if a["decision"] != "MANEUVER_RECOMMENDED" or not cand:
            raise DecisionError("There is no recommended manoeuvre to approve")
        if confirm is not True:
            raise DecisionError("Approval needs explicit confirmation", 400)
        burn = _burn_time(cand)
        if burn is None or burn <= datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=2):
            raise DecisionError("The burn time has passed or is too close; run a new assessment")
        sim = simulate(account, a)
        _, decision_id = db.execute(account, """
            INSERT INTO maneuver_decision (assessment_id, status, decided_by, reason, candidate, delta_v_mps, burn_time,
                                           burn_direction, miss_before_km, miss_after_km, pc_before, pc_after, execution)
            VALUES (%s, 'APPROVED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SIMULATED')""",
            (assessment_id, user["user_id"], reason, json.dumps(cand), cand.get("dv_mps"), burn, cand.get("direction"),
             sim["miss_before_km"], sim["miss_after_km"], a["pc_before"], cand.get("pc_after")))
        events.record("maneuver", "maneuver_approved",
                      f"Avoidance burn approved for {subj.get('primary_name')}: {cand.get('dv_mps', 0):.3f} m/s "
                      f"{cand.get('direction', '').lower()} (SIMULATED" + (", synthetic demo" if a["synthetic"] else "")
                      + f"); miss {sim['miss_before_km']:.3f} -> {sim['miss_after_km']:.3f} km",
                      severity="notice", actor=user["user_id"], entity_type="assessment", entity_id=assessment_id,
                      account=account, detail={"decision_id": decision_id, "by": user["name"], "burn_time": burn,
                                               "synthetic": a["synthetic"]})
        out.update(decision_id=decision_id, status="APPROVED", simulation={k: sim[k] for k in (
            "model", "miss_before_km", "miss_after_km", "closest_after", "burn", "tca")})
    elif action == "reject":
        if not reason or len(reason) < 3:
            raise DecisionError("Say why you reject the recommendation", 400)
        if min_miss_km is not None:
            try:
                min_miss_km = float(min_miss_km)
            except (TypeError, ValueError):
                raise DecisionError("min_miss_km must be a number", 400) from None
            if not 0.05 <= min_miss_km <= 50:
                raise DecisionError("min_miss_km must be between 0.05 and 50 km", 400)
        cand = a["maneuver"] or {}
        _, decision_id = db.execute(account, """
            INSERT INTO maneuver_decision (assessment_id, status, decided_by, reason, candidate, delta_v_mps, burn_time,
                                           burn_direction, pc_before, pc_after, execution)
            VALUES (%s, 'REJECTED', %s, %s, %s, %s, %s, %s, %s, %s, 'NONE')""",
            (assessment_id, user["user_id"], reason, json.dumps(cand) if cand else None, cand.get("dv_mps"),
             _burn_time(cand), cand.get("direction"), a["pc_before"], cand.get("pc_after")))
        events.record("maneuver", "maneuver_rejected",
                      f"Recommended burn for {subj.get('primary_name')} rejected by the reviewer"
                      + (" (synthetic demo)" if a["synthetic"] else "") + ("; re-planning" if replan else ""),
                      severity="notice", actor=user["user_id"], entity_type="assessment", entity_id=assessment_id,
                      account=account, detail={"decision_id": decision_id, "by": user["name"], "reason": reason,
                                               "replan": bool(replan),
                                               "min_miss_km": min_miss_km})
        out.update(decision_id=decision_id, status="REJECTED")
        if replan:
            out["replan_assessment_id"] = orchestrator.start(
                a["event_id"], user["user_id"], account, demo_event_id=a["demo_event_id"],
                parent_assessment_id=assessment_id, feedback=reason, min_miss_km=min_miss_km)
    else:
        raise DecisionError("action must be 'approve' or 'reject'", 400)
    return out
