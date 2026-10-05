"""Close-approach screening (SRS 3.4).

Every watchlist object (primary) is screened against every object with a
recent element set in Current_Orbit (secondaries) over the configured
horizon. Approaches closer than the threshold are recorded as
Conjunction_Event rows in ONE transaction, through sp_record_conjunction,
whose insert trigger creates the subscriber alerts.

Method
  1. Altitude filter: a pair can only meet if their perigee-apogee bands,
     widened by a margin, overlap.
  2. Coarse pass: all objects are propagated together with SGP4 (SatrecArray)
     on a time grid. At each sample the relative position r and velocity v
     give the linear closest-approach estimate within half a step,
     t* = -(r.v)/(v.v), d = |r + v t*|. Pairs with d below threshold +
     margin are candidates. (Sampled distances alone would miss fast
     encounters: at 15 km/s objects move 900 km per 60 s step.)
  3. Refinement: each candidate is re-propagated exactly on a 1 s grid
     around the estimate, then refined linearly to the millisecond, giving
     the time of closest approach, miss distance and relative velocity.
"""
import logging
import time
from datetime import timedelta

import numpy as np

from orbitwatch import db, orbital
from orbitwatch.physics import collision

log = logging.getLogger(__name__)

ALTITUDE_MARGIN_KM = 50.0     # mean elements vs osculating radius differ by tens of km
LINEAR_MARGIN_KM = 25.0       # error of the linear estimate over half a 60 s step (worst case ~8 km)
ATTACHED_KM, ATTACHED_KMS = 1.0, 0.01  # docked modules/vehicles share one orbit: not an "approach"
BLOCK = 120                   # coarse samples propagated per block (bounds memory)


def load_catalog(max_age_days):
    rows = db.query("jobs", f"""
        SELECT co.norad_id, co.epoch, {', '.join('co.' + f for f in orbital.ELEMENT_FIELDS)},
               co.perigee_km, co.apogee_km, so.name, so.object_type
          FROM current_orbit co
          JOIN space_object so ON so.norad_id = co.norad_id
         WHERE so.decay_date IS NULL
           AND co.epoch >= UTC_TIMESTAMP() - INTERVAL %s DAY""", (max_age_days,))
    watch = {r["norad_id"] for r in db.query("jobs", "SELECT norad_id FROM watchlist")}
    return rows, watch


