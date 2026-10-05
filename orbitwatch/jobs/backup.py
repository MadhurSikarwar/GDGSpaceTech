"""Backups of MySQL and MongoDB (SRS 4.7).

MySQL: mysqldump --single-transaction (a consistent InnoDB snapshot without
locking users out) of tables, routines, triggers and views, gzipped.
MongoDB: every collection streamed to <name>.bson.gz in mongodump's format
(concatenated BSON documents) plus its index definitions, so the files can
be restored with `ow restore-mongo` or with mongorestore --gzip.
"""
import gzip
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

import bson

from orbitwatch import config, db

log = logging.getLogger(__name__)


def _defaults_file(user, password):
    """Credentials for mysql/mysqldump in a temporary option file, never on the command line."""
    fd, path = tempfile.mkstemp(suffix=".cnf", dir=config.RUNTIME_DIR)
    with os.fdopen(fd, "w") as f:
        f.write(f"[client]\nuser={user}\npassword=\"{password}\"\nhost={config.MYSQL_HOST}\nport={config.MYSQL_PORT}\n")
    return path


def backup_mysql(target):
    user, var = config.MYSQL_ACCOUNTS["backup"]
    cnf = _defaults_file(user, config.require_env(var))
    out = target / "mysql_orbitwatch.sql.gz"
    try:
        proc = subprocess.Popen([str(config.MYSQL_BIN_DIR / "mysqldump.exe"), f"--defaults-extra-file={cnf}",
                                 "--single-transaction", "--routines", "--triggers", "--events", "--no-tablespaces",
                                 "--set-gtid-purged=OFF", "--default-character-set=utf8mb4", config.MYSQL_DATABASE],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with gzip.open(out, "wb") as gz:
            shutil.copyfileobj(proc.stdout, gz)
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"mysqldump failed: {err.strip()[:500]}")
    finally:
        os.remove(cnf)
    return out.stat().st_size


def backup_mongo(target):
    mongo = db.mongo_db("jobs")
    counts = {}
    for name in sorted(mongo.list_collection_names()):
        coll = mongo[name]
        n = 0
        with gzip.open(target / f"{name}.bson.gz", "wb") as gz:
            for doc in coll.find({}, batch_size=5000):
                gz.write(bson.encode(doc))
                n += 1
        indexes = [{"key": list(ix["key"].items()), **{k: v for k, v in ix.items() if k not in ("key", "v", "ns")}}
                   for ix in coll.list_indexes()]
        (target / f"{name}.metadata.json").write_text(json.dumps({"collection": name, "indexes": indexes},
                                                                 default=str, indent=1))
        counts[name] = n
    return counts


# Tables written continuously while the system runs: their counts may differ by a few rows between the
# count below and mysqldump's snapshot a moment later. Every other table must match exactly after a restore.
VOLATILE_TABLES = {"job_run", "event_log", "rate_limit_event", "scheduler_job", "scheduler_heartbeat", "email_outbox",
                   "space_weather", "data_source"}


def _table_counts():
    rows = db.query("jobs", "SELECT TABLE_NAME AS t FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s "
                            "AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME", (config.MYSQL_DATABASE,))
    return {r["t"]: db.query_one("jobs", f"SELECT COUNT(*) AS n FROM `{r['t']}`")["n"] for r in rows}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(ctx):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = config.BACKUP_DIR / stamp
    target.mkdir(parents=True)
    table_counts = _table_counts()
    mysql_bytes = backup_mysql(target)
    mongo_counts = backup_mongo(target)
    manifest = {"created_utc": stamp, "mysql_dump_bytes": mysql_bytes, "mysql_rows": table_counts,
                "volatile_tables": sorted(VOLATILE_TABLES), "mongo_documents": mongo_counts,
                "sha256": {f.name: _sha256(f) for f in sorted(target.iterdir()) if f.is_file()}}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))

    keep = int(db.get_config().get("backup_keep", 7))
    sets = sorted(p for p in config.BACKUP_DIR.iterdir() if p.is_dir() and (p / "manifest.json").exists())
    for old in sets[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    total_docs = sum(mongo_counts.values())
    return total_docs, (f"backup {stamp}: MySQL dump {mysql_bytes / 1e6:.1f} MB, MongoDB {total_docs} documents in "
                        f"{len(mongo_counts)} collections; kept the last {keep} sets in {config.BACKUP_DIR}")


def _confirm(what):
    answer = input(f"This overwrites the current {what} with the backup. Type 'yes' to continue: ")
    if answer.strip().lower() != "yes":
        raise SystemExit("cancelled")


def restore_mysql(directory):
    from pathlib import Path
    dump = Path(directory) / "mysql_orbitwatch.sql.gz"
    if not dump.exists():
        raise SystemExit(f"{dump} not found")
    _confirm("MySQL database 'orbitwatch'")
    cnf = _defaults_file(os.getenv("MYSQL_ROOT_USER", "root"), config.require_env("MYSQL_ROOT_PASSWORD"))
    try:
        proc = subprocess.Popen([str(config.MYSQL_BIN_DIR / "mysql.exe"), f"--defaults-extra-file={cnf}",
                                 "--default-character-set=utf8mb4", config.MYSQL_DATABASE], stdin=subprocess.PIPE)
        with gzip.open(dump, "rb") as gz:
            shutil.copyfileobj(gz, proc.stdin)
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit("mysql restore failed")
    finally:
        os.remove(cnf)
    print("MySQL restored from", dump)


def restore_mongo(directory):
    """Empties each collection and reloads it (keeps orbit_history's sharding and indexes)."""
    from pathlib import Path
    folder = Path(directory)
    files = sorted(folder.glob("*.bson.gz"))
    if not files:
        raise SystemExit(f"no .bson.gz files in {folder}")
    _confirm("MongoDB database 'orbitwatch'")
    mongo = db.mongo_db("root")
    for f in files:
        name = f.name[:-len(".bson.gz")]
        coll = mongo[name]
        coll.delete_many({})
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
        print(f"  {name}: {n} documents")
    print("MongoDB restored from", folder)
