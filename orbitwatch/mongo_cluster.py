"""Local MongoDB sharded cluster for the orbital history (SRS 4.6 / 4.7).

Topology (all on 127.0.0.1, keyfile-authenticated):

    mongos router          :27017   <- the application connects here
    config server  cfgRS   :27019   (1-member replica set)
    shard A        shardA  :27101 :27102 :27103   (3-member replica set)
    shard B        shardB  :27201 :27202 :27203   (3-member replica set)

orbitwatch.orbit_history is sharded on {norad_id: 1} (ranged, not hashed,
so the unique {norad_id, epoch} index is allowed: a unique index on a
sharded collection must be prefixed by the shard key). It is pre-split so
both shards hold data from the start. Each shard being a 3-member replica
set lets you stop its primary and watch a secondary get elected.
"""
import base64
import json
import logging
import os
import secrets
import subprocess
import time
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError, ServerSelectionTimeoutError

from orbitwatch import config

log = logging.getLogger(__name__)

CONFIG_RS = ("cfgRS", [27019])
SHARDS = {"shardA": [27101, 27102, 27103], "shardB": [27201, 27202, 27203]}
MONGOS_PORT = 27017
CACHE_GB = "0.25"           # small WiredTiger caches: 7 mongod processes share one laptop
SPLIT_AT_NORAD = 58000      # ~median NORAD id of objects with current orbits (Oct 2026): balances the shards

_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
_BREAKAWAY = 0x01000000              # CREATE_BREAKAWAY_FROM_JOB


def _bin_dir():
    found = sorted(config.MONGO_HOME.glob("mongodb-win32-*/bin"))
    if not found:
        raise RuntimeError(f"MongoDB binaries not found under {config.MONGO_HOME}")
    return found[-1]


def _keyfile():
    path = config.MONGO_HOME / "cluster.key"
    if not path.exists():
        path.write_text(base64.b64encode(secrets.token_bytes(756)).decode()[:1000], encoding="ascii")
    return path


def _members():
    """(role, replica set, port) for every mongod."""
    out = [("configsvr", CONFIG_RS[0], CONFIG_RS[1][0])]
    for rs, ports in SHARDS.items():
        out.extend(("shardsvr", rs, p) for p in ports)
    return out


def _direct(port, auth=True, timeout_ms=3000):
    kwargs = dict(directConnection=True, serverSelectionTimeoutMS=timeout_ms)
    if auth and os.getenv("MONGO_ROOT_PASSWORD"):
        kwargs.update(username="ow_root", password=os.getenv("MONGO_ROOT_PASSWORD"), authSource="admin")
    return MongoClient("127.0.0.1", port, **kwargs)


def _is_up(port):
    try:
        _direct(port, auth=False, timeout_ms=800).admin.command("ping")
        return True
    except PyMongoError:
        return False


def _spawn(args, log_name):
    logs = config.LOG_DIR / "mongo"
    logs.mkdir(parents=True, exist_ok=True)
    args = args + ["--logpath", str(logs / f"{log_name}.log"), "--logappend"]
    popen = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        # Break away from the launching terminal's job object so the cluster
        # keeps running after the command that started it exits.
        subprocess.Popen(args, creationflags=_DETACHED | _BREAKAWAY, **popen)
    except OSError:
        subprocess.Popen(args, creationflags=_DETACHED, **popen)


