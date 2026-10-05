"""Synthetic-debris demo (restored from OrbitalGuard): a guaranteed close approach on demand.

An analyst picks a real catalogue object; OrbitWatch creates one piece of
SYNTHETIC debris whose orbit crosses it at a chosen time and miss distance.
The synthetic object is a genuine SGP4 element set: two-body elements are
fitted iteratively until SGP4 itself puts the object exactly at the planned
encounter state. Screening refinement, the probability of collision, the
decision agent, approval and the burn simulation therefore all run on it
unchanged.

Separation from real data is structural, not a flag: synthetic objects live
only in demo_scenario / demo_object / demo_event, carry 'SYN-' designations
(never NORAD numbers), and no statistic, report, alert or export reads those
tables. Clearing a scenario deletes its synthetic objects and events.
"""
import math
from datetime import timedelta

import numpy as np

from orbitwatch import db, events, orbital
from orbitwatch.jobs import screening, spaceweather
from orbitwatch.physics import collision

MU = orbital.MU_EARTH
DEFAULT_LEAD_MIN = 360        # 6 h: room for burns at 1-6 half-orbits and an uplink pass before them
DEFAULT_MISS_KM = 0.25
DEFAULT_CROSSING_DEG = 70.0   # angle between the two velocity vectors at the encounter
MAX_ACTIVE_SCENARIOS = 5


class DemoError(ValueError):
    pass


def rv_to_elements(r, v):
    """Classical elements (OMM units) of a TEME state; used as SGP4 mean elements before refinement."""
    r, v = np.asarray(r, float), np.asarray(v, float)
    rn, vn = np.linalg.norm(r), np.linalg.norm(v)
    h = np.cross(r, v)
    hn = np.linalg.norm(h)
    node = np.cross([0.0, 0.0, 1.0], h)
    nn = np.linalg.norm(node)
    e_vec = ((vn ** 2 - MU / rn) * r - np.dot(r, v) * v) / MU
    e = float(np.linalg.norm(e_vec))
    a = 1.0 / (2.0 / rn - vn ** 2 / MU)
    inc = math.acos(max(-1.0, min(1.0, h[2] / hn)))
    raan = math.acos(max(-1.0, min(1.0, node[0] / nn))) if nn > 1e-12 else 0.0
    if node[1] < 0:
        raan = 2 * math.pi - raan
    argp = math.acos(max(-1.0, min(1.0, np.dot(node, e_vec) / (nn * e)))) if nn > 1e-12 and e > 1e-9 else 0.0
    if e_vec[2] < 0:
        argp = 2 * math.pi - argp
    nu = math.acos(max(-1.0, min(1.0, np.dot(e_vec, r) / (e * rn)))) if e > 1e-9 else 0.0
    if np.dot(r, v) < 0:
        nu = 2 * math.pi - nu
    big_e = 2.0 * math.atan(math.sqrt((1 - e) / (1 + e)) * math.tan(nu / 2.0))
    mean_anom = (big_e - e * math.sin(big_e)) % (2 * math.pi)
    n_rad_s = math.sqrt(MU / a ** 3)
    deg = 180.0 / math.pi
    return {"mean_motion": n_rad_s * 86400.0 / (2 * math.pi), "eccentricity": e, "inclination": inc * deg,
            "raan": raan * deg, "arg_perigee": argp * deg, "mean_anomaly": mean_anom * deg,
            "bstar": 0.0, "mean_motion_dot": 0.0, "mean_motion_ddot": 0.0}


def fit_sgp4_elements(r_target, v_target, epoch, iterations=30):
    """Mean elements whose SGP4 state at `epoch` equals (r_target, v_target) to < 1 m and < 1 mm/s."""
    r_in, v_in = np.array(r_target, float), np.array(v_target, float)
    jd, fr = orbital.julian(epoch)
    for _ in range(iterations):
        el = {**rv_to_elements(r_in, v_in), "epoch": epoch, "norad_id": 0}
        err, r, v = orbital.satrec_from_elements(el).sgp4(jd, fr)
        if err:
            raise DemoError(f"SGP4 rejected the synthetic orbit (error {err})")
        dr, dv = np.subtract(r_target, r), np.subtract(v_target, v)
        if np.linalg.norm(dr) < 1e-3 and np.linalg.norm(dv) < 1e-6:
            return el
        r_in, v_in = r_in + dr, v_in + dv
    raise DemoError("could not fit SGP4 elements to the planned encounter")


