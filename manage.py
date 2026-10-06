"""OrbitWatch command line.

    ow setup [--cached]      first-time setup (certificate, MySQL, MongoDB, catalogue, first ingest, screening)
    ow mysql-setup [--reset] create the database, routines, views, roles and accounts
    ow mongo-init            initialise the sharded MongoDB cluster (first time)
    ow mongo-start | mongo-stop | mongo-status
    ow load-catalog          SATCAT + GCAT reference data (countries ... missions)
    ow ingest                CelesTrak element sets -> MongoDB history + MySQL Current_Orbit
    ow screen                close-approach screening of the watchlist (with probability of collision)
    ow space-weather         NOAA Kp / F10.7 reading (drives the Pc covariance)
    ow assess EVENT_ID       run the AI decision-support agent on one close approach
    ow aggregate             MapReduce / aggregation over the history -> MySQL summaries
    ow reentry-train | reentry-predict
    ow spacetrack-import --mode watchlist|decayed [--days N] [--limit N]
    ow backup | restore-mysql DIR | restore-mongo DIR
    ow reconcile [--apply] [--no-git]
                             compare OrbitWatch with the OrbitalGuard archive (orbitalguard.db: every version in
                             git history, plus a copy in runtime/archive), find test data, duplicates, orphans
                             and conflicts; --apply fixes them
    ow create-admin          create an Administrator account (prompts for the password)
    ow make-cert             self-signed HTTPS certificate for localhost
    ow serve [--http] [--port N]
                             HTTPS (cheroot, TLS) on 8443 with http://:8080 redirecting to it;
                             --http: plain HTTP for development
    ow scheduler             run the periodic jobs (separate process from the web server)
    ow service               web server + scheduler, supervised (restarted if either crashes)
    ow verify restore|failover|https|scheduler|all
                             test the infrastructure for real (isolated restore, MongoDB failover, TLS ...)

Add --cached to setup / load-catalog / ingest to reuse the files in the runtime
cache instead of downloading again.
"""
import argparse
import getpass
import json
import os
import sys

from orbitwatch import config
from orbitwatch.logging_setup import setup_logging