def _wait(port, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _is_up(port):
            return
        time.sleep(0.5)
    raise RuntimeError(f"mongod on port {port} did not come up; see {config.LOG_DIR / 'mongo'}")


def start_mongod():
    bin_dir, key = _bin_dir(), _keyfile()
    for role, rs, port in _members():
        if _is_up(port):
            continue
        dbpath = config.MONGO_DATA_DIR / f"{rs}-{port}"
        dbpath.mkdir(parents=True, exist_ok=True)
        _spawn([str(bin_dir / "mongod.exe"), f"--{role}", "--replSet", rs, "--port", str(port),
                "--bind_ip", "127.0.0.1", "--dbpath", str(dbpath), "--keyFile", str(key),
                "--wiredTigerCacheSizeGB", CACHE_GB], f"{rs}-{port}")
    for _, _, port in _members():
        _wait(port)


def start_mongos():
    if _is_up(MONGOS_PORT):
        return
    rs, ports = CONFIG_RS
    _spawn([str(_bin_dir() / "mongos.exe"), "--configdb", f"{rs}/" + ",".join(f"127.0.0.1:{p}" for p in ports),
            "--port", str(MONGOS_PORT), "--bind_ip", "127.0.0.1", "--keyFile", str(_keyfile())], "mongos")
    _wait(MONGOS_PORT)


def start():
    start_mongod()
    start_mongos()


def _wait_primary(rs, ports, seconds=90):
    deadline = time.time() + seconds
    while time.time() < deadline:
        for p in ports:
            try:
                if _direct(p, auth=False).admin.command("hello").get("isWritablePrimary"):
                    return p
            except PyMongoError:
                pass
        time.sleep(1)
    raise RuntimeError(f"replica set {rs} elected no primary")


def _initiate(rs, ports, configsvr=False):
    cfg = {"_id": rs, "members": [{"_id": i, "host": f"127.0.0.1:{p}"} for i, p in enumerate(ports)]}
    if configsvr:
        cfg["configsvr"] = True
    try:
        # Allowed through the localhost exception before any user exists.
        _direct(ports[0], auth=False).admin.command("replSetInitiate", cfg)
    except OperationFailure as exc:
        # 23 AlreadyInitialized; 13 Unauthorized means users exist, i.e. set up earlier.
        if exc.code not in (23, 13):
            raise


def _create_root_user(client, password):
    try:
        client.admin.command("createUser", "ow_root", pwd=password, roles=["root"])
    except OperationFailure as exc:
        # 51003: user exists; 13: unauthorised because users already exist (localhost exception closed)
        if exc.code not in (51003, 13):
            raise


def init():
    """First-time setup: replica sets, users, shards, sharded collection, indexes."""
    root_pw = config.ensure_secret("MONGO_ROOT_PASSWORD")
    jobs_pw = config.ensure_secret("MONGO_JOBS_PASSWORD")
    analyst_pw = config.ensure_secret("MONGO_ANALYST_PASSWORD")

    start_mongod()
    rs, ports = CONFIG_RS
    _initiate(rs, ports, configsvr=True)
    for shard, shard_ports in SHARDS.items():
        _initiate(shard, shard_ports)
    _wait_primary(rs, ports)
    for shard, shard_ports in SHARDS.items():
        primary = _wait_primary(shard, shard_ports)
        # Shard-local administrator closes the localhost exception on each shard.
        _create_root_user(_direct(primary, auth=False), root_pw)

    start_mongos()
    _create_root_user(_direct(MONGOS_PORT, auth=False), root_pw)
    admin = MongoClient("127.0.0.1", MONGOS_PORT, username="ow_root", password=root_pw,
                        authSource="admin", serverSelectionTimeoutMS=10000)

    existing = {s["_id"] for s in admin.admin.command("listShards")["shards"]}
    for shard, shard_ports in SHARDS.items():
        if shard not in existing:
            admin.admin.command("addShard", f"{shard}/" + ",".join(f"127.0.0.1:{p}" for p in shard_ports), name=shard)

    for user, pw, role in (("ow_jobs", jobs_pw, "readWrite"), ("ow_analyst", analyst_pw, "read")):
        try:
            admin.admin.command("createUser", user, pwd=pw, roles=[{"role": role, "db": config.MONGO_DATABASE}])
        except OperationFailure as exc:
            if exc.code != 51003:
                raise
            admin.admin.command("updateUser", user, pwd=pw, roles=[{"role": role, "db": config.MONGO_DATABASE}])

    db = admin[config.MONGO_DATABASE]
    ns = f"{config.MONGO_DATABASE}.orbit_history"
    db.orbit_history.create_index([("norad_id", 1), ("epoch", 1)], unique=True, name="uq_norad_epoch")
    db.orbit_history.create_index([("epoch", 1)], name="ix_epoch")
    admin.admin.command("enableSharding", config.MONGO_DATABASE)
    sharded = admin.config.collections.find_one({"_id": ns})
    if not sharded:
        admin.admin.command("shardCollection", ns, key={"norad_id": 1})
        admin.admin.command("split", ns, middle={"norad_id": SPLIT_AT_NORAD})
        # Put the upper chunk on whichever shard does not hold the lower one.
        uuid = admin.config.collections.find_one({"_id": ns})["uuid"]
        lower = admin.config.chunks.find_one({"uuid": uuid, "max.norad_id": SPLIT_AT_NORAD})
        target = next(s for s in SHARDS if s != lower["shard"])
        admin.admin.command("moveChunk", ns, find={"norad_id": SPLIT_AT_NORAD}, to=target)

    db.raw_catalog.create_index([("source", 1), ("key", 1), ("version", -1)], unique=True, name="uq_source_key_version")
    db.download_log.create_index([("started_at", -1)], name="ix_started")
    db.download_log.create_index([("source", 1), ("started_at", -1)], name="ix_source_started")
    log.info("MongoDB cluster initialised")
    return status()


def stop():
    """Clean shutdown: router first, then shard secondaries, primaries, config server."""
    order = [MONGOS_PORT]
    for shard, ports in SHARDS.items():
        order.extend(ports)
    order.extend(CONFIG_RS[1])
    for port in order:
        if not _is_up(port):
            continue
        try:
            _direct(port).admin.command("shutdown", force=True, timeoutSecs=10)
        except PyMongoError:
            pass  # the connection drops as the server exits
    time.sleep(2)
    leftover = [p for p in order if _is_up(p)]
    if leftover:
        subprocess.run(["taskkill", "/F", "/IM", "mongos.exe"], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "mongod.exe"], capture_output=True)
    return leftover


