"""Database reconciliation: the previous version (OrbitalGuard) against OrbitWatch.

The previous version kept its data in a Supabase Postgres database (the project no
longer exists) with `orbitalguard.db` (SQLite) as the local copy; every version of that file
in git history (and a copy in runtime/archive, if there is one) is what survives. This module compares them with
OrbitWatch's MySQL catalogue and MongoDB history and, with apply=True, fixes what it
finds. Nothing is copied blindly:

* test fixtures (accounts on reserved test domains, events and agent runs created
  by tests) are removed;
* real CelesTrak element sets from the archive are added to the orbit history when
  that object and epoch are not already there, and become the current orbit only
  where they are newer than it;
* real archived close approaches, their final risk score and the decisions taken
  on them are imported as an archive (no alerts are sent for them);
* synthetic-debris demo data from the archive goes to the demo tables only, never
  into the catalogue or statistics;
* orphans, duplicates and conflicting values are reported and repaired.

Every finding and action is written to runtime/reports/reconcile-<time>.{md,json}.
"""
import json
import logging
import sqlite3
import subprocess
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from orbitwatch import config, db, orbital
from orbitwatch.jobs.ingest import store_history
from orbitwatch.notify import is_reserved_address

log = logging.getLogger(__name__)

ARCHIVE_FILE = "orbitalguard.db"
HISTORY_SOURCE = "OrbitalGuard archive (CelesTrak)"
SYNTHETIC_SOURCES = {"SyntheticGenerator"}
TEST_SOURCES = {"unit-test"}
# Agent runs started from the CLI (no requesting user) before the takeover clean-up were development tests.
TEST_ERA_END = datetime(2026, 10, 5, 14, 0)
EPOCH_TOLERANCE_S = 0.01                   # a TLE epoch resolves to ~1 ms; OMM epochs carry microseconds
REPORT_DIR = config.RUNTIME_DIR / "reports"

TABLE_KEYS = {
    "orbital_objects": "object_id",
    "conjunction_candidates": "conjunction_id",
    "risk_assessments": "id",
    "maneuver_candidates": "maneuver_id",
    "maneuver_decisions": "conjunction_id",
    "decision_feedback": "id",
    "historical_tle": "id",
}


is_test_email = is_reserved_address          # accounts on reserved test domains are test fixtures


