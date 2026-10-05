"""Run a background job with status tracking, retries and mutual exclusion.

Every attempt is a row in MySQL job_run (what the admin "Jobs" page shows).
A MySQL named lock (GET_LOCK) guarantees one instance of a job at a time
across processes, e.g. the scheduler and a manual "Run now" from the admin
page. Failures are logged and retried with backoff (SRS 4.3).
"""
import logging
import time
import traceback

import mysql.connector

from orbitwatch import db

log = logging.getLogger(__name__)

RETRY_DELAYS = (30, 120)  # seconds before the 2nd and 3rd attempt


class SkipJob(Exception):
    """Raised by a job that has nothing to do (recorded as 'skipped', not retried)."""


class JobContext:
    def __init__(self, run_id, name):
        self.run_id = run_id
        self.name = name


def _start(name, triggered_by, attempt):
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO job_run (job_name, triggered_by, attempt, status) VALUES (%s, %s, %s, 'running')",
                    (name, triggered_by, attempt))
        conn.commit()
        return cur.lastrowid


def _finish(run_id, status, records=None, message=None):
    db.execute("jobs", "UPDATE job_run SET status = %s, finished_at = CURRENT_TIMESTAMP(3), "
                       "records_processed = %s, message = %s WHERE run_id = %s",
               (status, records, (message or "")[:60000] or None, run_id))


def run_job(name, fn, triggered_by="schedule", retries=len(RETRY_DELAYS), retry_delays=RETRY_DELAYS, **kwargs):
    """Run fn(ctx, **kwargs) -> (records, message). Returns a summary dict."""
    lock_conn = None
    try:
        lock_conn = mysql.connector.connect(**db._mysql_params("jobs"))
        cur = lock_conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (f"orbitwatch_job_{name}",))
        if cur.fetchone()[0] != 1:
            log.warning("job %s is already running; not starting another", name)
            return {"job": name, "status": "skipped", "message": "already running"}

        for attempt in range(1, retries + 2):
            run_id = _start(name, triggered_by, attempt)
            t0 = time.time()
            log.info("job %s started (run %s, attempt %s)", name, run_id, attempt)
            try:
                records, message = fn(JobContext(run_id, name), **kwargs)
                _finish(run_id, "success", records, message)
                log.info("job %s succeeded in %.1fs: %s", name, time.time() - t0, message)
                return {"job": name, "run_id": run_id, "status": "success", "records": records,
                        "message": message, "seconds": round(time.time() - t0, 1)}
            except SkipJob as exc:
                _finish(run_id, "skipped", 0, str(exc))
                log.info("job %s skipped: %s", name, exc)
                return {"job": name, "run_id": run_id, "status": "skipped", "message": str(exc)}
            except Exception as exc:  # noqa: BLE001 - every failure is recorded and retried
                detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=8)}"
                _finish(run_id, "failed", None, detail)
                log.error("job %s failed (attempt %s): %s", name, attempt, exc)
                if attempt > retries:
                    return {"job": name, "run_id": run_id, "status": "failed", "message": str(exc)}
                time.sleep(retry_delays[min(attempt - 1, len(retry_delays) - 1)])
    finally:
        if lock_conn is not None:
            try:
                lock_conn.cursor().execute("DO RELEASE_LOCK(%s)", (f"orbitwatch_job_{name}",))
            finally:
                lock_conn.close()


def is_running(name):
    row = db.query_one("jobs", "SELECT IS_USED_LOCK(%s) AS holder", (f"orbitwatch_job_{name}",))
    return bool(row and row["holder"])