def status():
    out = {"processes": {}, "replica_sets": {}, "sharding": None}
    for role, rs, port in _members():
        out["processes"][f"{rs}:{port}"] = _is_up(port)
    out["processes"][f"mongos:{MONGOS_PORT}"] = _is_up(MONGOS_PORT)
    for rs, ports in [CONFIG_RS] + list(SHARDS.items()):
        for p in ports:
            if not _is_up(p):
                continue
            try:
                st = _direct(p).admin.command("replSetGetStatus")
                out["replica_sets"][rs] = {m["name"]: m["stateStr"] for m in st["members"]}
                break
            except PyMongoError as exc:
                out["replica_sets"][rs] = f"unavailable: {exc}"
    if _is_up(MONGOS_PORT) and os.getenv("MONGO_ROOT_PASSWORD"):
        try:
            client = _direct(MONGOS_PORT)
            ns = f"{config.MONGO_DATABASE}.orbit_history"
            coll = client.config.collections.find_one({"_id": ns})
            chunks = list(client.config.chunks.aggregate([
                {"$match": {"uuid": coll["uuid"]}} if coll else {"$match": {"ns": ns}},
                {"$group": {"_id": "$shard", "chunks": {"$sum": 1}}}]))
            docs = {}
            for shard, ports in SHARDS.items():
                for p in ports:
                    try:
                        c = _direct(p)
                        if c.admin.command("hello").get("isWritablePrimary"):
                            docs[shard] = c[config.MONGO_DATABASE].orbit_history.estimated_document_count()
                            break
                    except PyMongoError:
                        continue
            out["sharding"] = {"collection": ns, "shard_key": coll["key"] if coll else None,
                               "chunks": {c["_id"]: c["chunks"] for c in chunks}, "documents": docs}
        except PyMongoError as exc:
            out["sharding"] = f"unavailable: {exc}"
    return out


def print_status():
    print(json.dumps(status(), indent=2, default=str))
