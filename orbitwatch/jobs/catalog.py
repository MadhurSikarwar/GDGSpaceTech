"""Catalogue reference data -> MySQL, raw records -> MongoDB.

Sources
  * CelesTrak SATCAT (US Space Force catalogue): the object list itself —
    NORAD id, name, international designator, type, operational status,
    decay date.
  * GCAT (J. McDowell, planet4589.org): what the SATCAT lacks — launch log,
    launch vehicles and their makers, launch sites with coordinates,
    organisations with type and country, each object's owner, the parent
    an object broke away from, and the programme (mission) it belongs to.

Everything is written in ONE MySQL transaction, so users never see a
half-refreshed catalogue. Rows are upserted, so the weekly refresh is
idempotent.
"""
import csv
import hashlib
import io
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

from orbitwatch import config, db
from orbitwatch.jobs import sources

log = logging.getLogger(__name__)

GCAT_FILES = {
    "satcat": "cat/satcat", "psatcat": "cat/psatcat", "launchlog": "derived/launchlog",
    "orgs": "tables/orgs", "lv": "tables/lv", "sites": "tables/sites",
}
OBJECT_TYPES = {"PAY": "Payload", "R/B": "Rocket Body", "DEB": "Debris", "UNK": "Unknown"}
STATUS = {"+": "Operational", "-": "Non-operational", "P": "Partially operational", "B": "Backup",
          "S": "Spare", "X": "Extended mission", "D": "Decayed", "?": "Unknown", "": "Unknown"}
ORG_TYPES = {"A": "Academic", "B": "Private", "C": "Government", "D": "Government"}
PURPOSES = {
    "COM": "Communications", "IMG": "Earth imaging", "IMG-R": "Radar imaging", "NAV": "Navigation",
    "MET": "Meteorology", "SCI": "Science", "EOSCI": "Earth science", "TECH": "Technology demonstration",
    "CAL": "Calibration", "SIG": "Signals intelligence", "EW": "Early warning", "GEOD": "Geodesy",
    "AST": "Astronomy", "PLAN": "Planetary science", "SS": "Space station", "BIO": "Biology",
    "WEAPON": "Weapons test", "MET-RO": "Radio occultation", "INF": "Infrastructure",
}
# CelesTrak owner codes -> GCAT state codes, only used for objects too new for GCAT.
CELESTRAK_OWNERS = {"US": "US", "PRC": "CN", "IND": "IN", "JPN": "J", "FR": "F", "UK": "UK", "ESA": "I-ESA",
                    "GER": "D", "CA": "CA", "IT": "I", "SPN": "E", "ISRA": "IL", "SKOR": "KR", "AUS": "AU",
                    "BRAZ": "BR", "UAE": "AE", "TURK": "TR", "NZ": "NZ", "LUXE": "L", "ARGN": "AR"}
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                        "Oct", "Nov", "Dec"], start=1)}
_GCAT_DATE = re.compile(r"^(\d{4})(?:\s+([A-Z][a-z]{2})(?:\s+(\d{1,2}))?(?:\s+(\d{2})(\d{2})(?::(\d{2}))?)?)?")


def parse_gcat_tsv(text):
    rows, header = [], None
    for line in text.splitlines():
        if line.startswith("# "):
            continue
        if header is None:
            header = line.lstrip("#").split("\t")
            continue
        if line.strip():
            rows.append({k: v.strip() for k, v in zip(header, line.split("\t"))})
    return rows


def gcat_value(v):
    return None if v in (None, "", "-") else v