def _ts(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return orbital.utc_naive(value)
    text = str(value).strip().replace(" ", "T")
    dt = datetime.fromisoformat(text)
    return orbital.utc_naive(dt) if dt.tzinfo else dt


# --------------------------------------------------------------------------- archive
def _git(*args):
    return subprocess.run(["git", "-C", str(config.REPO_ROOT), *args], capture_output=True, text=True,
                          check=False).stdout


def archive_snapshots(include_git=True):
    """[(label, path)] oldest first: every distinct version of orbitalguard.db in git, then the local copy.

    The file is not part of the repository any more (its versions are in git history); a copy kept in
    ARCHIVE_DIR (runtime/archive) or, as before, in the repository root is read as well."""
    out, tmp = [], Path(tempfile.mkdtemp(prefix="og-archive-"))
    if include_git:
        commits = _git("log", "--all", "--reverse", "--format=%H", "--", ARCHIVE_FILE).split()
        commits += [c for c in _git("reflog", "show", "--format=%H", "refs/stash").split() if c not in commits]
        seen = set()
        for c in commits:
            blob = _git("rev-parse", f"{c}:{ARCHIVE_FILE}").strip()
            if not blob or blob in seen or len(blob) != 40:
                continue
            seen.add(blob)
            path = tmp / f"{c[:10]}.db"
            data = subprocess.run(["git", "-C", str(config.REPO_ROOT), "cat-file", "blob", blob],
                                  capture_output=True, check=False).stdout
            if data[:16] == b"SQLite format 3\x00":
                path.write_bytes(data)
                out.append((f"git {c[:7]}", path))
    for label, folder in (("local copy", config.ARCHIVE_DIR), ("working copy", config.REPO_ROOT)):
        if (folder / ARCHIVE_FILE).exists():
            out.append((label, folder / ARCHIVE_FILE))
    return out


def load_archive(snapshots):
    """Union of every snapshot: rows by primary key (newest snapshot wins), element sets by (object, epoch)."""
    tables = {t: {} for t in TABLE_KEYS}
    tles = {}
    stats = []
    for label, path in snapshots:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        counts = {}
        for table, key in TABLE_KEYS.items():
            try:
                rows = con.execute(f"SELECT * FROM {table}").fetchall()
            except sqlite3.Error:
                continue
            counts[table] = len(rows)
            for r in rows:
                tables[table][r[key]] = dict(r)
                if table == "orbital_objects" and r["raw_tle_line1"]:
                    tles[(str(r["catalog_id"]).strip(), r["raw_tle_line1"][18:32])] = {
                        "object_id": r["object_id"], "name": r["name"], "source": r["source"],
                        "object_type": r["object_type"], "line1": r["raw_tle_line1"], "line2": r["raw_tle_line2"],
                        "snapshot": label}
                elif table == "historical_tle" and r["raw_tle"]:
                    lines = [x for x in str(r["raw_tle"]).splitlines() if x.strip()]
                    if len(lines) >= 2:
                        tles[(str(r["catalog_id"]).strip(), lines[-2][18:32])] = {
                            "object_id": r["object_id"], "name": None, "source": r["source"], "object_type": None,
                            "line1": lines[-2], "line2": lines[-1], "snapshot": label}
        con.close()
        stats.append({"snapshot": label, **counts})
    return {"tables": tables, "tles": tles, "snapshots": stats}


# --------------------------------------------------------------------------- analysis
class Report:
    def __init__(self):
        self.sections = []
        self.summary = {}

    def section(self, title, findings=None, actions=None, details=None):
        self.sections.append({"title": title, "findings": findings or [], "actions": actions or [],
                              "details": details or {}})

    def markdown(self, applied):
        lines = [f"# OrbitWatch database reconciliation ({'applied' if applied else 'dry run'})",
                 f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC", ""]
        lines += ["## Summary", ""] + [f"- {k}: {v}" for k, v in self.summary.items()] + [""]
        for s in self.sections:
            lines += [f"## {s['title']}", ""]
            lines += [f"- {f}" for f in s["findings"]] or ["- nothing found"]
            if s["actions"]:
                lines += ["", "Actions:"] + [f"- {a}" for a in s["actions"]]
            lines.append("")
        return "\n".join(lines)


def _classify_archive(arch):
    """Split archive rows into real / synthetic / test / orphan."""
    t = arch["tables"]
    objects = t["orbital_objects"]
    synthetic_ids = {oid for oid, o in objects.items()
                     if o["source"] in SYNTHETIC_SOURCES or (o["object_type"] or "") == "SYNTHETIC_DEBRIS"}
    test_ids = {oid for oid, o in objects.items() if o["source"] in TEST_SOURCES}
    conj = t["conjunction_candidates"]
    real_conj, synthetic_conj, test_conj, unresolved_conj = {}, {}, {}, {}
    for cid, c in conj.items():
        ids = {c["primary_object_id"], c["secondary_object_id"]}
        if not ids <= set(objects):
            unresolved_conj[cid] = c
        elif ids & test_ids:
            test_conj[cid] = c
        elif ids & synthetic_ids:
            synthetic_conj[cid] = c
        else:
            real_conj[cid] = c
    by_conj = lambda table: {k: v for k, v in t[table].items() if v["conjunction_id"] in conj}
    orphan = lambda table: {k: v for k, v in t[table].items() if v["conjunction_id"] not in conj}
    return {
        "objects": objects, "synthetic_ids": synthetic_ids, "test_ids": test_ids,
        "real_conj": real_conj, "synthetic_conj": synthetic_conj, "test_conj": test_conj,
        "unresolved_conj": unresolved_conj,
        "risk": by_conj("risk_assessments"), "risk_orphans": orphan("risk_assessments"),
        "candidates": by_conj("maneuver_candidates"), "candidate_orphans": orphan("maneuver_candidates"),
        "decisions": by_conj("maneuver_decisions"), "decision_orphans": orphan("maneuver_decisions"),
        "feedback": by_conj("decision_feedback"), "feedback_orphans": orphan("decision_feedback"),
    }


def _norad(objects, object_id):
    o = objects.get(object_id)
    try:
        return int(str(o["catalog_id"]).strip()) if o else None
    except ValueError:
        return None


def analyse(include_git=True):
    """Everything reconcile() would do, without changing anything. Returns (report, plan)."""
    rep, plan = Report(), {}

    # ---- 1. test fixtures in OrbitWatch ------------------------------------------------------
    users = db.query("admin", "SELECT user_id, email, role, created_at FROM app_user")
    test_users = [u for u in users if is_test_email(u["email"])]
    test_uids = [u["user_id"] for u in test_users]
    test_events = db.query("admin", """
        SELECT event_id, primary_norad, secondary_norad, time_of_closest_approach, risk_level
          FROM conjunction_event
         WHERE origin = 'orbitwatch'
           AND (run_id IS NULL OR time_of_closest_approach > created_at + INTERVAL 30 DAY)""")
    ph = ",".join(["%s"] * len(test_uids)) or "NULL"
    test_assessments = db.query("admin", f"""
        SELECT assessment_id, event_id, requested_by, engine, started_at FROM agent_assessment
         WHERE origin = 'orbitwatch'
           AND (requested_by IN ({ph}) OR (requested_by IS NULL AND started_at < %s))""",
                                (*test_uids, TEST_ERA_END))
    subs = db.query("admin", f"SELECT user_id, norad_id FROM subscription WHERE user_id IN ({ph})", tuple(test_uids))
    alerts = db.query("admin", f"SELECT alert_id, event_id, user_id FROM alert WHERE user_id IN ({ph})", tuple(test_uids))
    plan["test"] = {"users": test_uids, "events": [e["event_id"] for e in test_events],
                    "assessments": [a["assessment_id"] for a in test_assessments]}
    rep.section("Test data", [
        f"{len(test_users)} accounts on reserved test domains: "
        + ", ".join(f"#{u['user_id']} {u['email']} ({u['role']})" for u in test_users),
        f"{len(test_events)} close approaches not produced by a screening run: "
        + ", ".join(f"#{e['event_id']} {e['risk_level']} TCA {e['time_of_closest_approach']:%Y-%m-%d}" for e in test_events),
        f"{len(test_assessments)} agent assessments run by test accounts or from the CLI during development: "
        + ", ".join(f"#{a['assessment_id']}" for a in test_assessments),
        f"{len(subs)} subscriptions and {len(alerts)} alerts belonging to test accounts (removed with them)",
    ], [f"delete the {len(test_users)} accounts, {len(test_events)} events and {len(test_assessments)} assessments "
        "(subscriptions, alerts and assessment steps cascade)"])

    # ---- 2. the archive -----------------------------------------------------------------------
    snaps = archive_snapshots(include_git)
    arch = load_archive(snaps)
    cls = _classify_archive(arch)
    known = {r["norad_id"]: r for r in db.query("admin", "SELECT norad_id, name, decay_date, in_earth_orbit FROM space_object")}
    objects = cls["objects"]

    real_tles, skipped = [], Counter()
    for (cat, _), rec in arch["tles"].items():
        if rec["source"] in SYNTHETIC_SOURCES or (rec["object_type"] or "") == "SYNTHETIC_DEBRIS":
            skipped["synthetic object"] += 1
            continue
        if rec["source"] in TEST_SOURCES:
            skipped["unit-test object"] += 1
            continue
        try:
            el = orbital.parse_tle(rec["line1"], rec["line2"], rec["name"])
        except ValueError as exc:
            skipped[f"unparseable TLE ({exc})"] += 1
            continue
        if el["norad_id"] not in known:
            skipped["object not in the OrbitWatch catalogue"] += 1
            continue
        el["raw"] = {"format": "TLE", "line1": rec["line1"], "line2": rec["line2"], "archive_snapshot": rec["snapshot"],
                     "orbitalguard_source": rec["source"], "orbitalguard_object_id": rec["object_id"]}
        real_tles.append(el)

    # which of those element sets the history already has (to the TLE's ~1 ms resolution)
    hist = db.mongo_db("analyst").orbit_history
    ids = sorted({el["norad_id"] for el in real_tles})
    have = defaultdict(list)
    for i in range(0, len(ids), 5000):
        for d in hist.find({"norad_id": {"$in": ids[i:i + 5000]}}, {"_id": 0, "norad_id": 1, "epoch": 1}):
            have[d["norad_id"]].append(orbital.utc_naive(d["epoch"]))
    new_tles = [el for el in real_tles
                if not any(abs((el["epoch"] - e).total_seconds()) <= EPOCH_TOLERANCE_S for e in have[el["norad_id"]])]
    current = {r["norad_id"]: r["epoch"] for r in db.query("admin", "SELECT norad_id, epoch FROM current_orbit")}
    newest = {}
    for el in new_tles:
        k = known[el["norad_id"]]
        if k["decay_date"] is None and k["in_earth_orbit"]:
            if el["norad_id"] not in newest or el["epoch"] > newest[el["norad_id"]]["epoch"]:
                newest[el["norad_id"]] = el
    newer_than_current = [el for n, el in newest.items() if n not in current or el["epoch"] > current[n]]
    name_mismatch = sum(1 for el in real_tles if el["name"] and known[el["norad_id"]]["name"]
                        and el["name"].upper() != known[el["norad_id"]]["name"].upper())
    plan["history"] = new_tles
    plan["current_from_archive"] = newer_than_current
    rep.section("Archive element sets", [
        f"{len(snaps)} distinct versions of {ARCHIVE_FILE} (git history, stashes and the working copy): "
        + "; ".join(f"{s['snapshot']}: {s.get('orbital_objects', 0)} objects" for s in arch["snapshots"]),
        f"{len(arch['tles'])} distinct element sets; {len(real_tles)} real ones for catalogued objects",
        "not used: " + (", ".join(f"{v} {k}" for k, v in skipped.items()) or "none"),
        f"{len(real_tles) - len(new_tles)} already in the MongoDB history (same object and epoch)",
        f"{len(new_tles)} not yet in the history, for {len({e['norad_id'] for e in new_tles})} objects",
        f"{len(newer_than_current)} are newer than (or missing from) the object's current orbit in MySQL",
        f"{name_mismatch} archive names differ from the SATCAT name (the catalogue name is kept)",
    ], [f"append {len(new_tles)} element sets to orbit_history (source '{HISTORY_SOURCE}')",
        f"update current_orbit for {len(newer_than_current)} objects whose newest element set is in the archive"])

    # ---- 3. archived close approaches and decisions -------------------------------------------
    existing_refs = {r["legacy_ref"] for r in db.query("admin", "SELECT legacy_ref FROM conjunction_event "
                                                                 "WHERE legacy_ref IS NOT NULL")}
    real_rows, unmapped = [], []
    for cid, c in cls["real_conj"].items():
        p, s = _norad(objects, c["primary_object_id"]), _norad(objects, c["secondary_object_id"])
        if p in known and s in known and p != s:
            real_rows.append((cid, p, s, c))
        else:
            unmapped.append(cid)
    risk_by_conj = defaultdict(list)
    for r in cls["risk"].values():
        risk_by_conj[r["conjunction_id"]].append(r)
    plan["real_conj"] = [r for r in real_rows if r[0] not in existing_refs]
    plan["risk_by_conj"] = risk_by_conj
    real_decisions = {k: v for k, v in cls["decisions"].items() if k in cls["real_conj"]}
    plan["real_decisions"] = real_decisions
    rep.section("Archived close approaches", [
        f"{len(arch['tables']['conjunction_candidates'])} close approaches in the archive: {len(cls['real_conj'])} between "
        f"real objects, {len(cls['synthetic_conj'])} involving synthetic demo debris, {len(cls['test_conj'])} involving "
        f"unit-test objects, {len(cls['unresolved_conj'])} with unknown objects",
        f"{len(real_rows)} real ones map onto catalogued objects; {len(unmapped)} do not",
        f"{len(real_rows) - len(plan['real_conj'])} already imported",
        f"{sum(len(risk_by_conj[c]) for c in cls['real_conj'])} risk re-scores of the real ones "
        f"(kept: the final score of each, with the number of re-scores)",
        f"{len(real_decisions)} manoeuvre decisions on real close approaches "
        + ", ".join(f"{k}: {v.get('simulation_status')}" for k, v in real_decisions.items()),
    ], [f"import {len(plan['real_conj'])} as archived events (origin 'orbitalguard-archive', no alerts)",
        f"import the {len(real_decisions)} decisions as archived assessments with their approval"])

    # ---- 4. synthetic demo data from the archive -------------------------------------------------
    plan["synthetic"] = cls
    rep.section("Archived synthetic-debris demo", [
        f"{len(cls['synthetic_ids'])} synthetic objects, {len(cls['synthetic_conj'])} synthetic close approaches",
        f"{sum(1 for k in cls['decisions'] if k in cls['synthetic_conj'])} manoeuvre decisions and "
        f"{len(cls['feedback'])} rejections with reviewer feedback on synthetic events",
        f"{len(cls['candidates'])} manoeuvre candidates",
        f"{len(cls['test_ids'])} unit-test object and {len(cls['test_conj'])} events with it: not imported (test fixture)",
    ], ["import into the demo tables only (status 'archived', labelled SYNTHETIC); never into the catalogue"])

    rep.section("Archive orphans (not importable)", [
        f"{len(cls['risk_orphans'])} risk assessments, {len(cls['candidate_orphans'])} manoeuvre candidates, "
        f"{len(cls['decision_orphans'])} decisions and {len(cls['feedback_orphans'])} feedback rows reference close "
        "approaches that are in no snapshot",
    ], ["left in the archive file; reported here"])

    # ---- 5. OrbitWatch integrity -----------------------------------------------------------------
    findings, fixes = [], []
    newest_hist = {d["_id"]: orbital.utc_naive(d["epoch"]) for d in hist.aggregate(
        [{"$group": {"_id": "$norad_id", "epoch": {"$max": "$epoch"}}}], allowDiskUse=True)}
    orphan_hist = [n for n in newest_hist if n not in known]
    stale_current = [n for n, e in newest_hist.items()
                     if n in current and e > current[n] + timedelta(seconds=EPOCH_TOLERANCE_S)]
    missing_current = [n for n in newest_hist if n in known and n not in current
                       and known[n]["decay_date"] is None and known[n]["in_earth_orbit"]]
    plan["stale_current"] = stale_current + missing_current
    findings.append(f"{len(orphan_hist)} objects in the MongoDB history are not in the MySQL catalogue")
    findings.append(f"{len(stale_current)} current orbits are older than the newest element set in the history; "
                    f"{len(missing_current)} Earth-orbiting objects have history but no current orbit")
    idx = hist.index_information()
    unique_ok = any(v.get("unique") and [k for k, _ in v["key"]] == ["norad_id", "epoch"] for v in idx.values())
    findings.append("unique {norad_id, epoch} index on orbit_history: " + ("present" if unique_ok else "MISSING"))
    bad_current = db.query("admin", """SELECT co.norad_id FROM current_orbit co JOIN space_object so USING (norad_id)
                                        WHERE NOT so.in_earth_orbit""")
    plan["non_earth_current"] = [r["norad_id"] for r in bad_current]
    findings.append(f"{len(bad_current)} current orbits for objects that have re-entered or are not in Earth orbit")
    dup_events = db.query("admin", """
        SELECT a.event_id AS keep_id, b.event_id AS dup_id FROM conjunction_event a JOIN conjunction_event b
          ON b.event_id > a.event_id AND a.origin = b.origin
         AND LEAST(a.primary_norad, a.secondary_norad) = LEAST(b.primary_norad, b.secondary_norad)
         AND GREATEST(a.primary_norad, a.secondary_norad) = GREATEST(b.primary_norad, b.secondary_norad)
         AND ABS(TIMESTAMPDIFF(SECOND, a.time_of_closest_approach, b.time_of_closest_approach))
             <= IF(LEAST(a.relative_velocity, b.relative_velocity) < 0.1, 86400, 900)""")
    plan["dup_events"] = dup_events
    findings.append(f"{len(dup_events)} duplicate close approaches (same pair within the matching window)")
    bad_watch = db.query("admin", """SELECT w.norad_id, so.name FROM watchlist w JOIN space_object so USING (norad_id)
                                      WHERE NOT so.in_earth_orbit""")
    plan["bad_watch"] = [r["norad_id"] for r in bad_watch]
    findings.append(f"{len(bad_watch)} watchlist entries that are not in Earth orbit"
                    + (": " + ", ".join(f"{r['norad_id']} {r['name']}" for r in bad_watch) if bad_watch else ""))
    stray_alerts = db.query("admin", """SELECT a.alert_id FROM alert a JOIN conjunction_event e USING (event_id)
                                         LEFT JOIN subscription s ON s.user_id = a.user_id
                                          AND s.norad_id IN (e.primary_norad, e.secondary_norad)
                                        WHERE s.user_id IS NULL""")
    findings.append(f"{len(stray_alerts)} alerts for objects the user no longer follows (kept: the alert was valid when sent)")
    orphan_summaries = db.query_one("admin", """SELECT COUNT(*) AS n FROM summary_monthly_altitude s
                                                 LEFT JOIN space_object so USING (norad_id) WHERE so.norad_id IS NULL""")["n"]
    findings.append(f"{orphan_summaries} monthly summaries without a catalogue object")
    unknown_models = db.query_one("admin", """SELECT COUNT(*) AS n FROM reentry_prediction p
                                               LEFT JOIN ml_model m ON m.model_version = p.model_version
                                              WHERE m.model_version IS NULL""")["n"]
    plan["unregistered_predictions"] = unknown_models
    findings.append(f"{unknown_models} re-entry predictions from a model that is not in the model registry")
    rep.section("OrbitWatch integrity", findings, [
        f"rebuild the current orbit of {len(plan['stale_current'])} objects from their newest element set",
        f"remove {len(plan['non_earth_current'])} current orbits of objects not in Earth orbit",
        f"merge {len(dup_events)} duplicate events",
        f"remove {len(bad_watch)} watchlist entries that are not in Earth orbit",
        f"remove {unknown_models} predictions with no registered model",
    ])
    rep.summary = {
        "test accounts": len(test_users), "test events": len(test_events), "test assessments": len(test_assessments),
        "archive element sets to add": len(new_tles), "current orbits updated from archive": len(newer_than_current),
        "archived real close approaches to import": len(plan["real_conj"]),
        "archived real decisions": len(real_decisions),
        "archived synthetic events (demo tables)": len(cls["synthetic_conj"]),
        "stale current orbits": len(plan["stale_current"]), "duplicate events": len(dup_events),
    }
    return rep, plan


# --------------------------------------------------------------------------- apply
def _apply_test_cleanup(cur, plan):
    t = plan["test"]
    n = {}
    for key, table, col in (("assessments", "agent_assessment", "assessment_id"),
                            ("events", "conjunction_event", "event_id"),
                            ("users", "app_user", "user_id")):
        ids = t[key]
        if ids:
            cur.execute(f"DELETE FROM {table} WHERE {col} IN ({','.join(['%s'] * len(ids))})", ids)
        n[key] = cur.rowcount if ids else 0
    return n


def _apply_history(plan, run_id):
    sets = plan["history"]
    inserted, dup = store_history(sets, HISTORY_SOURCE, run_id) if sets else (0, 0)
    return inserted, dup


def _upsert_current(cur, sets, source):
    from orbitwatch.jobs.ingest import upsert_current_orbits
    if not sets:
        return 0
    new, newer, _ = upsert_current_orbits(cur, sets, source)
    return new + newer


def _apply_current_from_history(cur, plan):
    ids = plan["stale_current"]
    if not ids:
        return 0
    hist = db.mongo_db("analyst").orbit_history
    sets = []
    for i in range(0, len(ids), 2000):
        for d in hist.aggregate([{"$match": {"norad_id": {"$in": ids[i:i + 2000]}}}, {"$sort": {"epoch": -1}},
                                 {"$group": {"_id": "$norad_id", "doc": {"$first": "$$ROOT"}}}]):
            doc = d["doc"]
            el = {k: doc[k] for k in orbital.ELEMENT_FIELDS}
            el.update(norad_id=doc["norad_id"], epoch=orbital.utc_naive(doc["epoch"]),
                      element_set_no=doc.get("element_set_no"), rev_at_epoch=doc.get("rev_at_epoch"))
            el["_source"] = "Space-Track" if doc.get("source") == "Space-Track" else "CelesTrak"
            sets.append(el)
    total = 0
    for src in ("CelesTrak", "Space-Track"):
        total += _upsert_current(cur, [s for s in sets if s["_source"] == src], src)
    return total


def _risk_level_sql(cur, miss, vel):
    cur.execute("SELECT fn_risk_level(%s, %s)", (miss, vel))
    return cur.fetchone()[0]


def _apply_real_conjunctions(cur, plan):
    n_ev = n_risk = 0
    event_ids = {}
    cur.execute("SELECT legacy_ref, event_id FROM conjunction_event WHERE legacy_ref IS NOT NULL")
    event_ids.update(dict(cur.fetchall()))
    for cid, p, s, c in plan["real_conj"]:
        miss, vel = float(c["miss_distance_km"]), float(c["relative_velocity_km_s"])
        cur.execute("""INSERT INTO conjunction_event (primary_norad, secondary_norad, time_of_closest_approach,
                           miss_distance_km, relative_velocity, risk_level, probability_of_collision, pc_method,
                           origin, legacy_ref, created_at)
                       VALUES (%s, %s, %s, %s, %s, fn_risk_level(%s, %s), %s, %s, 'orbitalguard-archive', %s, %s)""",
                    (p, s, _ts(c["tca"]), round(miss, 3), round(vel, 3), miss, vel, c.get("collision_probability"),
                     c.get("pc_method"), cid, _ts(c.get("created_at")) or datetime.now(timezone.utc).replace(tzinfo=None)))
        event_ids[cid] = cur.lastrowid
        n_ev += 1
    for cid, rows in plan["risk_by_conj"].items():
        if cid not in event_ids or cid not in plan["synthetic"]["real_conj"]:
            continue
        last = max(rows, key=lambda r: _ts(r.get("evaluated_at")) or datetime.min)
        cur.execute("""INSERT INTO archive_risk_assessment (event_id, risk_score, risk_level, collision_probability,
                           uncertainty_model, confidence, notes, evaluated_at, reassessments)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE reassessments = VALUES(reassessments)""",
                    (event_ids[cid], last["risk_score"], last["risk_level"], last.get("collision_probability"),
                     last.get("uncertainty_model"), last.get("confidence"), last.get("notes"),
                     _ts(last.get("evaluated_at")) or datetime.now(timezone.utc).replace(tzinfo=None), len(rows)))
        n_risk += 1
    return n_ev, n_risk, event_ids


def _archived_assessment(cur, legacy_ref, event_id, demo_event_id, decision_row, candidates, started):
    cur.execute("SELECT assessment_id FROM agent_assessment WHERE legacy_ref = %s", (legacy_ref,))
    row = cur.fetchone()
    if row:
        return row[0], False
    rec = next((c for c in candidates if c["maneuver_id"] == (decision_row or {}).get("recommended_maneuver_id")), None)
    maneuver = {"recommended_maneuver_id": (decision_row or {}).get("recommended_maneuver_id"),
                "candidates": [{k: c.get(k) for k in ("maneuver_id", "delta_v_m_s", "burn_direction", "new_separation_km",
                                                      "resulting_risk")} for c in candidates]}
    cur.execute("""INSERT INTO agent_assessment (event_id, demo_event_id, status, engine, decision, delta_v_mps,
                       burn_direction, maneuver, explanation, human_approval_required, started_at, finished_at,
                       origin, legacy_ref)
                   VALUES (%s, %s, 'complete', 'OrbitalGuard (archived)', 'MANEUVER_RECOMMENDED', %s, %s, %s, %s, %s,
                           %s, %s, 'orbitalguard-archive', %s)""",
                (event_id, demo_event_id, rec["delta_v_m_s"] if rec else None, rec["burn_direction"] if rec else None,
                 json.dumps(maneuver), (decision_row or {}).get("decision_reason"),
                 bool((decision_row or {}).get("human_approval_required", True)), started, started, legacy_ref))
    return cur.lastrowid, True


def _apply_decisions(cur, plan, event_ids, demo_ids):
    cls = plan["synthetic"]
    cands = defaultdict(list)
    for c in cls["candidates"].values():
        cands[c["conjunction_id"]].append(c)
    n_assess = n_dec = 0
    for cid, d in cls["decisions"].items():
        ev, demo = event_ids.get(cid), demo_ids.get(cid)
        if ev is None and demo is None:
            continue
        started = _ts(d.get("created_at")) or datetime.now(timezone.utc).replace(tzinfo=None)
        aid, created = _archived_assessment(cur, f"og-decision:{cid}", ev, demo, d, cands[cid], started)
        n_assess += created
        if d.get("simulation_status") == "EXECUTED":
            new_sep = d.get("new_tca_distance_km")
            cur.execute("""INSERT IGNORE INTO maneuver_decision (assessment_id, status, decided_at, reason,
                               miss_after_km, execution, origin)
                           VALUES (%s, 'APPROVED', %s, 'Approved and executed in OrbitalGuard''s simulation', %s,
                                   'SIMULATED', 'orbitalguard-archive')""", (aid, started, new_sep))
            n_dec += cur.rowcount
    for fid, f in cls["feedback"].items():
        cid = f["conjunction_id"]
        ev, demo = event_ids.get(cid), demo_ids.get(cid)
        if ev is None and demo is None:
            continue
        started = _ts(f.get("timestamp")) or datetime.now(timezone.utc).replace(tzinfo=None)
        d = {"recommended_maneuver_id": f.get("maneuver_id"), "decision_reason": None, "human_approval_required": True}
        aid, created = _archived_assessment(cur, f"og-feedback:{fid}", ev, demo, d, cands[cid], started)
        n_assess += created
        cur.execute("""INSERT IGNORE INTO maneuver_decision (assessment_id, status, decided_at, reason, execution, origin)
                       VALUES (%s, 'REJECTED', %s, %s, 'NONE', 'orbitalguard-archive')""",
                    (aid, started, (f.get("reason") or "Rejected in OrbitalGuard")[:1000]))
        n_dec += cur.rowcount
    return n_assess, n_dec


def _apply_synthetic(cur, plan):
    """Archived demo: one archived scenario per real target, synthetic objects and events in the demo tables."""
    cls = plan["synthetic"]
    objects = cls["objects"]
    targets = defaultdict(list)
    for cid, c in cls["synthetic_conj"].items():
        sides = [c["primary_object_id"], c["secondary_object_id"]]
        real = [o for o in sides if o not in cls["synthetic_ids"]]
        syn = [o for o in sides if o in cls["synthetic_ids"]]
        if len(real) == 1 and len(syn) == 1:
            targets[_norad(objects, real[0])].append((cid, syn[0], c))
    cur.execute("SELECT norad_id FROM space_object")
    known = {r[0] for r in cur.fetchall()}
    demo_ids, n_obj, n_ev = {}, 0, 0
    cur.execute("SELECT legacy_ref, demo_event_id FROM demo_event WHERE legacy_ref IS NOT NULL")
    demo_ids.update(dict(cur.fetchall()))
    for target, events in targets.items():
        if target not in known:
            continue
        cur.execute("SELECT scenario_id FROM demo_scenario WHERE origin = 'orbitalguard-archive' AND target_norad = %s",
                    (target,))
        row = cur.fetchone()
        if row:
            scenario = row[0]
        else:
            first = min(_ts(c.get("created_at")) or datetime.now(timezone.utc).replace(tzinfo=None) for _, _, c in events)
            cur.execute("""INSERT INTO demo_scenario (target_norad, created_at, status, origin, notes)
                           VALUES (%s, %s, 'archived', 'orbitalguard-archive', %s)""",
                        (target, first, "Synthetic debris injected by OrbitalGuard's demo; imported for the record."))
            scenario = cur.lastrowid
        obj_ids = {}
        for cid, syn_id, c in events:
            if syn_id not in obj_ids:
                o = objects[syn_id]
                designation = f"SYN-OG-{str(o['catalog_id']).strip()}"
                cur.execute("SELECT demo_object_id FROM demo_object WHERE designation = %s", (designation,))
                row = cur.fetchone()
                if row:
                    obj_ids[syn_id] = row[0]
                else:
                    try:
                        el = orbital.parse_tle(o["raw_tle_line1"], o["raw_tle_line2"], o["name"])
                    except ValueError:
                        obj_ids[syn_id] = None
                        continue
                    name = o["name"] if "SYNTHETIC" in o["name"].upper() else f"{o['name']} [SYNTHETIC]"
                    cur.execute(f"""INSERT INTO demo_object (scenario_id, designation, name, epoch,
                                        {', '.join(orbital.ELEMENT_FIELDS)})
                                    VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(orbital.ELEMENT_FIELDS))})""",
                                (scenario, designation, name[:100], el["epoch"], *(el[f] for f in orbital.ELEMENT_FIELDS)))
                    obj_ids[syn_id] = cur.lastrowid
                    n_obj += 1
            if cid in demo_ids:
                continue
            miss, vel = float(c["miss_distance_km"]), float(c["relative_velocity_km_s"])
            cur.execute("""INSERT INTO demo_event (scenario_id, demo_object_id, target_norad, time_of_closest_approach,
                               miss_distance_km, relative_velocity, risk_level, probability_of_collision, pc_method,
                               legacy_ref, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, fn_risk_level(%s, %s), %s, %s, %s, %s)""",
                        (scenario, obj_ids.get(syn_id), target, _ts(c["tca"]), round(miss, 3), round(vel, 3), miss, vel,
                         c.get("collision_probability"), c.get("pc_method"), cid,
                         _ts(c.get("created_at")) or datetime.now(timezone.utc).replace(tzinfo=None)))
            demo_ids[cid] = cur.lastrowid
            n_ev += 1
    return n_obj, n_ev, demo_ids


def _apply_integrity(cur, plan):
    out = {}
    ids = plan["non_earth_current"]
    if ids:
        cur.execute(f"DELETE FROM current_orbit WHERE norad_id IN ({','.join(['%s'] * len(ids))})", ids)
    out["non_earth_current_removed"] = len(ids)
    for d in plan["dup_events"]:
        cur.execute("UPDATE IGNORE alert SET event_id = %s WHERE event_id = %s", (d["keep_id"], d["dup_id"]))
        cur.execute("DELETE FROM conjunction_event WHERE event_id = %s", (d["dup_id"],))
    out["duplicate_events_merged"] = len(plan["dup_events"])
    if plan["bad_watch"]:
        cur.execute(f"DELETE FROM watchlist WHERE norad_id IN ({','.join(['%s'] * len(plan['bad_watch']))})",
                    plan["bad_watch"])
    out["watchlist_removed"] = len(plan["bad_watch"])
    if plan["unregistered_predictions"]:
        cur.execute("""DELETE p FROM reentry_prediction p LEFT JOIN ml_model m ON m.model_version = p.model_version
                        WHERE m.model_version IS NULL""")
    out["unregistered_predictions_removed"] = plan["unregistered_predictions"]
    return out


def _backfill_provenance(cur):
    """Freshness of sources that ran before provenance was tracked, from their job history; and the download
    time of current orbits stored before fetched_at existed, from the MongoDB history record of the same epoch."""
    mapping = {"celestrak_satcat": "catalog", "gcat": "catalog", "noaa_swpc": "space_weather", "celestrak_gp": "ingest"}
    n_sources = 0
    for key, job in mapping.items():
        cur.execute("""UPDATE data_source ds
                          JOIN (SELECT MAX(finished_at) AS t, MAX(started_at) AS a FROM job_run
                                 WHERE job_name = %s AND status = 'success') j
                           SET ds.last_success_at = j.t, ds.last_attempt_at = COALESCE(ds.last_attempt_at, j.a),
                               ds.last_status = 'ok', ds.last_message = 'from job history (before provenance tracking)'
                         WHERE ds.source_key = %s AND ds.last_success_at IS NULL AND j.t IS NOT NULL""", (job, key))
        n_sources += cur.rowcount
    cur.execute("SELECT norad_id, epoch FROM current_orbit WHERE fetched_at IS NULL")
    missing = cur.fetchall()
    hist = db.mongo_db("analyst").orbit_history
    rows = []
    for i in range(0, len(missing), 2000):
        part = missing[i:i + 2000]
        # MongoDB keeps datetimes to the millisecond, MySQL to the microsecond: match on milliseconds
        ms = lambda dt: dt.replace(microsecond=dt.microsecond // 1000 * 1000)
        wanted = {(n, ms(e)): e for n, e in part}
        for d in hist.find({"norad_id": {"$in": [n for n, _ in part]}},
                           {"_id": 0, "norad_id": 1, "epoch": 1, "ingested_at": 1, "download_id": 1}):
            key = (d["norad_id"], ms(orbital.utc_naive(d["epoch"])))
            if key in wanted and d.get("ingested_at"):
                rows.append((orbital.utc_naive(d["ingested_at"]), d.get("download_id"), key[0], wanted[key]))
    for r in rows:
        cur.execute("UPDATE current_orbit SET fetched_at = %s, download_id = %s WHERE norad_id = %s AND epoch = %s", r)
    return {"sources_backfilled": n_sources, "orbit_provenance_backfilled": len(rows),
            "orbits_without_provenance": len(missing) - len(rows)}


def run(ctx=None, apply=False, include_git=True):
    rep, plan = analyse(include_git)
    results = {}
    if apply:
        run_id = ctx.run_id if ctx else None
        with db.mysql_conn("admin") as conn:
            cur = conn.cursor()
            results["test data removed"] = _apply_test_cleanup(cur, plan)
            conn.commit()
        results["history element sets added"], results["history duplicates skipped"] = _apply_history(plan, run_id)
        with db.mysql_conn("admin") as conn:
            cur = conn.cursor()
            results["current orbits from archive"] = _upsert_current(cur, plan["current_from_archive"], "CelesTrak")
            n_ev, n_risk, event_ids = _apply_real_conjunctions(cur, plan)
            results["archived events imported"], results["archived risk scores (upserted)"] = n_ev, n_risk
            n_obj, n_dev, demo_ids = _apply_synthetic(cur, plan)
            results["demo objects imported"], results["demo events imported"] = n_obj, n_dev
            results["archived assessments"], results["archived decisions"] = _apply_decisions(cur, plan, event_ids, demo_ids)
            conn.commit()
        # the history now holds the archive too: make every current orbit the newest element set
        _, plan2 = analyse(include_git=False) if plan["history"] else (None, plan)
        with db.mysql_conn("admin") as conn:
            cur = conn.cursor()
            results["current orbits rebuilt from history"] = _apply_current_from_history(cur, plan2)
            results["integrity"] = _apply_integrity(cur, plan2)
            results["provenance"] = _backfill_provenance(cur)
            cur.execute("""UPDATE data_source SET last_attempt_at = NOW(3), last_success_at = NOW(3), last_status = 'ok',
                               last_records = %s, last_message = %s WHERE source_key = 'orbitalguard_archive'""",
                        (results["history element sets added"], "imported by reconciliation"))
            cur.execute("""INSERT INTO event_log (category, severity, visibility, action, entity_type, message, detail)
                           VALUES ('data', 'notice', 'analyst', 'reconcile', 'database', %s, %s)""",
                        ("Database reconciled with the OrbitalGuard archive; test data removed",
                         json.dumps(results, default=str)))
            conn.commit()
        rep.section("Applied", [f"{k}: {v}" for k, v in results.items()])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md = REPORT_DIR / f"reconcile-{stamp}.md"
    md.write_text(rep.markdown(apply), encoding="utf-8")
    (REPORT_DIR / f"reconcile-{stamp}.json").write_text(
        json.dumps({"applied": apply, "summary": rep.summary, "sections": rep.sections, "results": results},
                   default=str, indent=2), encoding="utf-8")
    msg = f"{'applied' if apply else 'dry run'}; report {md}"
    return sum(v for v in rep.summary.values() if isinstance(v, int)), msg
