"""Infrastructure verification: test it, don't just configure it.

    ow verify restore    take a backup, restore it into ISOLATED temporary MySQL and MongoDB instances
                         (separate data directories and ports, torn down afterwards) and compare every
                         table and collection with the counts recorded in the backup manifest
    ow verify failover   MongoDB replica-set failover on shard A: step the primary down, check that reads
                         and majority writes through mongos keep working and a new primary is elected;
                         then stop a secondary, keep writing, restart it and wait until it has caught up
    ow verify https      the running server: TLS version/cipher/certificate, TLS 1.1 refused, HTTP->HTTPS
                         redirect, Secure/HttpOnly/SameSite cookies, security headers, assets over HTTPS,
                         the live stream over TLS, no mixed-content (http://) references in the front end
    ow verify scheduler  the scheduler's heartbeat and the jobs it actually ran on its own
    ow verify all

Each run writes runtime/reports/verify-<what>-<time>.json and logs a summary in the event log.
"""
import gzip
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import bson
import mysql.connector
import requests
from pymongo import MongoClient, WriteConcern
from pymongo.errors import AutoReconnect, PyMongoError

from orbitwatch import config, db, events

log = logging.getLogger(__name__)
REPORT_DIR = config.RUNTIME_DIR / "reports"
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _free_port(start):
    for port in range(start, start + 100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


def _wait(fn, seconds, what):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - keep polling until the deadline
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"{what} did not become ready: {last}")


def _report(what, result):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"verify-{what}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    ok = result.get("passed")
    events.record("system", f"verify_{what}", f"Infrastructure check '{what}': {'PASSED' if ok else 'FAILED'}",
                  severity="info" if ok else "warning", visibility="admin", detail={"report": str(path)})
    result["report"] = str(path)
    return result