def _job(name, fn, **kwargs):
    from orbitwatch.jobs.runner import run_job
    # Interactive runs report failures at once; scheduled runs retry (jobs/scheduler.py).
    result = run_job(name, fn, triggered_by="cli", retries=0, **kwargs)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("status") in ("success", "skipped") else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="ow", description="OrbitWatch command line")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").add_argument("--cached", action="store_true")
    s = sub.add_parser("mysql-setup")
    s.add_argument("--reset", action="store_true", help="drop and recreate the orbitwatch database")
    for name in ("mongo-init", "mongo-start", "mongo-stop", "mongo-status", "aggregate", "screen", "space-weather",
                 "reentry-train", "reentry-predict", "backup", "create-admin", "make-cert", "scheduler", "service"):
        sub.add_parser(name)
    for name in ("load-catalog", "ingest"):
        sub.add_parser(name).add_argument("--cached", action="store_true")
    sub.add_parser("assess").add_argument("event_id", type=int)
    s = sub.add_parser("spacetrack-import")
    s.add_argument("--mode", choices=["watchlist", "decayed"], required=True)
    s.add_argument("--days", type=int, default=730)
    s.add_argument("--limit", type=int, default=200)
    s = sub.add_parser("verify")
    s.add_argument("what", choices=["restore", "failover", "https", "scheduler", "all"])
    s = sub.add_parser("reconcile")
    s.add_argument("--apply", action="store_true", help="make the changes (default: report only)")
    s.add_argument("--no-git", action="store_true", help="only the local copy of orbitalguard.db (runtime/archive)")
    for name in ("restore-mysql", "restore-mongo"):
        sub.add_parser(name).add_argument("directory")
    s = sub.add_parser("serve")
    s.add_argument("--http", action="store_true", help="plain HTTP (development only)")
    s.add_argument("--port", type=int, default=None, help="default 8443 (HTTPS) or 5000 (--http)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--no-redirect", action="store_true", help="do not listen on the HTTP redirect port")
    args = p.parse_args(argv)

    setup_logging(args.cmd)
    if args.cmd in ("load-catalog", "ingest", "setup") and args.cached:
        config.USE_CACHE = True

    if args.cmd == "mysql-setup":
        from orbitwatch import mysql_setup
        mysql_setup.run(reset=args.reset)
    elif args.cmd == "mongo-init":
        from orbitwatch import mongo_cluster
        print(json.dumps(mongo_cluster.init(), indent=2, default=str))
    elif args.cmd == "mongo-start":
        from orbitwatch import mongo_cluster
        mongo_cluster.start()
        mongo_cluster.print_status()
    elif args.cmd == "mongo-stop":
        from orbitwatch import mongo_cluster
        left = mongo_cluster.stop()
        print("stopped" if not left else f"force-killed after clean shutdown failed on {left}")
    elif args.cmd == "mongo-status":
        from orbitwatch import mongo_cluster
        mongo_cluster.print_status()
    elif args.cmd == "load-catalog":
        from orbitwatch.jobs import catalog
        return _job("catalog", catalog.run)
    elif args.cmd == "ingest":
        from orbitwatch.jobs import ingest
        return _job("ingest", ingest.run)
    elif args.cmd == "screen":
        from orbitwatch.jobs import screening
        return _job("screening", screening.run)
    elif args.cmd == "space-weather":
        from orbitwatch.jobs import spaceweather
        return _job("space_weather", spaceweather.run)
    elif args.cmd == "assess":
        from orbitwatch import db
        from orbitwatch.agent import orchestrator
        aid = orchestrator.start(args.event_id, None, "admin", background=False)
        row = db.query_one("admin", "SELECT * FROM agent_assessment WHERE assessment_id = %s", (aid,))
        for st in db.query("admin", "SELECT step_no, actor, tool_name, summary FROM agent_step WHERE assessment_id = %s "
                                    "ORDER BY step_no", (aid,)):
            print(f"{st['step_no']:>3} {st['actor']:<9} {st['tool_name'] or '':<30} {st['summary']}")
        print(json.dumps({k: row[k] for k in ("assessment_id", "status", "engine", "risk_tier", "decision", "pc_before",
                                              "pc_after", "delta_v_mps", "burn_time", "burn_direction", "explanation")},
                         indent=2, default=str))
        return 0 if row["status"] == "complete" else 1
    elif args.cmd == "aggregate":
        from orbitwatch.jobs import aggregate
        return _job("aggregation", aggregate.run)
    elif args.cmd == "reentry-train":
        from orbitwatch.jobs import reentry
        return _job("reentry_train", reentry.train)
    elif args.cmd == "reentry-predict":
        from orbitwatch.jobs import reentry
        return _job("reentry_predict", reentry.predict)
    elif args.cmd == "spacetrack-import":
        from orbitwatch.jobs import spacetrack
        return _job("spacetrack_import", spacetrack.run, mode=args.mode, days=args.days, limit=args.limit)
    elif args.cmd == "backup":
        from orbitwatch.jobs import backup
        return _job("backup", backup.run)
    elif args.cmd == "verify":
        from orbitwatch import verify
        fns = {"restore": verify.verify_restore, "failover": verify.verify_failover, "https": verify.verify_https,
               "scheduler": verify.verify_scheduler}
        passed = True
        for name in (fns if args.what == "all" else [args.what]):
            res = fns[name]()
            passed &= bool(res.get("passed"))
            print(json.dumps(res, indent=2, default=str))
        return 0 if passed else 1
    elif args.cmd == "reconcile":
        from orbitwatch import reconcile
        return _job("reconcile", reconcile.run, apply=args.apply, include_git=not args.no_git)
    elif args.cmd == "restore-mysql":
        from orbitwatch.jobs import backup
        backup.restore_mysql(args.directory)
    elif args.cmd == "restore-mongo":
        from orbitwatch.jobs import backup
        backup.restore_mongo(args.directory)
    elif args.cmd == "create-admin":
        return create_admin()
    elif args.cmd == "make-cert":
        from orbitwatch.server import make_cert
        print(make_cert())
    elif args.cmd == "serve":
        from orbitwatch import server
        server.serve(host=args.host, port=args.port, http=args.http, redirect_port=0 if args.no_redirect else None)
    elif args.cmd == "service":
        from orbitwatch import server
        server.service()
    elif args.cmd == "scheduler":
        from orbitwatch.jobs.scheduler import run_scheduler
        run_scheduler()
    elif args.cmd == "setup":
        return setup()
    return 0


def create_admin():
    from orbitwatch import auth
    name = input("Name: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Password (min 8 characters): ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match")
        return 1
    user_id = auth.create_user(name, email, password, role="admin")
    print(f"Administrator created (user_id {user_id})")
    return 0


def setup():
    from orbitwatch import mongo_cluster, mysql_setup
    from orbitwatch.jobs import catalog, ingest, screening
    from orbitwatch.server import make_cert
    from orbitwatch.jobs.runner import run_job
    print("1/6 HTTPS certificate:", make_cert())
    print("2/6 MySQL schema, routines, roles and accounts")
    mysql_setup.run(reset=False)
    print("3/6 MongoDB sharded cluster")
    mongo_cluster.init()
    for step, (name, fn) in enumerate((("catalog", catalog.run), ("ingest", ingest.run),
                                       ("screening", screening.run)), start=4):
        result = run_job(name, fn, triggered_by="cli")
        print(f"{step}/6 {name}: {result['status']} {result.get('message') or ''}")
    print("Done. Create an administrator with:  ow create-admin")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
