"""Space-Track.org history import (SRS 3.2: older history for a useful record from the start).

Credentials come from SPACETRACK_USER / SPACETRACK_PASSWORD in .env (a free
account from space-track.org). Space-Track allows 30 requests per minute and
300 per hour; this client stays well below both.

  --mode watchlist  element-set history of every watchlist object
  --mode decayed    the last year before re-entry of recently re-entered
                    objects: training data for the re-entry model
"""
import json
import logging
import os
import time
from datetime import timedelta

import requests

from orbitwatch import config, db, orbital
from orbitwatch.jobs import sources
from orbitwatch.jobs.ingest import store_history, upsert_current_orbits
from orbitwatch.jobs.runner import SkipJob

log = logging.getLogger(__name__)

BATCH = 20               # NORAD ids per query
MIN_INTERVAL_S = 2.5     # <= 24 requests per minute
MAX_PER_RUN = 250        # stay under the 300-per-hour cap


class SpaceTrack:
    def __init__(self):
        self.user, self.password = os.getenv("SPACETRACK_USER"), os.getenv("SPACETRACK_PASSWORD")
        if not self.user or not self.password:
            raise SkipJob("Space-Track credentials are not configured (SPACETRACK_USER / SPACETRACK_PASSWORD in .env)")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.HTTP_USER_AGENT
        self.requests, self.last = 0, 0.0

    def login(self):
        resp = self.session.post(f"{config.SPACETRACK_BASE_URL}/ajaxauth/login",
                                 data={"identity": self.user, "password": self.password}, timeout=60)
        if resp.status_code != 200 or "Failed" in resp.text:
            raise RuntimeError("Space-Track login failed (check SPACETRACK_USER / SPACETRACK_PASSWORD)")

    def gp_history(self, norad_ids, start, end, run_id):
        if self.requests >= MAX_PER_RUN:
            raise RuntimeError("request budget for this run exhausted")
        wait = MIN_INTERVAL_S - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        url = (f"{config.SPACETRACK_BASE_URL}/basicspacedata/query/class/gp_history/NORAD_CAT_ID/"
               f"{','.join(str(n) for n in norad_ids)}/EPOCH/{start:%Y-%m-%d}--{end:%Y-%m-%d}/"
               f"orderby/NORAD_CAT_ID,EPOCH/format/json")
        self.last = time.time()
        self.requests += 1
        text, dl = sources.fetch(url, "spacetrack_gp_history", "spacetrack_last.json", run_id,
                                 session=self.session, attempts=2)
        data = json.loads(text)
        sources.update_download(dl, records=len(data))
        return data, dl

    def logout(self):
        try:
            self.session.get(f"{config.SPACETRACK_BASE_URL}/ajaxauth/logout", timeout=30)
        except requests.RequestException:
            pass


def _plan(mode, days, limit):
    today = orbital.now_utc()
    if mode == "watchlist":
        ids = [r["norad_id"] for r in db.query("jobs", "SELECT norad_id FROM watchlist ORDER BY norad_id")]
        return [(ids[i:i + BATCH], today - timedelta(days=days), today) for i in range(0, len(ids), BATCH)]
    rows = db.query("jobs", """
        SELECT norad_id, decay_date FROM space_object
         WHERE decay_date >= UTC_DATE() - INTERVAL %s DAY
         ORDER BY object_type = 'Debris', decay_date DESC LIMIT %s""", (days, limit))
    plan = []
    for i in range(0, len(rows), BATCH):
        part = rows[i:i + BATCH]
        end = max(r["decay_date"] for r in part)
        start = min(r["decay_date"] for r in part) - timedelta(days=365)
        plan.append(([r["norad_id"] for r in part], start, end))
    return plan


def run(ctx, mode="watchlist", days=730, limit=200):
    client = SpaceTrack()
    plan = _plan(mode, days, limit)
    if not plan:
        raise SkipJob("nothing to import")
    client.login()
    total = inserted = duplicates = 0
    latest = {}
    try:
        for ids, start, end in plan:
            data, dl = client.gp_history(ids, start, end, ctx.run_id)
            sets = []
            for rec in data:
                try:
                    el = orbital.parse_omm(rec)
                except (KeyError, ValueError):
                    continue
                el["raw"], el["download_id"] = rec, dl
                sets.append(el)
                prev = latest.get(el["norad_id"])
                if prev is None or el["epoch"] > prev["epoch"]:
                    latest[el["norad_id"]] = el
            ins, dup = store_history(sets, "Space-Track", ctx.run_id)
            total, inserted, duplicates = total + len(sets), inserted + ins, duplicates + dup
            log.info("Space-Track batch of %s objects: %s element sets (%s new)", len(ids), len(sets), ins)
    finally:
        client.logout()

    newer = 0
    if mode == "watchlist" and latest:
        with db.mysql_conn("jobs") as conn:
            cur = conn.cursor()
            _, newer, _ = upsert_current_orbits(cur, list(latest.values()), "Space-Track")
            conn.commit()
    return inserted, (f"{mode}: {total} element sets for {len(latest)} objects in {client.requests} requests; "
                      f"history {inserted} new, {duplicates} already stored; {newer} current orbits updated")
