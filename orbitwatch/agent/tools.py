"""Deterministic tools the decision agent calls.

The LLM never computes physics: every number it reasons about comes from
these tools, which read OrbitWatch's MySQL data and run SGP4, the Foster Pc
and the Clohessy-Wiltshire optimizer. Each tool returns a JSON-serialisable
dict with a one-line 'summary'.
"""
from datetime import timedelta

import numpy as np

from orbitwatch import db, orbital
from orbitwatch.jobs import screening
from orbitwatch.physics import collision, ground, maneuver, spaceweather

MIN_LEAD_MIN = 15            # a burn needs at least this long to plan and uplink
RISK_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

TOOL_SCHEMAS = [
    {"name": "get_conjunction", "description": "Close-approach facts: objects, owners, orbits, time of closest approach, miss distance, relative velocity, element-set ages.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_space_weather", "description": "Current geomagnetic and solar activity (NOAA Kp, Ap, F10.7) and the drag scalar that inflates orbit uncertainty.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "compute_collision_probability", "description": "Foster 2D probability of collision at TCA using an element-set-age covariance model.",
     "parameters": {"type": "object", "properties": {"hard_body_radius_m": {"type": "number", "description": "Combined hard-body radius in metres (default 20)."}}}},
    {"name": "assess_risk", "description": "Risk tier combining the miss-distance risk level and the Pc tier, plus time remaining to TCA.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "ground_station_passes", "description": "Ground-station contact windows of the watched satellite (needed to uplink a manoeuvre).",
     "parameters": {"type": "object", "properties": {"hours": {"type": "number", "description": "Look-ahead in hours (default 12)."}}}},
    {"name": "generate_maneuver_candidates", "description": "Minimum delta-v avoidance burns at several lead times before TCA (Clohessy-Wiltshire + SLSQP).",
     "parameters": {"type": "object", "properties": {"count": {"type": "integer", "description": "How many burn times to try (1-4, default 3)."}}}},
    {"name": "evaluate_maneuver_constraints", "description": "Check one candidate against hard constraints: delta-v budget, slot-keeping drift, Pc after the burn, lead time, uplink opportunity.",
     "parameters": {"type": "object", "properties": {"candidate_id": {"type": "string"}}, "required": ["candidate_id"]}},
]


