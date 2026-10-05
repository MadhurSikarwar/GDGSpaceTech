"""Periodic jobs (APScheduler), run as their own process: `ow scheduler` (or `ow service`).

Running separately from the web server means ingestion and screening never
block user queries (SRS 4.1); MySQL's MVCC lets readers keep reading the
previous snapshot while a job's transaction is open. Intervals and cron
expressions are read from system_config and re-read every 10 minutes, so
an administrator's change takes effect without a restart.

Production behaviour:
* one scheduler at a time: a MySQL named lock is held for the process lifetime;
* every job runs through runner.run_job: GET_LOCK per job (no overlap with a
  manual "Run now"), retries with back-off, every attempt recorded in job_run;
* jobs are idempotent (unique history index, upserts, sp_record_conjunction,
  outbox dedupe keys), so a retried or repeated run never duplicates data;
* status for the admin page and dashboard: scheduler_job (next run, last
  start/finish/status/success) and scheduler_heartbeat (every 30 s).
"""
import logging
import os
import signal
import socket
import threading
from datetime import datetime, timedelta, timezone

import mysql.connector
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from orbitwatch import config, db, events, notify
from orbitwatch.jobs import aggregate, backup, catalog, ingest, reentry, screening, spacetrack, spaceweather
from orbitwatch.jobs.runner import run_job

log = logging.getLogger(__name__)
SCHEDULER_LOCK = "orbitwatch_scheduler"
_results = threading.local()   # the run_job results of the scheduled job running on this thread


def _run(name, fn, retries=None, **kwargs):
    result = run_job(name, fn, **({"retries": retries} if retries is not None else {}), **kwargs)
    getattr(_results, "items", []).append(result)
    return result


def _collect(fn):
    """Wrap a scheduled callable so it returns the results of every job it ran (APScheduler's retval)."""
    def wrapper():
        _results.items = []
        try:
            fn()
            return list(_results.items)
        finally:
            _results.items = []
    wrapper.__name__ = getattr(fn, "__name__", "job")
    return wrapper


def ingest_and_screen():
    _run("space_weather", spaceweather.run, retries=1)
    result = _run("ingest", ingest.run)
    if result.get("status") == "success":
        events.record("data", "ingest", f"Orbital data refreshed: {result.get('records')} objects",
                      entity_type="job_run", entity_id=result.get("run_id"))
        screened = _run("screening", screening.run)
        if screened.get("status") == "success":
            events.record("screening", "screening", f"Close-approach screening finished: {screened.get('message', '')[:300]}",
                          entity_type="job_run", entity_id=screened.get("run_id"))
            _run("notify", notify.queue_all, retries=1)
    elif result.get("status") == "failed":
        events.record("data", "ingest_failed", f"Orbital data refresh failed: {result.get('message', '')[:300]}",
                      severity="warning", visibility="analyst")


def reentry_train():
    r = _run("reentry_train", reentry.train, retries=0)
    if r.get("status") == "success":
        events.record("data", "reentry_model", r.get("message", "")[:500], visibility="analyst")


def reentry_predict():
    _run("reentry_predict", reentry.predict, retries=1)


def email_tick():
    """Every minute: only open a job run when there is mail due (keeps job_run free of empty ticks)."""
    if notify.due_count() and config.smtp_configured():
        _run("email_dispatch", notify.dispatch, retries=0)


def _anything_to_notify():
    row = db.query_one("jobs", """SELECT (SELECT COUNT(*) FROM alert WHERE sent_on > NOW() - INTERVAL 10 MINUTE)
                                        + (SELECT COUNT(*) FROM maneuver_decision
                                            WHERE decided_at > NOW() - INTERVAL 10 MINUTE) AS n""")
    return row["n"] > 0


def spacetrack_daily():
    _run("spacetrack_decay", spacetrack.run, retries=1, mode="decay", days=60)
    _run("spacetrack_decaying", spacetrack.run, retries=1, mode="decaying", days=90, limit=200)


def spacetrack_weekly():
    _run("spacetrack_watchlist", spacetrack.run, retries=1, mode="watchlist", days=730)
    _run("spacetrack_decayed", spacetrack.run, retries=1, mode="decayed", days=1095, limit=400)


