"""Automated ingestion (SRS 3.2): CelesTrak element sets -> MongoDB history + MySQL Current_Orbit.

* Every element set goes into MongoDB orbit_history (sharded on norad_id).
  The unique {norad_id, epoch} index makes the history append-only and
  idempotent: a re-downloaded element set is ignored, an older record is
  never overwritten.
* The original JSON of every record is kept with it, and every download is
  logged in download_log.
* The newest element set of each object is then written to MySQL
  Current_Orbit inside ONE transaction, so queries see either the old or the
  new set of orbits, never a mixture.

MongoDB and MySQL cannot share a transaction. MongoDB is written first and
its writes are idempotent, so if the MySQL commit fails the job is simply
retried and the history is not duplicated.
"""
import json
import logging
import re
from datetime import datetime, timezone

from pymongo.errors import BulkWriteError

from orbitwatch import config, db, orbital
from orbitwatch.jobs import sources

log = logging.getLogger(__name__)

HISTORY_FIELDS = orbital.ELEMENT_FIELDS + ("element_set_no", "rev_at_epoch", "perigee_km", "apogee_km",
                                           "mean_altitude_km", "period_min")


def celestrak_sets(cfg):
    """[(label, url)] from the celestrak_groups setting ('special:gpz' -> SPECIAL=gpz)."""
    out = []
    for item in (cfg.get("celestrak_groups") or "active").split(","):
        item = item.strip()
        if not item:
            continue
        if item.startswith("special:"):
            name = item.split(":", 1)[1]
            out.append((f"special-{name}", f"{config.CELESTRAK_GP_URL}?SPECIAL={name}&FORMAT=json"))
        else:
            out.append((item, f"{config.CELESTRAK_GP_URL}?GROUP={item}&FORMAT=json"))
    return out


def guess_type(name):
    upper = f" {name.upper()} "
    if " DEB" in upper or "DEBRIS" in upper:
        return "Debris"
    if " R/B" in upper or "ROCKET" in upper:
        return "Rocket Body"
    return "Unknown"


def store_history(element_sets, source, run_id):
    """Append element sets to MongoDB; returns (inserted, duplicates)."""
    coll = db.mongo_db("jobs").orbit_history
    now = datetime.now(timezone.utc)
    docs = []
    for el in element_sets:
        doc = {k: el[k] for k in HISTORY_FIELDS}
        doc.update(norad_id=el["norad_id"], epoch=el["epoch"].replace(tzinfo=timezone.utc), source=source,
                   download_id=el.get("download_id"), run_id=run_id, ingested_at=now, raw=el.get("raw"))
        docs.append(doc)
    inserted = duplicates = 0
    for i in range(0, len(docs), 5000):
        part = docs[i:i + 5000]
        try:
            inserted += len(coll.insert_many(part, ordered=False).inserted_ids)
        except BulkWriteError as exc:
            errors = exc.details.get("writeErrors", [])
            dup = sum(1 for e in errors if e.get("code") == 11000)
            if dup != len(errors):
                raise
            inserted += exc.details.get("nInserted", 0)
            duplicates += dup
    return inserted, duplicates


def upsert_current_orbits(cur, element_sets, source):
    """Write the newest element set of each object to current_orbit; returns (new, newer, unchanged)."""
    ids = [el["norad_id"] for el in element_sets]
    existing = {}
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        cur.execute(f"SELECT norad_id, epoch FROM current_orbit WHERE norad_id IN ({','.join(['%s'] * len(chunk))})",
                    chunk)
        existing.update(dict(cur.fetchall()))
    rows, new, newer = [], 0, 0
    for el in element_sets:
        old = existing.get(el["norad_id"])
        if old is None:
            new += 1
        elif el["epoch"] > old:
            newer += 1
        else:
            continue
        rows.append((el["norad_id"], el["epoch"], *(el[f] for f in orbital.ELEMENT_FIELDS),
                     el["element_set_no"], el["rev_at_epoch"], source))
    cols = ("mean_motion", "eccentricity", "inclination", "raan", "arg_perigee", "mean_anomaly", "bstar",
            "mean_motion_dot", "mean_motion_ddot", "element_set_no", "rev_at_epoch", "source")
    # Only a newer epoch replaces the stored one; epoch is assigned last because
    # MySQL evaluates ON DUPLICATE KEY assignments left to right.
    updates = ", ".join(f"{c} = IF(n.epoch > current_orbit.epoch, n.{c}, current_orbit.{c})" for c in cols)
    db.bulk_upsert(cur, f"INSERT INTO current_orbit (norad_id, epoch, {', '.join(cols)}) VALUES", rows,
                   f"AS n ON DUPLICATE KEY UPDATE {updates}, epoch = GREATEST(current_orbit.epoch, n.epoch)")
    return new, newer, len(element_sets) - new - newer


def ensure_objects(cur, element_sets):
    """Objects too new for the catalogue snapshot get a minimal space_object row."""
    cur.execute("SELECT norad_id, decay_date FROM space_object")
    known = dict(cur.fetchall())
    cur.execute("SELECT launch_id FROM launch")
    launches = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT intl_designator FROM space_object WHERE intl_designator IS NOT NULL")
    used = {r[0] for r in cur.fetchall()}
    rows = []
    for el in element_sets:
        if el["norad_id"] in known:
            continue
        intl = el["intl_designator"] if el["intl_designator"] not in used else None
        m = re.match(r"^(\d{4}-\d{3})", intl or "")
        rows.append((el["norad_id"], intl, el["name"][:100], guess_type(el["name"]),
                     m.group(1) if m and m.group(1) in launches else None))
        known[el["norad_id"]] = None
    db.bulk_upsert(cur, "INSERT IGNORE INTO space_object (norad_id, intl_designator, name, object_type, launch_id) "
                        "VALUES", rows)
    return len(rows), {n for n, d in known.items() if d is not None}


def run(ctx):
    cfg = db.get_config()
    latest, failures, downloads, total = {}, [], 0, 0
    for label, url in celestrak_sets(cfg):
        try:
            text, dl = sources.fetch(url, f"celestrak_gp:{label}", f"gp_{label}.json", ctx.run_id)
        except RuntimeError as exc:
            failures.append(f"{label}: {exc}")
            continue
        downloads += 1
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = []  # CelesTrak answers 'No GP data found' as plain text for an empty group
        parsed = 0
        for rec in data:
            try:
                el = orbital.parse_omm(rec)
            except (KeyError, ValueError):
                continue
            parsed += 1
            el["raw"], el["download_id"] = rec, dl
            prev = latest.get(el["norad_id"])
            if prev is None or el["epoch"] > prev["epoch"]:
                latest[el["norad_id"]] = el
        total += parsed
        sources.update_download(dl, records=len(data), parsed=parsed)
    if not latest:
        raise RuntimeError("no element sets downloaded: " + "; ".join(failures))

    sets = list(latest.values())
    inserted, duplicates = store_history(sets, "CelesTrak", ctx.run_id)

    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        created, decayed = ensure_objects(cur, sets)
        live = [el for el in sets if el["norad_id"] not in decayed]
        new, newer, unchanged = upsert_current_orbits(cur, live, "CelesTrak")
        conn.commit()
        cur.close()

    msg = (f"{total} element sets from {downloads} downloads ({len(sets)} objects); history: {inserted} new, "
           f"{duplicates} already stored; current_orbit: {new} new, {newer} newer, {unchanged} unchanged; "
           f"{created} new catalogue objects")
    if failures:
        msg += "; FAILED downloads: " + "; ".join(failures)
    return len(sets), msg