class AgentTools:
    synthetic = False

    def __init__(self, event_id, account, min_miss_km=None):
        self.event_id = event_id
        self.account = account
        self.min_miss_km = float(min_miss_km) if min_miss_km else None
        self.cache = {}
        self.candidates = {}
        self.evaluations = {}

    # -- helpers -----------------------------------------------------------
    def _event(self):
        if "event" not in self.cache:
            ev = db.query_one(self.account, "SELECT * FROM v_conjunction_detail WHERE event_id = %s", (self.event_id,))
            if ev is None:
                raise ValueError(f"no close approach #{self.event_id}")
            objs = {}
            for role in ("primary", "secondary"):
                n = ev[f"{role}_norad"]
                o = db.query_one(self.account, f"""
                    SELECT c.norad_id, c.name, c.object_type, c.status, c.org_name, c.country_name, c.region_name,
                           co.epoch, {', '.join('co.' + f for f in orbital.ELEMENT_FIELDS)}, co.perigee_km, co.apogee_km,
                           co.period_min
                      FROM v_object_catalog c LEFT JOIN current_orbit co ON co.norad_id = c.norad_id
                     WHERE c.norad_id = %s""", (n,))
                objs[role] = o
            self.cache["event"], self.cache["objects"] = ev, objs
        return self.cache["event"], self.cache["objects"]

    def _states(self):
        """Both objects' TEME states at closest approach, re-refined with the current element sets."""
        if "states" not in self.cache:
            ev, objs = self._event()
            if not objs["primary"]["epoch"] or not objs["secondary"]["epoch"]:
                raise ValueError("an object has no current element set")
            sa = orbital.satrec_from_elements(objs["primary"])
            sb = orbital.satrec_from_elements(objs["secondary"])
            ref = screening._refine(sa, sb, ev["time_of_closest_approach"], 120.0)
            if ref is None:
                raise ValueError("SGP4 could not propagate one of the objects to TCA")
            self.cache["states"] = ref
        return self.cache["states"]

    def _drag(self):
        return self.get_space_weather()["drag_scalar"]

    def _encounter(self, hbr_m=collision.DEFAULT_HARD_BODY_RADIUS_M):
        ev, objs = self._event()
        st = self._states()
        tca = st["tca"]
        age = lambda o: abs((tca - o["epoch"]).total_seconds()) / 3600.0
        (pp, pv), (sp, sv) = st["p_state"], st["s_state"]
        return maneuver.Encounter(pp, pv, sp, sv, age(objs["primary"]), age(objs["secondary"]),
                                  objs["primary"]["object_type"], objs["secondary"]["object_type"], self._drag(), hbr_m)

    # -- tools ------------------------------------------------------------
    def get_conjunction(self):
        ev, objs = self._event()
        st = self._states()
        now = orbital.now_utc()

        def obj(o):
            return {"norad_id": o["norad_id"], "name": o["name"], "type": o["object_type"], "status": o["status"],
                    "owner": o["org_name"], "country": o["country_name"], "region": o["region_name"],
                    "perigee_km": round(float(o["perigee_km"]), 1) if o["perigee_km"] is not None else None,
                    "apogee_km": round(float(o["apogee_km"]), 1) if o["apogee_km"] is not None else None,
                    "element_set_age_h": round(abs((st["tca"] - o["epoch"]).total_seconds()) / 3600.0, 1) if o["epoch"] else None}
        out = {"event_id": ev["event_id"], "synthetic": self.synthetic, "tca_utc": st["tca"].isoformat() + "Z",
               "hours_to_tca": round((st["tca"] - now).total_seconds() / 3600.0, 2),
               "miss_distance_km": round(st["miss_km"], 3), "relative_velocity_km_s": round(st["rel_vel_kms"], 3),
               "screening_risk_level": ev["risk_level"], "primary": obj(objs["primary"]), "secondary": obj(objs["secondary"])}
        out["summary"] = (f"{out['primary']['name']} vs {out['secondary']['name']} ({out['secondary']['type']}): miss "
                          f"{out['miss_distance_km']} km at {out['relative_velocity_km_s']} km/s, TCA in "
                          f"{out['hours_to_tca']} h; screening level {ev['risk_level']}")
        return out

    def get_space_weather(self):
        if "sw" not in self.cache:
            from orbitwatch.jobs.spaceweather import latest
            row = latest(self.account, max_age_hours=6)
            if row:
                sw = {"kp": float(row["kp"]), "ap": float(row["ap"]), "f107": float(row["f107"]),
                      "activity": row["activity"], "drag_scalar": float(row["drag_scalar"]), "source": row["source"],
                      "observed_at": row["observed_at"].isoformat() + "Z", "live": bool(row["live"])}
            else:
                live = spaceweather.fetch()
                sw = {**live, "observed_at": live["observed_at"].isoformat() + "Z"}
            sw["summary"] = (f"Kp {sw['kp']:.1f} ({sw['activity'].lower()}), F10.7 {sw['f107']:.0f} sfu; "
                             f"in-track uncertainty growth x{sw['drag_scalar']:.2f}")
            self.cache["sw"] = sw
        return self.cache["sw"]

    def compute_collision_probability(self, hard_body_radius_m=collision.DEFAULT_HARD_BODY_RADIUS_M):
        hbr = float(hard_body_radius_m or collision.DEFAULT_HARD_BODY_RADIUS_M)
        hbr = min(max(hbr, 1.0), 100.0)
        ev, objs = self._event()
        st = self._states()
        age = lambda o: abs((st["tca"] - o["epoch"]).total_seconds()) / 3600.0
        res = collision.encounter_pc(st["p_state"], st["s_state"], age(objs["primary"]), age(objs["secondary"]),
                                     objs["primary"]["object_type"], objs["secondary"]["object_type"], self._drag(), hbr)
        self.cache["pc"] = res
        res["summary"] = (f"Pc {res['pc']:.2e} ({res['tier']} tier) with {hbr:g} m hard-body radius; encounter-plane "
                          f"sigma {res['encounter_plane_sigma_km'][0]:.2f} x {res['encounter_plane_sigma_km'][1]:.2f} km")
        return res

    def assess_risk(self):
        ev, _ = self._event()
        pc = self.cache.get("pc") or self.compute_collision_probability()
        st = self._states()
        tier = max(ev["risk_level"], pc["tier"], key=RISK_ORDER.index)
        hours = (st["tca"] - orbital.now_utc()).total_seconds() / 3600.0
        guidance = {"LOW": "monitor only; no manoeuvre analysis needed",
                    "MEDIUM": "deeper analysis; usually monitor and re-screen with fresher element sets",
                    "HIGH": "generate and evaluate avoidance manoeuvres",
                    "CRITICAL": "generate several manoeuvres, evaluate all, recommend the safest feasible one"}[tier]
        out = {"risk_tier": tier, "screening_risk_level": ev["risk_level"], "pc": pc["pc"], "pc_tier": pc["tier"],
               "hours_to_tca": round(hours, 2), "tca_passed": hours <= 0, "guidance": guidance}
        out["summary"] = f"risk tier {tier} (miss-distance level {ev['risk_level']}, Pc tier {pc['tier']}); {guidance}"
        self.cache["risk"] = out
        return out

    def ground_station_passes(self, hours=12):
        hours = min(max(float(hours or 12), 1.0), 48.0)
        _, objs = self._event()
        stations = db.query(self.account, "SELECT * FROM ground_station")
        p = ground.passes(objs["primary"], stations, orbital.now_utc(), hours=hours)
        self.cache["passes"], self.cache["passes_hours"] = p, hours
        out = {"hours": hours, "passes": [{**x, "aos": x["aos"].isoformat() + "Z", "los": x["los"].isoformat() + "Z"} for x in p[:20]]}
        first = p[0] if p else None
        out["summary"] = (f"{len(p)} contact windows in {hours:g} h" + (f"; next {first['station']} at "
                          f"{first['aos']:%H:%M} UTC (max {first['max_elevation_deg']} deg)" if first else ""))
        return out

    def generate_maneuver_candidates(self, count=3):
        count = int(min(max(int(count or 3), 1), 4))
        _, objs = self._event()
        st = self._states()
        enc = self._encounter()
        period_s = float(objs["primary"]["period_min"]) * 60.0
        now = orbital.now_utc()
        out = []
        k = 0
        for half_orbits in (1, 2, 3, 4, 5, 6):
            if len(out) >= count:
                break
            dt = half_orbits * period_s / 2.0
            burn = st["tca"] - timedelta(seconds=dt)
            if burn < now + timedelta(minutes=MIN_LEAD_MIN):
                continue
            k += 1
            c = maneuver.optimise(enc, dt, min_miss_km=self.min_miss_km)
            c.update(candidate_id=f"M{k}", burn_time_utc=burn.isoformat() + "Z", lead_time_min=round(dt / 60.0, 1),
                     half_orbits_before_tca=half_orbits)
            self.candidates[c["candidate_id"]] = {**c, "_burn": burn}
            out.append(c)
        res = {"pc_before": enc.pc_after(np.zeros(3), 1.0), "pc_target": maneuver.PC_TARGET, "candidates": out}
        res["summary"] = (f"{len(out)} candidates: " + "; ".join(
            f"{c['candidate_id']} {c['dv_mps']:.3f} m/s {c['direction']} at T-{c['lead_time_min']:.0f} min -> Pc {c['pc_after']:.1e}"
            for c in out)) if out else "no candidate: TCA is too close for a planned burn"
        return res

    def evaluate_maneuver_constraints(self, candidate_id):
        c = self.candidates.get(str(candidate_id))
        if c is None:
            return {"candidate_id": candidate_id, "feasible": False, "violations": ["unknown candidate"],
                    "summary": f"{candidate_id}: unknown candidate (generate candidates first)"}
        now = orbital.now_utc()
        needed_h = (c["_burn"] - now).total_seconds() / 3600.0 + 0.5
        if self.cache.get("passes") is None or self.cache.get("passes_hours", 0) < needed_h:
            self.ground_station_passes(hours=min(max(needed_h, 1.0), 48.0))
        passes = self.cache["passes"]
        uplink = [p for p in passes if now + timedelta(minutes=5) <= p["aos"] <= c["_burn"] - timedelta(minutes=5)]
        checks = {
            "solver_converged": c["converged"],
            "delta_v_within_budget": c["dv_mps"] <= maneuver.MAX_DELTA_V_MPS,
            "slot_drift_within_bound": c["sma_drift_km"] <= maneuver.MAX_SMA_DRIFT_KM,
            "pc_below_target": c["pc_after"] <= maneuver.PC_TARGET,
            "lead_time_ok": c["_burn"] >= now + timedelta(minutes=MIN_LEAD_MIN),
            "uplink_window_before_burn": bool(uplink),
        }
        if self.min_miss_km:
            checks["meets_reviewer_min_miss"] = c["miss_after_km"] >= self.min_miss_km
        violations = [k for k, ok in checks.items() if not ok]
        out = {"candidate_id": candidate_id, "feasible": not violations, "checks": checks, "violations": violations,
               "dv_mps": c["dv_mps"], "pc_after": c["pc_after"], "sma_drift_km": c["sma_drift_km"],
               "burn_time_utc": c["burn_time_utc"],
               "uplink_station": uplink[0]["station"] if uplink else None}
        self.evaluations[candidate_id] = out
        out["summary"] = (f"{candidate_id}: {'FEASIBLE' if out['feasible'] else 'REJECTED'} — {c['dv_mps']:.3f} m/s, Pc after "
                          f"{c['pc_after']:.1e}, drift {c['sma_drift_km']:.2f} km"
                          + (f"; violates {', '.join(violations)}" if violations else f"; uplink via {out['uplink_station']}"))
        return out

    def call(self, name, args):
        fn = {s["name"]: getattr(self, s["name"]) for s in TOOL_SCHEMAS}.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}", "summary": f"unknown tool {name}"}
        try:
            return fn(**(args or {}))
        except TypeError as exc:
            return {"error": str(exc), "summary": f"bad arguments for {name}: {exc}"}
        except ValueError as exc:
            return {"error": str(exc), "summary": f"{name} failed: {exc}"}