def screen(catalog, primary_ids, start, horizon_s, step_s, threshold_km):
    """Pure screening function. catalog: dicts with norad_id, epoch, elements, perigee_km, apogee_km.

    Returns (events, stats); each event is a dict with primary, secondary, tca, miss_km, rel_vel_kms.
    """
    t0 = time.time()
    ids = np.array([o["norad_id"] for o in catalog])
    index = {n: i for i, n in enumerate(ids)}
    sats = [orbital.satrec_from_elements(o) for o in catalog]
    perigee = np.array([o["perigee_km"] for o in catalog])
    apogee = np.array([o["apogee_km"] for o in catalog])
    primaries = [index[n] for n in primary_ids if n in index]
    primary_set = set(primaries)

    margin = threshold_km + ALTITUDE_MARGIN_KM
    candidates = {}
    for p in primaries:
        mask = (perigee - margin <= apogee[p]) & (perigee[p] - margin <= apogee)
        mask[p] = False
        cand = np.nonzero(mask)[0]
        # A pair of two primaries is screened once, from the lower index.
        cand = cand[[(c not in primary_set) or (c > p) for c in cand]] if primary_set else cand
        candidates[p] = cand
    pairs_considered = int(sum(len(c) for c in candidates.values()))
    needed = np.unique(np.concatenate([np.array(primaries, dtype=int)] + list(candidates.values())))
    local = {int(g): i for i, g in enumerate(needed)}
    needed_sats = [sats[g] for g in needed]
    local_cand = {p: np.array([local[int(c)] for c in cand], dtype=int) for p, cand in candidates.items()}

    n_steps = int(horizon_s // step_s) + 1
    offsets = np.arange(n_steps) * float(step_s)
    flag_limit = threshold_km + LINEAR_MARGIN_KM
    flagged = {}  # (p, c) -> list of (sample index, linear distance, t*)
    for b0 in range(0, n_steps, BLOCK):
        jd, fr = orbital.time_grid(start, offsets[b0:b0 + BLOCK])
        err, r, v = orbital.propagate(needed_sats, jd, fr)
        r = r.astype(np.float32)
        v = v.astype(np.float32)
        for p in primaries:
            cand = candidates[p]
            if not len(cand):
                continue
            lp, lc = local[p], local_cand[p]
            dr = r[lc] - r[lp]
            dv = v[lc] - v[lp]
            with np.errstate(invalid="ignore", divide="ignore"):
                vv = np.einsum("ijk,ijk->ij", dv, dv)
                ts = np.clip(-np.einsum("ijk,ijk->ij", dr, dv) / vv, -step_s / 2, step_s / 2)
                ts = np.nan_to_num(ts)
                d = np.linalg.norm(dr + dv * ts[..., None], axis=2)
                hit = d < flag_limit
            for ci, k in zip(*np.nonzero(hit)):
                flagged.setdefault((p, int(cand[ci])), []).append((b0 + int(k), float(d[ci, k]), float(ts[ci, k])))
    t_coarse = time.time() - t0

    events, refined = [], 0
    for (p, c), samples in flagged.items():
        samples.sort()
        runs, current = [], [samples[0]]
        for s in samples[1:]:
            if s[0] - current[-1][0] <= 1:
                current.append(s)
            else:
                runs.append(current)
                current = [s]
        runs.append(current)
        for run in runs:
            k, _, tstar = min(run, key=lambda s: s[1])
            refined += 1
            ev = _refine(sats[p], sats[c], start + timedelta(seconds=offsets[k] + tstar), step_s)
            if ev is None or ev["miss_km"] > threshold_km:
                continue
            if ev["miss_km"] < ATTACHED_KM and ev["rel_vel_kms"] < ATTACHED_KMS:
                continue
            ev.update(primary=int(ids[p]), secondary=int(ids[c]))
            events.append(ev)
    stats = {"primaries": len(primaries), "objects": len(catalog), "pairs": pairs_considered,
             "candidates": refined, "events": len(events), "coarse_s": round(t_coarse, 1),
             "total_s": round(time.time() - t0, 1)}
    return events, stats


def _refine(sat_a, sat_b, center, half_window_s):
    """Exact SGP4 around `center`: 1 s grid, then a linear step to the true minimum."""
    offs = np.arange(-half_window_s, half_window_s + 1, 1.0)
    jd, fr = orbital.time_grid(center, offs)
    ea, ra, va = sat_a.sgp4_array(jd, fr)
    eb, rb, vb = sat_b.sgp4_array(jd, fr)
    if ea.any() or eb.any():
        return None
    dist = np.linalg.norm(ra - rb, axis=1)
    i = int(np.argmin(dist))
    dr, dv = ra[i] - rb[i], va[i] - vb[i]
    vv = float(dv @ dv)
    tstar = float(np.clip(-(dr @ dv) / vv, -1.0, 1.0)) if vv > 0 else 0.0
    tca = center + timedelta(seconds=float(offs[i]) + tstar)
    jd1, fr1 = orbital.julian(tca)
    e1, r1, v1 = sat_a.sgp4(jd1, fr1)
    e2, r2, v2 = sat_b.sgp4(jd1, fr1)
    if e1 or e2:
        return None
    return {"tca": tca, "miss_km": float(np.linalg.norm(np.subtract(r1, r2))),
            "rel_vel_kms": float(np.linalg.norm(np.subtract(v1, v2))),
            "p_state": (r1, v1), "s_state": (r2, v2)}


def event_pc(ev, by_id, drag):
    """Foster 2D probability of collision for one recorded approach (None if undefined)."""
    p, s = by_id.get(ev["primary"]), by_id.get(ev["secondary"])
    if not p or not s or ev["rel_vel_kms"] < 0.01:
        return None
    age = lambda o: abs((ev["tca"] - o["epoch"]).total_seconds()) / 3600.0
    try:
        return collision.encounter_pc(ev["p_state"], ev["s_state"], age(p), age(s), p["object_type"],
                                      s["object_type"], drag)["pc"]
    except (ValueError, FloatingPointError):
        return None


def run(ctx):
    cfg = db.get_config()
    threshold = float(cfg.get("screening_threshold_km", 10))
    horizon_h = float(cfg.get("screening_horizon_hours", 24))
    step = float(cfg.get("screening_step_seconds", 60))
    max_age = int(cfg.get("screening_max_epoch_age_days", 30))

    catalog, watch = load_catalog(max_age)
    if not watch:
        from orbitwatch.jobs.runner import SkipJob
        raise SkipJob("watchlist is empty")
    start = orbital.now_utc().replace(microsecond=0)
    events, stats = screen(catalog, sorted(watch), start, horizon_h * 3600, step, threshold)

    # Probability of collision for every approach (an addition carried over from OrbitalGuard):
    # covariance grows with element-set age and with today's space-weather drag activity.
    from orbitwatch.jobs.spaceweather import drag_scalar
    by_id = {o["norad_id"]: o for o in catalog}
    drag = drag_scalar("jobs")
    for ev in events:
        ev["pc"] = event_pc(ev, by_id, drag)

    new = updated = 0
    new_ids = []
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        for ev in sorted(events, key=lambda e: e["tca"]):
            res = cur.callproc("sp_record_conjunction", (
                ev["primary"], ev["secondary"], ev["tca"].replace(microsecond=(ev["tca"].microsecond // 1000) * 1000),
                round(ev["miss_km"], 3), round(ev["rel_vel_kms"], 3), ctx.run_id, 0, 0))
            cur.execute("UPDATE conjunction_event SET probability_of_collision = %s, pc_method = %s WHERE event_id = %s",
                        (ev["pc"], collision.PC_METHOD if ev["pc"] is not None else None, res[6]))
            if res[7]:
                new += 1
                new_ids.append(res[6])
            else:
                updated += 1
        conn.commit()
        alerts = 0
        if new_ids:
            cur.execute(f"SELECT COUNT(*) FROM alert WHERE event_id IN ({','.join(['%s'] * len(new_ids))})", new_ids)
            alerts = cur.fetchone()[0]
        cur.close()

    msg = (f"{stats['primaries']} watchlist objects vs {stats['objects']} objects over {horizon_h:g} h "
           f"(threshold {threshold:g} km): {stats['pairs']} pairs, {stats['candidates']} candidates refined, "
           f"{len(events)} close approaches ({new} new, {updated} updated), {alerts} alerts; "
           f"max Pc {max((e['pc'] or 0) for e in events) if events else 0:.1e}; {stats['total_s']} s")
    return len(events), msg