def parse_gcat_date(text):
    """'1957 Oct  4 1928:34', '2017 May', '1964', '1958 Jan  4?' -> datetime (missing parts = start)."""
    if not text or text == "-":
        return None
    m = _GCAT_DATE.match(re.sub(r"\s+", " ", text.replace("?", "").strip()))
    if not m:
        return None
    year, mon, day, hh, mm, ss = m.groups()
    try:
        return datetime(int(year), _MONTHS.get(mon, 1), int(day or 1), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None


def _float_in(v, lo, hi):
    try:
        x = float(v)
        return x if lo <= x <= hi else None
    except (TypeError, ValueError):
        return None


def _load_sources(run_id):
    text, satcat_dl = sources.fetch(config.CELESTRAK_SATCAT_URL, "celestrak_satcat", "satcat.csv", run_id)
    satcat = list(csv.DictReader(io.StringIO(text)))
    sources.update_download(satcat_dl, records=len(satcat))
    gcat, gcat_dl = {}, {}
    for name, path in GCAT_FILES.items():
        t, dl = sources.fetch(f"{config.GCAT_BASE_URL}/{path}.tsv", f"gcat_{name}", f"gcat_{name}.tsv", run_id)
        gcat[name] = parse_gcat_tsv(t)
        gcat_dl[name] = dl
        sources.update_download(dl, records=len(gcat[name]))
    return satcat, satcat_dl, gcat, gcat_dl


def _store_raw(run_id, source, key_field, records, download_id):
    """Raw catalogue records in MongoDB, versioned: a record is stored again only when it changed."""
    coll = db.mongo_db("jobs").raw_catalog
    latest = {d["_id"]: (d["hash"], d["version"]) for d in coll.aggregate([
        {"$match": {"source": source}}, {"$sort": {"key": 1, "version": -1}},
        {"$group": {"_id": "$key", "hash": {"$first": "$hash"}, "version": {"$first": "$version"}}}],
        allowDiskUse=True)}
    now = datetime.now(timezone.utc)
    batch, stored = [], 0
    for rec in records:
        key = rec.get(key_field)
        if not key:
            continue
        digest = hashlib.sha1(json.dumps(rec, sort_keys=True).encode()).hexdigest()
        prev = latest.get(key)
        if prev and prev[0] == digest:
            continue
        batch.append({"source": source, "key": key, "version": (prev[1] + 1) if prev else 1, "hash": digest,
                      "record": rec, "fetched_at": now, "download_id": download_id, "run_id": run_id})
        if len(batch) >= 5000:
            coll.insert_many(batch, ordered=False)
            stored += len(batch)
            batch = []
    if batch:
        coll.insert_many(batch, ordered=False)
        stored += len(batch)
    return stored


def _transfers():
    path = config.SQL_DIR / "ownership_transfers.csv"
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return sorted(({"from": r["from_org"], "to": r["to_org"], "date": date.fromisoformat(r["transfer_date"])}
                   for r in rows), key=lambda r: r["date"])


def build_rows(satcat, gcat):
    """Map the source files onto the relational schema (pure function, unit-testable)."""
    orgs_src = [o for o in gcat["orgs"] if o["Type"] != "AP"]
    countries = {}
    for o in orgs_src:
        if o["Type"] in ("CY", "CYP"):
            countries[o["Code"]] = (gcat_value(o.get("ShortEName")) or gcat_value(o.get("ShortName"))
                                    or o["Name"])[:120]
    organisations = {}
    for o in orgs_src:
        code = o["Code"]
        if not code or len(code) > 16:
            continue
        name = (gcat_value(o.get("EName")) or gcat_value(o.get("Name")) or code)[:160]
        cc = o.get("StateCode")
        organisations[code] = (code, name, ORG_TYPES.get(o.get("Class"), "Government"),
                               cc if cc in countries else None)

    sites = {}
    for s in gcat["sites"]:
        sid = s["Site"]
        if not sid or len(sid) > 16:
            continue
        name = (gcat_value(s.get("EName")) or gcat_value(s.get("Name")) or sid)[:160]
        cc = s.get("StateCode")
        sites[sid] = (sid, name, cc if cc in countries else None,
                      _float_in(s.get("Latitude"), -90, 90), _float_in(s.get("Longitude"), -180, 180))

    vehicles = {}
    for v in gcat["lv"]:
        name = v["LV_Name"]
        if not name or name in vehicles or len(name) > 64:
            continue
        maker = next((m for m in (v.get("LV_Manufacturer") or "").split("/") if m in organisations), None)
        vehicles[name] = maker

    launches = {}
    for r in gcat["launchlog"]:
        tag = r["Launch_Tag"]
        if tag in launches or not tag or len(tag) > 16:
            continue
        when = parse_gcat_date(r["Launch_Date"])
        if when is None:
            continue
        lv = gcat_value(r.get("LV_Type"))
        if lv and lv not in vehicles and len(lv) <= 64:
            vehicles[lv] = None
        code = (r.get("Launch_Code") or "  ")
        outcome = {"S": "Success", "F": "Failure"}.get(code[1:2], "Unknown")
        launches[tag] = (tag, when, r["Launch_Site"] if r.get("Launch_Site") in sites else None,
                         lv if lv in vehicles else None, outcome)

    gcat_by_norad, jcat_to_norad = {}, {}
    for s in gcat["satcat"]:
        if s["Satcat"].isdigit():  # unassigned pieces carry codes like 'NNA'
            n = int(s["Satcat"])
            gcat_by_norad[n] = s
            jcat_to_norad[s["JCAT"]] = n

    objects, parents, ownership = [], {}, {}
    norads = set()
    for r in satcat:
        n = int(r["NORAD_CAT_ID"])
        norads.add(n)
        g = gcat_by_norad.get(n)
        tag = g["Launch_Tag"] if g and g["Launch_Tag"] in launches else None
        if tag is None:
            m = re.match(r"^(\d{4}-\d{3})", r["OBJECT_ID"] or "")
            tag = m.group(1) if m and m.group(1) in launches else None
        decay = r["DECAY_DATE"] or None
        objects.append((n, r["OBJECT_ID"] or None, (r["OBJECT_NAME"] or f"NORAD {n}")[:100],
                        OBJECT_TYPES.get(r["OBJECT_TYPE"], "Unknown"), tag,
                        "Decayed" if decay else STATUS.get(r["OPS_STATUS_CODE"], "Unknown"), decay,
                        (r.get("ORBIT_CENTER") or None) and r["ORBIT_CENTER"][:8], (r.get("ORBIT_TYPE") or None),
                        (r.get("DATA_STATUS_CODE") or None)))
        if g and gcat_value(g.get("Parent")):
            parents[n] = jcat_to_norad.get(g["Parent"])
        owner = None
        if g:
            owner = next((c for c in (g.get("Owner") or "").split("/") if c in organisations), None)
        if owner is None:
            cc = CELESTRAK_OWNERS.get(r["OWNER"])
            if r["OWNER"] == "CIS":
                cc = "SU" if (r["LAUNCH_DATE"] or "9999") < "1992" else "RU"
            owner = cc if cc in organisations else None
        launch_day = (parse_gcat_date(g["LDate"]).date() if g and parse_gcat_date(g.get("LDate"))
                      else (date.fromisoformat(r["LAUNCH_DATE"]) if r["LAUNCH_DATE"] else None))
        if owner and launch_day:
            ownership[n] = {"org": owner, "from": launch_day, "decay": date.fromisoformat(decay) if decay else None}
    parent_rows = [(n, p) for n, p in parents.items() if p and p != n and p in norads]

    # Ownership history: the GCAT owner from launch, then any curated transfer.
    ownership_rows = []
    for n, o in ownership.items():
        periods = [[o["org"], o["from"], None]]
        for t in _transfers():
            cur = periods[-1]
            if cur[0] == t["from"] and cur[1] < t["date"] and (o["decay"] is None or o["decay"] > t["date"]):
                cur[2] = t["date"]
                periods.append([t["to"], t["date"], None])
        ownership_rows.extend((n, org, start, end) for org, start, end in periods if org in organisations)

    # Missions = GCAT programmes; the owning organisation is the commonest owner of its payloads.
    program_members, program_category = defaultdict(list), {}
    for p in gcat["psatcat"]:
        prog = gcat_value(p.get("Program"))
        n = jcat_to_norad.get(p["JCAT"])
        if prog and n in norads:
            program_members[prog].append(n)
            program_category.setdefault(prog, p.get("Category") or "")
    missions, object_missions = [], []
    for prog, members in program_members.items():
        owners = Counter(ownership[n]["org"] for n in members if n in ownership)
        org = owners.most_common(1)[0][0] if owners else None
        cat = program_category.get(prog, "")
        purpose = PURPOSES.get(cat) or PURPOSES.get(cat.split("/")[0].rstrip("*")) or (cat or "Unspecified")
        missions.append((prog[:120], purpose[:60], org))
        object_missions.extend((n, prog[:120]) for n in set(members))

    return {
        "countries": sorted(countries.items()),
        "organisations": list(organisations.values()),
        "sites": list(sites.values()),
        "vehicles": sorted(vehicles.items()),
        "launches": list(launches.values()),
        "objects": objects,
        "parents": parent_rows,
        "ownership": ownership_rows,
        "missions": missions,
        "object_missions": object_missions,
    }


def _upsert_ownership(cur, ownership_rows):
    """Upsert ownership periods without ever tripping the no-overlap triggers.

    * Objects that already have a multi-period history keep it: that history
      came from a curated transfer or an administrator and is authoritative.
    * If GCAT corrected a launch date, the single existing period is moved to
      the new start date first, so the upsert updates it instead of inserting
      a second, overlapping open period.
    * Periods go in chronological order, so each closes before the next opens.
    """
    cur.execute("SELECT norad_id, from_date FROM object_ownership ORDER BY norad_id, from_date")
    existing = defaultdict(list)
    for n, f in cur.fetchall():
        existing[n].append(f)
    by_object = defaultdict(list)
    for r in ownership_rows:
        by_object[r[0]].append(r)
    rows, moved = [], []
    for n, periods in by_object.items():
        if len(existing.get(n, ())) > 1:
            continue
        periods.sort(key=lambda r: r[2])
        if n in existing and existing[n][0] != periods[0][2]:
            moved.append((periods[0][2], n, existing[n][0]))
        rows.extend(periods)
    if moved:
        cur.executemany("UPDATE object_ownership SET from_date = %s WHERE norad_id = %s AND from_date = %s", moved)
    db.bulk_upsert(cur, "INSERT INTO object_ownership (norad_id, org_id, from_date, to_date) VALUES", rows,
                   "AS n ON DUPLICATE KEY UPDATE org_id = n.org_id, to_date = n.to_date")


def run(ctx):
    satcat, satcat_dl, gcat, gcat_dl = _load_sources(ctx.run_id)
    rows = build_rows(satcat, gcat)

    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        up = db.bulk_upsert
        up(cur, "INSERT INTO country (country_code, name) VALUES", rows["countries"],
           "AS n ON DUPLICATE KEY UPDATE name = n.name")
        up(cur, "INSERT INTO organisation (org_id, name, org_type, country_code) VALUES", rows["organisations"],
           "AS n ON DUPLICATE KEY UPDATE name = n.name, org_type = n.org_type, country_code = n.country_code")
        up(cur, "INSERT INTO launch_site (site_id, name, country_code, latitude, longitude) VALUES", rows["sites"],
           "AS n ON DUPLICATE KEY UPDATE name = n.name, country_code = n.country_code, "
           "latitude = n.latitude, longitude = n.longitude")
        up(cur, "INSERT INTO launch_vehicle (name, org_id) VALUES", rows["vehicles"],
           "AS n ON DUPLICATE KEY UPDATE org_id = n.org_id")
        cur.execute("SELECT vehicle_id, name FROM launch_vehicle")
        vehicle_ids = {name: vid for vid, name in cur.fetchall()}
        up(cur, "INSERT INTO launch (launch_id, launch_date, site_id, vehicle_id, outcome) VALUES",
           [(t, d, s, vehicle_ids.get(v), o) for t, d, s, v, o in rows["launches"]],
           "AS n ON DUPLICATE KEY UPDATE launch_date = n.launch_date, site_id = n.site_id, "
           "vehicle_id = n.vehicle_id, outcome = n.outcome")
        up(cur, "INSERT INTO space_object (norad_id, intl_designator, name, object_type, launch_id, status, "
                "decay_date, orbit_center, orbit_type, data_status) VALUES", rows["objects"],
           "AS n ON DUPLICATE KEY UPDATE intl_designator = n.intl_designator, name = n.name, "
           "object_type = n.object_type, launch_id = n.launch_id, status = n.status, decay_date = n.decay_date, "
           "orbit_center = n.orbit_center, orbit_type = n.orbit_type, data_status = n.data_status")
        # Second pass for the recursive parent link: every object now exists.
        names = {o[0]: (o[2], o[3]) for o in rows["objects"]}
        up(cur, "INSERT INTO space_object (norad_id, name, object_type, parent_norad_id) VALUES",
           [(n, names[n][0], names[n][1], p) for n, p in rows["parents"]],
           "AS n ON DUPLICATE KEY UPDATE parent_norad_id = n.parent_norad_id")
        _upsert_ownership(cur, rows["ownership"])
        up(cur, "INSERT INTO mission (name, purpose, org_id) VALUES", rows["missions"],
           "AS n ON DUPLICATE KEY UPDATE purpose = n.purpose, org_id = n.org_id")
        cur.execute("SELECT mission_id, name FROM mission")
        mission_ids = {name: mid for mid, name in cur.fetchall()}
        up(cur, "INSERT IGNORE INTO object_mission (norad_id, mission_id) VALUES",
           [(n, mission_ids[m]) for n, m in rows["object_missions"] if m in mission_ids])

        # Default watchlist (SRS example): the ISS plus Indian payloads still in orbit.
        cur.execute("SELECT COUNT(*) FROM watchlist")
        seeded = 0
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT IGNORE INTO watchlist (norad_id, reason)
                SELECT so.norad_id,
                       IF(so.norad_id = 25544, 'International Space Station', 'Indian payload')
                  FROM space_object so
                  LEFT JOIN v_current_owner cw ON cw.norad_id = so.norad_id
                 WHERE so.in_earth_orbit
                   AND (so.norad_id = 25544 OR (so.object_type = 'Payload' AND cw.country_code = 'IN'))""")
            seeded = cur.rowcount
        conn.commit()
        cur.close()

    raw = _store_raw(ctx.run_id, "celestrak_satcat", "NORAD_CAT_ID", satcat, satcat_dl)
    raw += _store_raw(ctx.run_id, "gcat_satcat", "JCAT", gcat["satcat"], gcat_dl["satcat"])
    msg = (f"{len(rows['objects'])} objects, {len(rows['launches'])} launches, {len(rows['organisations'])} "
           f"organisations, {len(rows['countries'])} countries, {len(rows['sites'])} sites, "
           f"{len(rows['vehicles'])} vehicles, {len(rows['missions'])} missions, {len(rows['parents'])} parent links, "
           f"{len(rows['ownership'])} ownership periods; {raw} raw records versioned in MongoDB"
           + (f"; watchlist seeded with {seeded} objects" if seeded else ""))
    sources.record_source("celestrak_satcat", "ok", len(satcat), f"{len(satcat)} catalogue records")
    sources.record_source("gcat", "ok", len(gcat["satcat"]), f"{len(rows['launches'])} launches, "
                                                          f"{len(rows['organisations'])} organisations")
    return len(rows["objects"]), msg