def _rotate(vec, axis, angle):
    axis = axis / np.linalg.norm(axis)
    return (vec * math.cos(angle) + np.cross(axis, vec) * math.sin(angle)
            + axis * np.dot(axis, vec) * (1 - math.cos(angle)))


def create(target_norad, user_id, account, lead_min=DEFAULT_LEAD_MIN, miss_km=DEFAULT_MISS_KM,
           crossing_deg=DEFAULT_CROSSING_DEG):
    lead_min = float(min(max(lead_min, 60), 24 * 60))
    miss_km = float(min(max(miss_km, 0.02), 5.0))
    crossing = math.radians(float(min(max(crossing_deg, 10), 170)))
    active = db.query_one(account, "SELECT COUNT(*) AS n FROM demo_scenario WHERE status = 'active'")["n"]
    if active >= MAX_ACTIVE_SCENARIOS:
        raise DemoError(f"{active} demo scenarios are active; clear one first")
    target = db.query_one(account, f"""
        SELECT so.norad_id, so.name, so.object_type, co.epoch, {', '.join('co.' + f for f in orbital.ELEMENT_FIELDS)}
          FROM space_object so JOIN current_orbit co ON co.norad_id = so.norad_id
         WHERE so.norad_id = %s AND so.in_earth_orbit""", (target_norad,))
    if target is None:
        raise DemoError("pick an object in Earth orbit that has a current orbit")
    tca = (orbital.now_utc() + timedelta(minutes=lead_min)).replace(microsecond=0)
    sat_t = orbital.satrec_from_elements(target)
    err, r_t, v_t = sat_t.sgp4(*orbital.julian(tca))
    if err:
        raise DemoError("the target's orbit cannot be propagated to the encounter time")
    r_t, v_t = np.array(r_t), np.array(v_t)
    if np.linalg.norm(r_t) - orbital.EARTH_RADIUS_KM > 2000:
        raise DemoError("the demo supports LEO targets (below 2,000 km)")
    radial = r_t / np.linalg.norm(r_t)
    # Debris on a crossing orbit: the target's velocity turned about the local vertical, 0.2 % faster
    # (a slightly eccentric orbit), passing `miss_km` radially above it at TCA.
    v_d = _rotate(v_t, radial, crossing) * 1.002
    r_d = r_t + miss_km * radial
    el = fit_sgp4_elements(r_d, v_d, tca)
    sat_d = orbital.satrec_from_elements({**el, "epoch": tca, "norad_id": 0})
    ref = screening._refine(sat_t, sat_d, tca, 300.0)
    if ref is None or abs(ref["miss_km"] - miss_km) > 0.05:
        raise DemoError("the synthetic encounter did not verify against SGP4")
    age_h = abs((ref["tca"] - target["epoch"]).total_seconds()) / 3600.0
    pc = collision.encounter_pc(ref["p_state"], ref["s_state"], age_h, age_h, target["object_type"], "Debris",
                                spaceweather.drag_scalar(account))

    with db.mysql_conn(account) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO demo_scenario (target_norad, created_by, notes) VALUES (%s, %s, %s)",
                    (target_norad, user_id, f"Synthetic debris crossing {target['name']} at "
                                            f"{math.degrees(crossing):.0f} deg, planned miss {miss_km:g} km"))
        scenario = cur.lastrowid
        designation = f"SYN-{scenario:06d}-1"
        cur.execute(f"""INSERT INTO demo_object (scenario_id, designation, name, epoch, {', '.join(orbital.ELEMENT_FIELDS)})
                        VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(orbital.ELEMENT_FIELDS))})""",
                    (scenario, designation, f"SYNTHETIC DEBRIS {designation}", tca,
                     *(el[f] for f in orbital.ELEMENT_FIELDS)))
        obj = cur.lastrowid
        cur.execute("""INSERT INTO demo_event (scenario_id, demo_object_id, target_norad, time_of_closest_approach,
                           miss_distance_km, relative_velocity, risk_level, probability_of_collision, pc_method)
                       VALUES (%s, %s, %s, %s, %s, %s, fn_risk_level(%s, %s), %s, %s)""",
                    (scenario, obj, target_norad, ref["tca"], round(ref["miss_km"], 3), round(ref["rel_vel_kms"], 3),
                     ref["miss_km"], ref["rel_vel_kms"], pc["pc"], pc["method"][:40]))
        event = cur.lastrowid
        conn.commit()
    events.record("demo", "demo_injected",
                  f"SYNTHETIC debris {designation} injected to cross {target['name']} (demo only, not real data)",
                  severity="notice", actor=user_id, entity_type="demo_event", entity_id=event, account=account,
                  detail={"scenario_id": scenario, "miss_km": round(ref["miss_km"], 3), "tca": ref["tca"],
                          "pc": pc["pc"]})
    return get(account, scenario)