# --------------------------------------------------------------------------------------- restore
def verify_restore(backup_dir=None, fresh=True):
    from orbitwatch.jobs import backup
    from orbitwatch.jobs.runner import run_job
    if fresh and backup_dir is None:
        r = run_job("backup", backup.run, triggered_by="cli", retries=0)
        if r.get("status") != "success":
            raise RuntimeError(f"backup failed: {r.get('message')}")
    if backup_dir is None:
        sets = sorted(p for p in config.BACKUP_DIR.iterdir() if (p / "manifest.json").exists())
        backup_dir = sets[-1]
    backup_dir = Path(backup_dir)
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    result = {"backup": backup_dir.name, "checks": {}}
    work = config.RUNTIME_DIR / "verify" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True)
    try:
        # checksums of the backup files
        if manifest.get("sha256"):
            from orbitwatch.jobs.backup import _sha256
            bad = [n for n, h in manifest["sha256"].items() if n != "manifest.json" and _sha256(backup_dir / n) != h]
            result["checks"]["checksums"] = {"files": len(manifest["sha256"]), "mismatched": bad, "ok": not bad}
        result["mysql"] = _restore_mysql_isolated(backup_dir, manifest, work)
        result["mongo"] = _restore_mongo_isolated(backup_dir, manifest, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result["passed"] = (result["mysql"]["ok"] and result["mongo"]["ok"]
                        and result["checks"].get("checksums", {}).get("ok", True))
    return _report("restore", result)


def _restore_mysql_isolated(backup_dir, manifest, work):
    mysqld = config.MYSQL_BIN_DIR / "mysqld.exe"
    mysql_cli = config.MYSQL_BIN_DIR / "mysql.exe"
    datadir = work / "mysql-data"
    port = _free_port(3390)
    t0 = time.time()
    subprocess.run([str(mysqld), "--no-defaults", f"--datadir={datadir}", "--initialize-insecure", "--console"],
                   check=True, capture_output=True, timeout=600, creationflags=_NO_WINDOW)
    proc = subprocess.Popen([str(mysqld), "--no-defaults", f"--datadir={datadir}", f"--port={port}", "--mysqlx=OFF",
                             "--bind-address=127.0.0.1", "--skip-log-bin", f"--log-error={work / 'mysqld.err'}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    try:
        conn = _wait(lambda: mysql.connector.connect(host="127.0.0.1", port=port, user="root", password=""),
                     120, "temporary MySQL")
        cur = conn.cursor()
        cur.execute("CREATE DATABASE orbitwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        load = subprocess.Popen([str(mysql_cli), "--host=127.0.0.1", f"--port={port}", "--user=root",
                                 "--default-character-set=utf8mb4", "orbitwatch"],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW)
        with gzip.open(backup_dir / "mysql_orbitwatch.sql.gz", "rb") as gz:
            shutil.copyfileobj(gz, load.stdin)
        load.stdin.close()
        err = load.stderr.read().decode(errors="replace")
        if load.wait() != 0:
            raise RuntimeError(f"loading the dump failed: {err[:500]}")
        restore_s = time.time() - t0
        cur.execute("USE orbitwatch")
        cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'orbitwatch' "
                    "AND TABLE_TYPE = 'BASE TABLE'")
        tables = [r[0] for r in cur.fetchall()]
        restored = {}
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            restored[t] = cur.fetchone()[0]
        objects = {}
        for kind, sql in (("views", "SELECT COUNT(*) FROM information_schema.VIEWS WHERE TABLE_SCHEMA = 'orbitwatch'"),
                          ("routines", "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = 'orbitwatch'"),
                          ("triggers", "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = 'orbitwatch'")):
            cur.execute(sql)
            objects[kind] = cur.fetchone()[0]
        # the restored copy works, not just holds rows: a view, a function and a join
        cur.execute("SELECT COUNT(*) FROM v_object_catalog WHERE in_earth_orbit")
        smoke_view = cur.fetchone()[0]
        cur.execute("SELECT fn_risk_level(0.5, 10)")
        smoke_fn = cur.fetchone()[0]
        try:
            cur.execute("SHUTDOWN")
        except mysql.connector.Error:
            pass
        conn.close()
    finally:
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
    live_objects = {  # the backup account may see trigger and routine definitions (TRIGGER, SHOW_ROUTINE)
        "views": db.query_one("backup", "SELECT COUNT(*) AS n FROM information_schema.VIEWS WHERE TABLE_SCHEMA = 'orbitwatch'")["n"],
        "routines": db.query_one("backup", "SELECT COUNT(*) AS n FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = 'orbitwatch'")["n"],
        "triggers": db.query_one("backup", "SELECT COUNT(*) AS n FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = 'orbitwatch'")["n"],
    }
    expected = manifest.get("mysql_rows") or {}
    volatile = set(manifest.get("volatile_tables") or [])
    mismatches = {t: {"expected": n, "restored": restored.get(t)} for t, n in expected.items()
                  if t not in volatile and restored.get(t) != n}
    drift = {t: {"expected": n, "restored": restored.get(t)} for t, n in expected.items()
             if t in volatile and restored.get(t) != n}
    missing = sorted(set(expected) - set(restored))
    ok = bool(expected) and not mismatches and not missing and objects == live_objects and smoke_view > 0 and bool(smoke_fn)
    return {"ok": ok, "port": port, "seconds": round(restore_s, 1), "tables": len(restored),
            "rows": sum(restored.values()), "mismatches": mismatches, "missing_tables": missing,
            "volatile_drift": drift, "schema_objects": objects, "live_schema_objects": live_objects,
            "smoke": {"v_object_catalog_in_orbit": smoke_view, "fn_risk_level": smoke_fn}}


def _restore_mongo_isolated(backup_dir, manifest, work):
    from orbitwatch.mongo_cluster import _bin_dir
    dbpath = work / "mongo-data"
    dbpath.mkdir()
    port = _free_port(27390)
    proc = subprocess.Popen([str(_bin_dir() / "mongod.exe"), "--dbpath", str(dbpath), "--port", str(port),
                             "--bind_ip", "127.0.0.1", "--wiredTigerCacheSizeGB", "0.25", "--logpath",
                             str(work / "mongod.log")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    try:
        client = MongoClient("127.0.0.1", port, directConnection=True, serverSelectionTimeoutMS=2000)
        _wait(lambda: client.admin.command("ping"), 90, "temporary MongoDB")
        target = client["orbitwatch_restore"]
        restored, index_ok = {}, {}
        for f in sorted(backup_dir.glob("*.bson.gz")):
            name = f.name[:-len(".bson.gz")]
            coll = target[name]
            batch, n = [], 0
            with gzip.open(f, "rb") as gz:
                for doc in bson.decode_file_iter(gz):
                    batch.append(doc)
                    if len(batch) >= 5000:
                        coll.insert_many(batch, ordered=False)
                        n += len(batch)
                        batch = []
            if batch:
                coll.insert_many(batch, ordered=False)
                n += len(batch)
            meta = backup_dir / f"{name}.metadata.json"
            if meta.exists():
                for ix in json.loads(meta.read_text())["indexes"]:
                    if ix.get("name") == "_id_":
                        continue
                    opts = {k: v for k, v in ix.items() if k in ("name", "unique", "sparse")}
                    coll.create_index([(k, int(v) if isinstance(v, (int, float)) else v) for k, v in ix["key"]], **opts)
                index_ok[name] = len(coll.index_information())
            restored[name] = coll.estimated_document_count()
        unique_history = any(v.get("unique") and [k for k, _ in v["key"]] == ["norad_id", "epoch"]
                             for v in target.orbit_history.index_information().values()) if "orbit_history" in restored else False
        try:
            client.admin.command("shutdown")
        except (AutoReconnect, PyMongoError):
            pass
    finally:
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
    expected = manifest.get("mongo_documents") or {}
    mismatches = {c: {"expected": n, "restored": restored.get(c)} for c, n in expected.items() if restored.get(c) != n}
    return {"ok": bool(expected) and not mismatches and unique_history, "port": port,
            "collections": len(restored), "documents": sum(restored.values()), "mismatches": mismatches,
            "indexes": index_ok, "orbit_history_unique_index": unique_history}


# --------------------------------------------------------------------------------------- failover
def verify_failover(shard="shardA"):
    from orbitwatch.mongo_cluster import SHARDS, _direct, start_mongod, status as cluster_status
    ports = SHARDS[shard]
    result = {"shard": shard, "steps": []}
    step = lambda name, **kw: result["steps"].append({"step": name, "at": datetime.now(timezone.utc).isoformat(), **kw})

    def members():
        for p in ports:
            try:
                st = _direct(p).admin.command("replSetGetStatus")
                return {m["name"]: m["stateStr"] for m in st["members"]}
            except PyMongoError:
                continue
        return {}

    def primary():
        return next((h for h, s in members().items() if s == "PRIMARY"), None)

    mongos = db.mongo_db("root")
    probe = mongos.get_collection("failover_probe", write_concern=WriteConcern(w="majority", wtimeout=20000))
    probe.drop()
    # a NORAD id below the split point lives on shard A
    hist = db.mongo_db("analyst").orbit_history
    sample = hist.find_one({"norad_id": {"$lt": 58000}}, {"norad_id": 1})
    before = members()
    old = primary()
    step("initial state", members=before, primary=old)
    writes = {"ok": 0, "failed": 0}
    reads = {"ok": 0, "failed": 0}

    def traffic(tag, n=10):
        for i in range(n):
            try:
                probe.insert_one({"tag": tag, "i": i, "t": datetime.now(timezone.utc)})
                writes["ok"] += 1
            except PyMongoError:
                writes["failed"] += 1
            try:
                if sample:
                    hist.find_one({"norad_id": sample["norad_id"]})
                reads["ok"] += 1
            except PyMongoError:
                reads["failed"] += 1
            time.sleep(0.2)

    traffic("before")
    # 1. planned failover: step the primary down
    t0 = time.time()
    try:
        _direct(int(old.split(":")[1])).admin.command("replSetStepDown", 30, secondaryCatchUpPeriodSecs=10)
    except AutoReconnect:
        pass   # the old primary drops its connections when it steps down
    new = _wait(lambda: (lambda p: p if p and p != old else (_ for _ in ()).throw(RuntimeError("no new primary")))(primary()),
                60, "a new primary")
    elected_s = time.time() - t0
    traffic("after stepdown")
    step("primary stepped down", old_primary=old, new_primary=new, election_seconds=round(elected_s, 1),
         members=members())
    # 2. unplanned loss of a secondary: shut one down, keep writing with w:majority (2 of 3 remain)
    secondary = next(h for h, s in members().items() if s == "SECONDARY")
    try:
        _direct(int(secondary.split(":")[1])).admin.command("shutdown", force=True)
    except (AutoReconnect, PyMongoError):
        pass
    _wait(lambda: (lambda st: st if st.get(secondary) not in ("SECONDARY", "PRIMARY") else
                   (_ for _ in ()).throw(RuntimeError("still up")))(members()), 60, "secondary to be seen down")
    step("secondary stopped", stopped=secondary, members=members())
    traffic("one member down")
    # 3. recovery: restart the stopped member and wait until it is a caught-up secondary again
    start_mongod()
    _wait(lambda: (lambda st: st if st.get(secondary) == "SECONDARY" else
                   (_ for _ in ()).throw(RuntimeError("recovering")))(members()), 180, "the stopped member to rejoin")
    lag_ok = _wait(lambda: _caught_up(_direct, ports), 120, "replication to catch up")
    step("member recovered", member=secondary, members=members(), caught_up=lag_ok)
    traffic("recovered")
    stored = probe.count_documents({})
    probe.drop()
    result.update(old_primary=old, new_primary=new, election_seconds=round(elected_s, 1),
                  writes=writes, reads=reads, probe_documents_stored=stored, final_members=members(),
                  cluster=cluster_status().get("processes"))
    result["passed"] = (new != old and writes["failed"] == 0 and reads["failed"] == 0 and stored == writes["ok"]
                        and all(s in ("PRIMARY", "SECONDARY") for s in result["final_members"].values()))
    return _report("failover", result)


def _caught_up(direct, ports):
    for p in ports:
        try:
            st = direct(p).admin.command("replSetGetStatus")
        except PyMongoError:
            continue
        optimes = [m["optimeDate"] for m in st["members"] if m["stateStr"] in ("PRIMARY", "SECONDARY")]
        if len(optimes) == len(ports) and (max(optimes) - min(optimes)).total_seconds() <= 5:
            return True
        raise RuntimeError("members are still catching up")
    raise RuntimeError("no member reachable")


# --------------------------------------------------------------------------------------- HTTPS
def verify_https(host="127.0.0.1", port=None, redirect_port=None):
    from orbitwatch import auth
    from orbitwatch.server import CERT
    port = port or config.HTTPS_PORT
    redirect_port = redirect_port or config.HTTP_REDIRECT_PORT
    base = f"https://{host}:{port}"
    checks = {}
    # TLS: modern protocol, the certificate verifies for localhost, old protocols refused
    ctx = ssl.create_default_context(cafile=str(CERT))
    with socket.create_connection((host, port), timeout=10) as sock, ctx.wrap_socket(sock, server_hostname="localhost") as tls:
        cert = tls.getpeercert()
        checks["tls"] = {"version": tls.version(), "cipher": tls.cipher()[0], "verified_for": "localhost",
                         "not_after": cert.get("notAfter"), "ok": tls.version() in ("TLSv1.2", "TLSv1.3")}
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.check_hostname = False
    old.verify_mode = ssl.CERT_NONE
    old.minimum_version = ssl.TLSVersion.TLSv1
    old.maximum_version = ssl.TLSVersion.TLSv1_1
    try:
        with socket.create_connection((host, port), timeout=10) as sock, old.wrap_socket(sock):
            checks["old_tls_refused"] = {"ok": False}
    except (ssl.SSLError, OSError) as exc:
        checks["old_tls_refused"] = {"ok": True, "error": type(exc).__name__}
    # plain HTTP on the TLS port fails; the redirect port sends everything to HTTPS, path and query kept
    try:
        requests.get(f"http://{host}:{port}/", timeout=5)
        checks["plain_http_on_tls_port"] = {"ok": False}
    except requests.RequestException:
        checks["plain_http_on_tls_port"] = {"ok": True}
    r = requests.get(f"http://{host}:{redirect_port}/dashboard?x=1", allow_redirects=False, timeout=10)
    checks["redirect"] = {"status": r.status_code, "location": r.headers.get("Location"),
                          "ok": r.status_code == 301 and r.headers.get("Location") == f"https://{host}:{port}/dashboard?x=1"}
    s = requests.Session()
    s.verify = str(CERT)
    url = lambda p: f"https://localhost:{port}{p}"
    page = s.get(url("/"), timeout=10)
    hdr = page.headers
    checks["headers"] = {k: hdr.get(k) for k in ("X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Cache-Control")}
    checks["headers"]["ok"] = (hdr.get("X-Content-Type-Options") == "nosniff" and hdr.get("X-Frame-Options") == "DENY"
                               and hdr.get("Referrer-Policy") == "same-origin")
    assets = {}
    for path in ("/css/app.css", "/js/app.js", "/js/views/globe.js", "/favicon.svg"):
        a = s.get(url(path), timeout=10)
        assets[path] = (a.status_code, a.headers.get("Content-Type", "").split(";")[0])
    checks["assets"] = {"items": assets, "ok": all(code == 200 for code, _ in assets.values())}
    # cookies over HTTPS
    email, pw = f"https-check-{secrets.token_hex(3)}@example.test", secrets.token_urlsafe(12)
    auth.create_user("HTTPS check", email, pw, "viewer")
    try:
        login = s.post(url("/api/auth/login"), json={"email": email, "password": pw},
                       headers={"X-Requested-With": "OrbitWatch"}, timeout=10)
        cookie = login.headers.get("Set-Cookie", "")
        me = s.get(url("/api/auth/me"), timeout=10).json().get("user") or {}
        checks["session_cookie"] = {"status": login.status_code, "secure": "Secure" in cookie, "httponly": "HttpOnly" in cookie,
                                    "samesite_strict": "SameSite=Strict" in cookie, "session_works": me.get("email") == email}
        checks["session_cookie"]["ok"] = all(v is True for k, v in checks["session_cookie"].items() if k != "status")
        # the live stream over TLS
        t0 = time.time()
        got = ""
        with s.get(url("/api/stream"), stream=True, timeout=15) as st:
            for chunk in st.iter_content(chunk_size=None):
                got += chunk.decode(errors="replace")
                if "event: hello" in got or time.time() - t0 > 10:
                    break
        checks["sse_over_tls"] = {"ok": "event: hello" in got, "first_event_s": round(time.time() - t0, 2)}
        s.post(url("/api/auth/logout"), headers={"X-Requested-With": "OrbitWatch"}, timeout=10)
    finally:
        db.execute("admin", "DELETE FROM app_user WHERE email = %s", (email,))
    # mixed content: no http:// resource references in the front end
    found = []
    for f in config.FRONTEND_DIR.rglob("*"):
        if f.suffix in (".html", ".js", ".css", ".svg") and f.is_file():
            for m in re.finditer(r"""(?:src|href|url\(|fetch\(|['"])\s*(http://[^'")\s]+)""", f.read_text(encoding="utf-8")):
                if "www.w3.org" not in m.group(1):
                    found.append(f"{f.relative_to(config.FRONTEND_DIR)}: {m.group(1)}")
    checks["mixed_content"] = {"http_references": found, "ok": not found}
    result = {"base": base, "checks": checks, "passed": all(c.get("ok") for c in checks.values())}
    return _report("https", result)


# --------------------------------------------------------------------------------------- scheduler
def verify_scheduler(hours=24):
    from orbitwatch.jobs import scheduler
    st = scheduler.status()
    runs = db.query("admin", """SELECT job_name, status, COUNT(*) AS n, MAX(started_at) AS last
                                  FROM job_run WHERE triggered_by = 'schedule' AND started_at > NOW() - INTERVAL %s HOUR
                                 GROUP BY job_name, status ORDER BY job_name, status""", (hours,))
    overdue = [j["job_id"] for j in st["jobs"] if j["next_run_at"]
               and (datetime.now(timezone.utc).replace(tzinfo=None) - j["next_run_at"]).total_seconds() > 600]
    result = {"running": st["running"], "heartbeat": st["heartbeat"], "scheduled_runs_last_hours": hours,
              "runs": runs, "jobs": st["jobs"], "overdue": overdue,
              "passed": st["running"] and bool(runs) and not overdue}
    return _report("scheduler", result)