# job id -> (callable, description, default trigger factory(cfg))
JOBS = {
    "ingest_and_screen": (ingest_and_screen, "Space weather, element sets (CelesTrak + Space-Track), screening, alert e-mails",
                          lambda c: IntervalTrigger(hours=float(c.get("ingest_interval_hours", 4)), timezone="UTC")),
    "space_weather": (lambda: _run("space_weather", spaceweather.run, retries=1), "NOAA Kp / F10.7",
                      lambda c: IntervalTrigger(hours=3, timezone="UTC")),
    "catalog": (lambda: _run("catalog", catalog.run), "SATCAT + GCAT reference data",
                lambda c: CronTrigger.from_crontab(c.get("catalog_refresh_cron", "0 4 * * sun"), timezone="UTC")),
    "spacetrack_daily": (spacetrack_daily, "Space-Track decay messages and history of decaying objects",
                         lambda c: CronTrigger.from_crontab(c.get("spacetrack_daily_cron", "15 0 * * *"), timezone="UTC")),
    "spacetrack_weekly": (spacetrack_weekly, "Space-Track history: watchlist and re-entered objects (training data)",
                          lambda c: CronTrigger.from_crontab(c.get("spacetrack_weekly_cron", "0 5 * * sat"), timezone="UTC")),
    "aggregation": (lambda: _run("aggregation", aggregate.run), "MapReduce / aggregation summaries",
                    lambda c: CronTrigger.from_crontab(c.get("aggregation_cron", "30 1 * * *"), timezone="UTC")),
    "reentry_train": (reentry_train, "Re-entry model training and evaluation",
                      lambda c: CronTrigger.from_crontab(c.get("reentry_train_cron", "0 2 * * sun"), timezone="UTC")),
    "reentry_predict": (reentry_predict, "Re-entry predictions for decaying objects",
                        lambda c: CronTrigger.from_crontab(c.get("reentry_cron", "0 2 * * *"), timezone="UTC")),
    "backup": (lambda: _run("backup", backup.run), "MySQL + MongoDB backup",
               lambda c: CronTrigger.from_crontab(c.get("backup_cron", "0 3 * * *"), timezone="UTC")),
    "email": (email_tick, "Send queued e-mails (with retries)",
              lambda c: IntervalTrigger(minutes=1, timezone="UTC")),
    "notify": (lambda: _run("notify", notify.queue_all, retries=0) if _anything_to_notify() else None,
               "Queue alert and decision e-mails", lambda c: IntervalTrigger(minutes=5, timezone="UTC")),
    "housekeeping": (lambda: _run("housekeeping", notify.housekeeping, retries=0),
                     "Prune rate limits, spent tokens, stale runs, cleared demos",
                     lambda c: IntervalTrigger(hours=1, timezone="UTC")),
}
CONFIG_KEYS = ("ingest_interval_hours", "catalog_refresh_cron", "aggregation_cron", "reentry_cron", "backup_cron",
               "reentry_train_cron", "spacetrack_daily_cron", "spacetrack_weekly_cron")


def _status(job_id, **fields):
    cols = ", ".join(f"{k} = %s" for k in fields)
    try:
        db.execute("jobs", f"UPDATE scheduler_job SET {cols} WHERE job_id = %s", (*fields.values(), job_id))
    except Exception as exc:  # noqa: BLE001 - status bookkeeping never stops the scheduler
        log.warning("scheduler_job update failed: %s", exc)


def _register(sched):
    rows = []
    for job in sched.get_jobs():
        if job.id in JOBS:
            nrt = job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None) if job.next_run_time else None
            rows.append((job.id, JOBS[job.id][1], str(job.trigger)[:120], nrt))
    with db.mysql_conn("jobs") as conn:
        cur = conn.cursor()
        db.bulk_upsert(cur, "INSERT INTO scheduler_job (job_id, description, trigger_desc, next_run_at) VALUES", rows,
                       "AS n ON DUPLICATE KEY UPDATE description = n.description, trigger_desc = n.trigger_desc, "
                       "next_run_at = n.next_run_at")
        conn.commit()