def get(account, scenario_id):
    sc = db.query_one(account, """SELECT s.*, so.name AS target_name FROM demo_scenario s
                                   JOIN space_object so ON so.norad_id = s.target_norad WHERE s.scenario_id = %s""",
                      (scenario_id,))
    if sc is None:
        return None
    sc["objects"] = db.query(account, "SELECT demo_object_id, designation, name, epoch FROM demo_object "
                                      "WHERE scenario_id = %s", (scenario_id,))
    sc["events"] = db.query(account, """
        SELECT de.demo_event_id, de.time_of_closest_approach, de.miss_distance_km, de.relative_velocity, de.risk_level,
               de.probability_of_collision, de.pc_method, dob.designation,
               (SELECT MAX(a.assessment_id) FROM agent_assessment a WHERE a.demo_event_id = de.demo_event_id)
                   AS latest_assessment_id
          FROM demo_event de LEFT JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
         WHERE de.scenario_id = %s ORDER BY de.time_of_closest_approach""", (scenario_id,))
    return sc


def list_scenarios(account, include_archived=False):
    rows = db.query(account, f"""
        SELECT s.scenario_id, s.target_norad, so.name AS target_name, s.created_at, s.status, s.origin, s.notes,
               (SELECT COUNT(*) FROM demo_event de WHERE de.scenario_id = s.scenario_id) AS events
          FROM demo_scenario s JOIN space_object so ON so.norad_id = s.target_norad
         WHERE s.status = 'active' {"OR s.status = 'archived'" if include_archived else ""}
         ORDER BY s.status = 'active' DESC, s.created_at DESC LIMIT 100""")
    return rows


def clear(account, user_id, scenario_id=None):
    """Remove synthetic objects and events of one scenario (or of every active one). Returns how many."""
    where, params = ("scenario_id = %s AND status = 'active'", (scenario_id,)) if scenario_id else ("status = 'active'", ())
    with db.mysql_conn(account) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT scenario_id FROM demo_scenario WHERE {where}", params)
        ids = [r[0] for r in cur.fetchall()]
        for sid in ids:
            cur.execute("DELETE FROM demo_object WHERE scenario_id = %s", (sid,))   # cascades to its events
            cur.execute("DELETE FROM demo_event WHERE scenario_id = %s", (sid,))
            cur.execute("UPDATE demo_scenario SET status = 'cleared' WHERE scenario_id = %s", (sid,))
        conn.commit()
    if ids:
        events.record("demo", "demo_cleared", f"Synthetic demo cleared ({len(ids)} scenario{'s' if len(ids) > 1 else ''})",
                      actor=user_id, entity_type="demo_scenario", entity_id=",".join(map(str, ids))[:64], account=account)
    return len(ids)


def positions(account, t, span=None):
    """Earth-fixed positions of the active synthetic objects (the globe's demo layer)."""
    rows = db.query(account, f"""SELECT dob.designation, dob.name, dob.epoch, {', '.join('dob.' + f for f in orbital.ELEMENT_FIELDS)}
                                  FROM demo_object dob JOIN demo_scenario s ON s.scenario_id = dob.scenario_id
                                 WHERE s.status = 'active'""")
    if not rows:
        return {"items": []}
    sats = [orbital.satrec_from_elements({**r, "norad_id": 0}) for r in rows]
    jd, fr = orbital.time_grid(t, [0.0, span] if span else [0.0])
    err, r, _ = orbital.propagate(sats, jd, fr)
    out = []
    for i, row in enumerate(rows):
        if err[i, 0]:
            continue
        item = {"designation": row["designation"], "name": row["name"], "synthetic": True,
                "pos": np.round(orbital.teme_to_ecef(r[i:i + 1, 0, :], jd[0], fr[0])[0], 2).tolist()}
        if span and not err[i, 1]:
            item["pos2"] = np.round(orbital.teme_to_ecef(r[i:i + 1, 1, :], jd[1], fr[1])[0], 2).tolist()
        out.append(item)
    return {"items": out, "span": span}