class DemoAgentTools(AgentTools):
    """The same tools for a SYNTHETIC demo close approach: a real catalogue object (primary) against a
    synthetic debris object from the demo tables (secondary). Everything downstream is unchanged."""
    synthetic = True

    def _event(self):
        if "event" not in self.cache:
            ev = db.query_one(self.account, """
                SELECT de.demo_event_id AS event_id, de.time_of_closest_approach, de.miss_distance_km,
                       de.relative_velocity, de.risk_level, de.target_norad AS primary_norad,
                       dob.designation AS secondary_norad, de.demo_object_id
                  FROM demo_event de JOIN demo_object dob ON dob.demo_object_id = de.demo_object_id
                 WHERE de.demo_event_id = %s""", (self.event_id,))
            if ev is None:
                raise ValueError(f"no synthetic close approach #{self.event_id}")
            primary = db.query_one(self.account, f"""
                SELECT c.norad_id, c.name, c.object_type, c.status, c.org_name, c.country_name, c.region_name,
                       co.epoch, {', '.join('co.' + f for f in orbital.ELEMENT_FIELDS)}, co.perigee_km, co.apogee_km,
                       co.period_min
                  FROM v_object_catalog c LEFT JOIN current_orbit co ON co.norad_id = c.norad_id
                 WHERE c.norad_id = %s""", (ev["primary_norad"],))
            syn = db.query_one(self.account, f"""SELECT designation, name, epoch, {', '.join(orbital.ELEMENT_FIELDS)}
                                                  FROM demo_object WHERE demo_object_id = %s""", (ev["demo_object_id"],))
            alt = orbital.derived_altitudes(syn["mean_motion"], syn["eccentricity"])
            secondary = {**syn, "norad_id": syn["designation"], "object_type": "Debris", "status": "SYNTHETIC",
                         "org_name": None, "country_name": None, "region_name": None,
                         "perigee_km": alt["perigee_km"], "apogee_km": alt["apogee_km"], "period_min": alt["period_min"]}
            self.cache["event"], self.cache["objects"] = ev, {"primary": primary, "secondary": secondary}
        return self.cache["event"], self.cache["objects"]