def _listener(sched):
    def on_event(ev):
        if ev.job_id not in JOBS:
            return
        job = sched.get_job(ev.job_id)
        nrt = job.next_run_time.astimezone(timezone.utc).replace(tzinfo=None) if job and job.next_run_time else None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if ev.code == EVENT_JOB_SUBMITTED:
            _status(ev.job_id, last_started_at=now, last_status="running", next_run_at=nrt)
        elif ev.code == EVENT_JOB_MISSED:
            _status(ev.job_id, last_message="missed its start time (scheduler was busy or stopped)", next_run_at=nrt)
        else:
            # A chained job is 'failed' if any step failed, 'skipped' if all steps had nothing to do.
            results = ev.retval if ev.code == EVENT_JOB_EXECUTED and isinstance(ev.retval, list) else []
            statuses = {r.get("status") for r in results}
            if ev.code == EVENT_JOB_ERROR or "failed" in statuses:
                status = "failed"
            elif statuses and statuses <= {"skipped"}:
                status = "skipped"
            else:
                status = "success"
            msg = "; ".join(f"{r.get('job')}: {r.get('status')}" + (f" ({str(r.get('message'))[:80]})"
                                                                      if r.get("status") != "success" else "")
                            for r in results)[:500] or (str(ev.exception)[:500] if ev.exception else None)
            fields = {"last_finished_at": now, "last_status": status, "last_message": msg, "next_run_at": nrt}
            if status == "success":
                fields["last_success_at"] = now
            _status(ev.job_id, **fields)
    return on_event


def _heartbeat(started):
    try:
        db.execute("jobs", """INSERT INTO scheduler_heartbeat (id, host, pid, started_at, heartbeat_at)
                              VALUES (1, %s, %s, %s, NOW(3))
                              AS n ON DUPLICATE KEY UPDATE host = n.host, pid = n.pid, started_at = n.started_at,
                                                           heartbeat_at = n.heartbeat_at""",
                   (socket.gethostname()[:100], os.getpid(), started))
    except Exception as exc:  # noqa: BLE001
        log.warning("heartbeat failed: %s", exc)


def run_scheduler(first_ingest_delay_min=1):
    # One scheduler process at a time (a second one exits instead of running every job twice).
    lock = mysql.connector.connect(**db._mysql_params("jobs"))
    cur = lock.cursor()
    cur.execute("SELECT GET_LOCK(%s, 0)", (SCHEDULER_LOCK,))
    if cur.fetchone()[0] != 1:
        raise SystemExit("another OrbitWatch scheduler is already running")

    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                            "misfire_grace_time": 3600})
    state = {"cfg": None}
    started = datetime.now(timezone.utc).replace(tzinfo=None)

    def apply_config():
        cfg = db.get_config()
        snapshot = {k: cfg.get(k) for k in CONFIG_KEYS}
        if snapshot == state["cfg"]:
            return
        for job_id, (fn, _, trigger_for) in JOBS.items():
            trigger = trigger_for(cfg)
            if sched.get_job(job_id):
                sched.reschedule_job(job_id, trigger=trigger)
            else:
                # The first ingest + screening runs shortly after start-up, then on the interval.
                first = (datetime.now(timezone.utc) + timedelta(minutes=first_ingest_delay_min)
                         if job_id == "ingest_and_screen" else None)
                sched.add_job(_collect(fn), trigger, id=job_id, name=job_id,
                              **({"next_run_time": first} if first else {}))
        state["cfg"] = snapshot
        log.info("schedule (re)applied: %s", snapshot)

    sched.add_listener(_listener(sched), EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    apply_config()
    sched.add_job(apply_config, IntervalTrigger(minutes=10), id="config_watch", name="config_watch")
    sched.add_job(lambda: (_heartbeat(started), _register(sched)), IntervalTrigger(seconds=30), id="heartbeat",
                  name="heartbeat", next_run_time=datetime.now(timezone.utc))
    signal.signal(signal.SIGINT, lambda *_: sched.shutdown(wait=False))
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: sched.shutdown(wait=False))
    for job in sched.get_jobs():
        log.info("scheduled %s: %s", job.id, job.trigger)
    events.record("system", "scheduler_start", "Scheduler started", visibility="admin",
                  detail={"pid": os.getpid(), "host": socket.gethostname()})
    try:
        sched.start()
    finally:
        try:
            cur.execute("DO RELEASE_LOCK(%s)", (SCHEDULER_LOCK,))
        finally:
            lock.close()


def status():
    """What the admin page and dashboard show: heartbeat + per-job schedule and last results."""
    hb = db.query_one("viewer", "SELECT host, pid, started_at, heartbeat_at, "
                                "TIMESTAMPDIFF(SECOND, heartbeat_at, NOW(3)) AS age_s FROM scheduler_heartbeat")
    jobs = db.query("viewer", "SELECT job_id, description, trigger_desc, next_run_at, last_started_at, last_finished_at, "
                              "last_status, last_success_at, last_message FROM scheduler_job ORDER BY job_id")
    running = bool(hb and hb["age_s"] is not None and hb["age_s"] < 120)
    return {"running": running, "heartbeat": hb, "jobs": jobs}
