"""Downloads with retries; every attempt is logged to MongoDB download_log (SRS 3.2, 4.3)."""
import logging
import time
import uuid
from datetime import datetime, timezone

import requests

from orbitwatch import config, db

log = logging.getLogger(__name__)


def _download_log():
    return db.mongo_db("jobs").download_log


def log_download(entry):
    try:
        _download_log().insert_one(dict(entry))
    except Exception as exc:  # logging must never break the job itself
        log.warning("could not write download_log: %s", exc)


def update_download(download_id, **fields):
    """Attach results (record counts etc.) to the successful attempt of a download."""
    try:
        _download_log().update_one({"download_id": download_id, "status": {"$in": ["ok", "cache"]}},
                                   {"$set": fields})
    except Exception as exc:
        log.warning("could not update download_log: %s", exc)


def record_source(source_key, status, records=None, message=None):
    """Freshness bookkeeping for one data source (MySQL data_source), shown next to its data."""
    try:
        db.execute("jobs", """
            UPDATE data_source
               SET last_attempt_at = NOW(3),
                   last_success_at = IF(%s = 'ok', NOW(3), last_success_at),
                   last_status = %s, last_records = COALESCE(%s, last_records), last_message = %s
             WHERE source_key = %s""", (status, status, records, (message or "")[:500] or None, source_key))
    except Exception as exc:  # bookkeeping must never break the job itself
        log.warning("could not update data_source %s: %s", source_key, exc)


def fetch(url, source, cache_name, run_id=None, timeout=180, attempts=3, session=None, params=None):
    """GET url (retried with backoff) and keep a copy in the runtime cache.

    Returns (text, download_id). With USE_CACHE the cached copy is used and
    logged as such, so development runs do not hammer the providers.
    """
    cache_path = config.CACHE_DIR / cache_name
    download_id = str(uuid.uuid4())
    if config.USE_CACHE and cache_path.exists():
        text = cache_path.read_text(encoding="utf-8")
        now = datetime.now(timezone.utc)
        log_download({"download_id": download_id, "source": source, "url": url, "run_id": run_id,
                      "started_at": now, "finished_at": now, "status": "cache", "http_status": None,
                      "bytes": len(text), "attempt": 1, "error": None})
        return text, download_id

    http = session or requests.Session()
    last_error = None
    for attempt in range(1, attempts + 1):
        started = datetime.now(timezone.utc)
        entry = {"download_id": download_id, "source": source, "url": url, "run_id": run_id,
                 "started_at": started, "attempt": attempt}
        try:
            resp = http.get(url, params=params, timeout=timeout, headers={"User-Agent": config.HTTP_USER_AGENT})
            entry.update(http_status=resp.status_code, bytes=len(resp.content),
                         finished_at=datetime.now(timezone.utc))
            if resp.status_code == 200 and resp.content.strip():
                text = resp.text
                cache_path.write_text(text, encoding="utf-8")
                entry.update(status="ok", error=None)
                log_download(entry)
                return text, download_id
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            entry.update(finished_at=datetime.now(timezone.utc), http_status=None, bytes=0)
        entry.update(status="error", error=last_error)
        log_download(entry)
        log.warning("download %s failed (attempt %s/%s): %s", url, attempt, attempts, last_error)
        if attempt < attempts:
            time.sleep(5 * attempt)  # all attempts share one download_id
    raise RuntimeError(f"download failed after {attempts} attempts: {url}: {last_error}")
