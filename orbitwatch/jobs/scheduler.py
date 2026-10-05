"""Periodic jobs (APScheduler), run as their own process: `ow scheduler`.

Running separately from the web server means ingestion and screening never
block user queries (SRS 4.1); MySQL's MVCC lets readers keep reading the
previous snapshot while a job's transaction is open. Intervals and cron
expressions are read from system_config and re-read every 10 minutes, so
an administrator's change takes effect without a restart.
"""
import logging
import signal
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from orbitwatch import db
from orbitwatch.jobs import aggregate, backup, catalog, ingest, reentry, screening, spaceweather
from orbitwatch.jobs.runner import run_job

log = logging.getLogger(__name__)


def ingest_and_screen():
    run_job("space_weather", spaceweather.run, retries=1)
    result = run_job("ingest", ingest.run)
    if result.get("status") == "success":
        run_job("screening", screening.run)


def reentry_refresh():
    run_job("reentry_train", reentry.train)
    run_job("reentry_predict", reentry.predict)


def _triggers(cfg):
    return {
        "ingest_and_screen": IntervalTrigger(hours=float(cfg.get("ingest_interval_hours", 4)), timezone="UTC"),
        "catalog": CronTrigger.from_crontab(cfg.get("catalog_refresh_cron", "0 4 * * sun"), timezone="UTC"),
        "aggregation": CronTrigger.from_crontab(cfg.get("aggregation_cron", "30 1 * * *"), timezone="UTC"),
        "reentry": CronTrigger.from_crontab(cfg.get("reentry_cron", "0 2 * * *"), timezone="UTC"),
        "backup": CronTrigger.from_crontab(cfg.get("backup_cron", "0 3 * * *"), timezone="UTC"),
    }


JOBS = {
    "ingest_and_screen": ingest_and_screen,
    "catalog": lambda: run_job("catalog", catalog.run),
    "aggregation": lambda: run_job("aggregation", aggregate.run),
    "reentry": reentry_refresh,
    "backup": lambda: run_job("backup", backup.run),
}


def run_scheduler():
    sched = BlockingScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1,
                                                            "misfire_grace_time": 3600})
    state = {"cfg": None}

    def apply_config():
        cfg = db.get_config()
        keys = ("ingest_interval_hours", "catalog_refresh_cron", "aggregation_cron", "reentry_cron", "backup_cron")
        snapshot = {k: cfg.get(k) for k in keys}
        if snapshot == state["cfg"]:
            return
        for job_id, trigger in _triggers(cfg).items():
            if sched.get_job(job_id):
                sched.reschedule_job(job_id, trigger=trigger)
            else:
                # The first ingest + screening runs a minute after start-up, then on the interval.
                first = datetime.now(timezone.utc) + timedelta(minutes=1) if job_id == "ingest_and_screen" else None
                sched.add_job(JOBS[job_id], trigger, id=job_id, name=job_id,
                              **({"next_run_time": first} if first else {}))
        state["cfg"] = snapshot
        log.info("schedule (re)applied: %s", snapshot)

    apply_config()
    sched.add_job(apply_config, IntervalTrigger(minutes=10), id="config_watch", name="config_watch")
    signal.signal(signal.SIGINT, lambda *_: sched.shutdown(wait=False))
    for job in sched.get_jobs():
        log.info("scheduled %s: %s", job.id, job.trigger)
    sched.start()
