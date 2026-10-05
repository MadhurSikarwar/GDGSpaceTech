"""Logging to console and a rotating file per process (SRS 4.5: errors and operations are logged).

The web server, the scheduler and one-off CLI commands write separate files:
several processes rotating one shared file fails on Windows.
"""
import logging
from logging.handlers import RotatingFileHandler

from orbitwatch import config

LOG_FILES = ("web", "scheduler", "cli")


def log_path(name):
    return config.LOG_DIR / f"{name}.log"


def setup_logging(command="cli", level=logging.INFO):
    root = logging.getLogger()
    if getattr(root, "_orbitwatch_configured", False):
        return
    name = {"serve": "web", "scheduler": "scheduler"}.get(command, "cli")
    fmt = logging.Formatter(f"%(asctime)s %(levelname)-7s [{command}] %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_path(name), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(file_handler)
    root.addHandler(console)
    root.setLevel(level)
    for noisy in ("pymongo", "urllib3", "apscheduler", "werkzeug", "mysql.connector"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._orbitwatch_configured = True


def tail(name, lines=200):
    path = log_path(name)
    if name not in LOG_FILES or not path.exists():
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.readlines()[-lines:]
