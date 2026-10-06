"""Space-Track.org: the data CelesTrak does not publish (SRS 3.2).

* the GP catalogue: the newest element set of every object on orbit, including
  the debris and rocket bodies outside CelesTrak's groups (merged by ingest);
* element-set history (gp_history): decay-rate history and model training data;
* decay messages: observed re-entry times, the re-entry model's ground truth.

Credentials come from SPACETRACK_USERNAME (or SPACETRACK_USER) and
SPACETRACK_PASSWORD in .env: a free account from space-track.org. Space-Track
allows 30 requests per minute and 300 per hour per account; this client spaces
requests >= 2.5 s apart and counts every request in the MongoDB download log, so
all processes together stay under a 250-per-hour budget. The GP catalogue is
fetched with Space-Track's recommended query, at most once an hour.

  --mode watchlist  history of every watchlist object (default 730 days)
  --mode decaying   recent history of low objects that are coming down (re-entry candidates)
  --mode decayed    the last year before re-entry of recently re-entered objects (training data)
  --mode decay      decay messages: observed re-entry epochs
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from pymongo import UpdateOne

from orbitwatch import config, db, orbital
from orbitwatch.jobs import sources
from orbitwatch.jobs.runner import SkipJob

log = logging.getLogger(__name__)

BATCH = 20               # NORAD ids per gp_history query
MIN_INTERVAL_S = 2.5     # <= 24 requests per minute
HOURLY_BUDGET = 250      # across all processes; Space-Track's cap is 300
GP_MIN_INTERVAL = timedelta(minutes=55)
GP_QUERY = ("/basicspacedata/query/class/gp/decay_date/null-val/epoch/%3Enow-30"
            "/orderby/norad_cat_id/format/json")


def credentials():
    user = os.getenv("SPACETRACK_USERNAME") or os.getenv("SPACETRACK_USER")
    return user, os.getenv("SPACETRACK_PASSWORD")


def configured():
    user, password = credentials()
    return bool(user and password)


def requests_last_hour():
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        return db.mongo_db("jobs").download_log.count_documents(
            {"source": {"$regex": "^spacetrack"}, "started_at": {"$gte": since}, "status": {"$ne": "cache"}})
    except Exception:  # noqa: BLE001 - if the log is unreadable, assume the worst case is not reached
        return 0


class SpaceTrack:
    def __init__(self):
        self.user, self.password = credentials()
        if not (self.user and self.password):
            raise SkipJob("Space-Track credentials are not configured "
                          "(SPACETRACK_USERNAME / SPACETRACK_PASSWORD in .env)")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.HTTP_USER_AGENT
        self.requests, self.last, self.logged_in = 0, 0.0, False

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        self.logout()

    def login(self):
        # A network or TLS hiccup (the site sometimes presents an incomplete certificate chain from one of its
        # edge servers) is retried with back-off; certificate checking itself is never relaxed. A refused
        # login is not retried: repeated bad logins can lock the account.
        resp = None
        for attempt, wait in enumerate((3, 10, 30, None), start=1):
            try:
                resp = self.session.post(f"{config.SPACETRACK_BASE_URL}/ajaxauth/login",
                                         data={"identity": self.user, "password": self.password}, timeout=60)
                break
            except requests.RequestException as exc:
                if wait is None:
                    raise
                log.warning("Space-Track login attempt %d failed (%s); retrying in %d s", attempt, exc, wait)
                time.sleep(wait)
        if resp.status_code != 200 or "Failed" in resp.text:
            raise RuntimeError("Space-Track login failed: check SPACETRACK_USERNAME / SPACETRACK_PASSWORD")
        self.logged_in = True

    def logout(self):
        if self.logged_in:
            try:
                self.session.get(f"{config.SPACETRACK_BASE_URL}/ajaxauth/logout", timeout=30)
            except requests.RequestException:
                pass
            self.logged_in = False

    def get(self, path, source, cache_name, run_id, timeout=300):
        used = requests_last_hour()
        if used >= HOURLY_BUDGET:
            raise RuntimeError(f"Space-Track hourly request budget used up ({used} in the last hour); try later")
        wait = MIN_INTERVAL_S - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()
        self.requests += 1
        text, dl = sources.fetch(f"{config.SPACETRACK_BASE_URL}{path}", source, cache_name, run_id,
                                 session=self.session, attempts=2, timeout=timeout)
        data = json.loads(text)
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(f"Space-Track error: {data['error']}")
        sources.update_download(dl, records=len(data))
        return data, dl

    def gp_catalogue(self, run_id):
        return self.get(GP_QUERY, "spacetrack_gp", "spacetrack_gp.json", run_id, timeout=600)

    def gp_history(self, norad_ids, start, end, run_id):
        path = (f"/basicspacedata/query/class/gp_history/NORAD_CAT_ID/{','.join(str(n) for n in norad_ids)}"
                f"/EPOCH/{start:%Y-%m-%d}--{end:%Y-%m-%d}/orderby/NORAD_CAT_ID,EPOCH/format/json")
        return self.get(path, "spacetrack_gp_history", "spacetrack_history_last.json", run_id)

    def decay_messages(self, days, run_id):
        path = (f"/basicspacedata/query/class/decay/DECAY_EPOCH/%3Enow-{int(days)}/MSG_TYPE/Historical"
                f"/orderby/DECAY_EPOCH%20desc/format/json")
        return self.get(path, "spacetrack_decay", "spacetrack_decay.json", run_id)


def gp_due():
    """Space-Track asks for the GP catalogue at most once an hour."""
    row = db.query_one("jobs", "SELECT last_success_at FROM data_source WHERE source_key = 'spacetrack_gp'")
    return not (row and row["last_success_at"]
                and datetime.now(timezone.utc).replace(tzinfo=None) - row["last_success_at"] < GP_MIN_INTERVAL)


def fetch_catalogue(run_id):
    """Element sets of the whole GP catalogue (used by the ingest job). Returns (element_sets, download_id)."""
    with SpaceTrack() as st:
        data, dl = st.gp_catalogue(run_id)
    fetched = datetime.now(timezone.utc).replace(tzinfo=None)
    sets = []
    for rec in data:
        try:
            el = orbital.parse_omm(rec)
        except (KeyError, ValueError):
            continue
        el.update(raw=rec, download_id=dl, source="Space-Track", fetched_at=fetched)
        sets.append(el)
    return sets, dl


# --------------------------------------------------------------------------- history / decay import
def _plan(mode, days, limit):
    today = orbital.now_utc()
    if mode == "watchlist":
        ids = [r["norad_id"] for r in db.query("jobs", "SELECT norad_id FROM watchlist ORDER BY norad_id")]
        return [(ids[i:i + BATCH], today - timedelta(days=days), today) for i in range(0, len(ids), BATCH)]
    if mode == "decaying":
        rows = db.query("jobs", """
            SELECT co.norad_id FROM current_orbit co JOIN space_object so USING (norad_id)
             WHERE so.in_earth_orbit AND (co.perigee_km < 300 OR (co.mean_altitude_km < 450 AND co.mean_motion_dot > 0))
             ORDER BY co.perigee_km LIMIT %s""", (limit,))
        ids = [r["norad_id"] for r in rows]
        return [(ids[i:i + BATCH], today - timedelta(days=min(days, 120)), today) for i in range(0, len(ids), BATCH)]
    # decayed: the year before re-entry of objects that came down in the last `days` days
    rows = db.query("jobs", """
        SELECT so.norad_id, COALESCE(DATE(ro.decay_epoch), so.decay_date) AS decay_date
          FROM space_object so
          LEFT JOIN reentry_observation ro ON ro.norad_id = so.norad_id AND ro.source = 'Space-Track decay'
         WHERE COALESCE(DATE(ro.decay_epoch), so.decay_date) >= UTC_DATE() - INTERVAL %s DAY
         ORDER BY so.object_type = 'Debris', decay_date DESC""", (days,))
    done = set(db.mongo_db("jobs").import_state.distinct("norad_id", {"mode": "decayed"}))
    rows = [r for r in rows if r["norad_id"] not in done][:limit]
    plan = []
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        end = max(r["decay_date"] for r in part)
        start = min(r["decay_date"] for r in part) - timedelta(days=365)
        plan.append(([r["norad_id"] for r in part], start, end))
    return plan


def _import_decay(ctx, days):
    with SpaceTrack() as st:
        data, _ = st.decay_messages(days, ctx.run_id)
    known = {r["norad_id"]: r["decay_date"] for r in db.query("jobs", "SELECT norad_id, decay_date FROM space_object")}
    rows, newly_decayed = [], []
    for rec in data:
        try:
            n = int(rec["NORAD_CAT_ID"])
            decay = datetime.fromisoformat(str(rec["DECAY_EPOCH"]).replace(" ", "T")[:19])
        except (KeyError, ValueError, TypeError):
            continue
        if n not in known:
            continue
        msg = rec.get("MSG_EPOCH")
        rows.append((n, "Space-Track decay", decay, datetime.fromisoformat(str(msg).replace(" ", "T")[:19]) if msg else None))
        if known[n] is None:
            newly_decayed.append((decay.date(), n))
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        db.bulk_upsert(cur, "INSERT INTO reentry_observation (norad_id, source, decay_epoch, msg_epoch) VALUES", rows,
                       "AS n ON DUPLICATE KEY UPDATE decay_epoch = n.decay_epoch, msg_epoch = n.msg_epoch, "
                       "fetched_at = CURRENT_TIMESTAMP")
        # an observed re-entry the weekly SATCAT refresh has not caught up with yet
        for d, n in newly_decayed:
            cur.execute("UPDATE space_object SET decay_date = %s WHERE norad_id = %s AND decay_date IS NULL", (d, n))
        conn.commit()
    sources.record_source("spacetrack_decay", "ok", len(rows), f"{len(rows)} observed re-entries in {days} days")
    return len(rows), (f"decay: {len(rows)} observed re-entries in the last {days} days; "
                       f"{len(newly_decayed)} objects newly marked as re-entered")


def run(ctx, mode="watchlist", days=730, limit=200):
    from orbitwatch.jobs.ingest import store_history, upsert_current_orbits
    if not configured():
        for key in ("spacetrack_gp_history", "spacetrack_decay"):
            sources.record_source(key, "skipped", message="credentials not configured")
        raise SkipJob("Space-Track credentials are not configured (SPACETRACK_USERNAME / SPACETRACK_PASSWORD in .env)")
    if mode == "decay":
        return _import_decay(ctx, days)
    plan = _plan(mode, days, limit)
    if not plan:
        raise SkipJob("nothing to import")
    total = inserted = duplicates = 0
    latest = {}
    with SpaceTrack() as client:
        try:
            for ids, start, end in plan:
                data, dl = client.gp_history(ids, start, end, ctx.run_id)
                sets = []
                fetched = datetime.now(timezone.utc).replace(tzinfo=None)
                for rec in data:
                    try:
                        el = orbital.parse_omm(rec)
                    except (KeyError, ValueError):
                        continue
                    el.update(raw=rec, download_id=dl, source="Space-Track", fetched_at=fetched)
                    sets.append(el)
                    prev = latest.get(el["norad_id"])
                    if prev is None or el["epoch"] > prev["epoch"]:
                        latest[el["norad_id"]] = el
                ins, dup = store_history(sets, "Space-Track", ctx.run_id)
                total, inserted, duplicates = total + len(sets), inserted + ins, duplicates + dup
                log.info("Space-Track batch of %s objects: %s element sets (%s new)", len(ids), len(sets), ins)
        except RuntimeError as exc:
            if not total:
                sources.record_source("spacetrack_gp_history", "error", message=str(exc))
                raise
            log.warning("stopping early: %s", exc)   # keep what was imported; the next run continues

    newer = 0
    if mode in ("watchlist", "decaying") and latest:
        with db.mysql_conn("jobs") as conn:
            cur = conn.cursor()
            cur.execute("SELECT norad_id FROM space_object WHERE in_earth_orbit")
            live = {r[0] for r in cur.fetchall()}
            _, newer, _ = upsert_current_orbits(cur, [el for el in latest.values() if el["norad_id"] in live],
                                                "Space-Track")
            conn.commit()
    if mode == "decayed":
        # remember which re-entered objects were imported, so later runs move on to others
        imported = {n for ids, _, _ in plan for n in ids}
        if imported:
            now = datetime.now(timezone.utc)
            db.mongo_db("jobs").import_state.bulk_write(
                [UpdateOne({"norad_id": n, "mode": "decayed"},
                           {"$set": {"imported_at": now, "run_id": ctx.run_id}}, upsert=True)
                 for n in imported])
    sources.record_source("spacetrack_gp_history", "ok", inserted,
                          f"{mode}: {inserted} new element sets for {len(latest)} objects")
    return inserted, (f"{mode}: {total} element sets for {len(latest)} objects in {client.requests} requests; "
                      f"history {inserted} new, {duplicates} already stored; {newer} current orbits updated")
