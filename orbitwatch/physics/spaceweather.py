"""Live space weather from NOAA SWPC, ported from OrbitalGuard's noaa_client.py.

Planetary Kp / a_running (3-hourly) and the F10.7 solar flux. a_running (Ap,
linear in geomagnetic energy) drives the drag-activity scalar that inflates
the in-track covariance growth used for Pc. On any failure a quiet-sun
snapshot marked live=False is returned instead.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

from orbitwatch import config

log = logging.getLogger(__name__)

KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
QUIET = {"kp": 2.0, "ap": 7.0, "f107": 150.0}


def activity_level(kp):
    return "STORM" if kp >= 7 else "ELEVATED" if kp >= 5 else "MODERATE" if kp >= 4 else "QUIET"


def drag_scalar(ap):
    return 1.0 + max(0.0, ap - 7.0) / 50.0


def _latest(rows, only=None):
    """The record with the newest time tag. NOAA publishes some of these feeds oldest-first and others
    newest-first (the F10.7 feed is newest-first), so the position in the list says nothing."""
    # The Kp product has been published both as objects and as a header row + lists.
    if rows and isinstance(rows[0], list):
        header = rows[0]
        rows = [dict(zip(header, r)) for r in rows[1:]]
    if only:
        rows = [r for r in rows if only(r)]
    if not rows:
        raise ValueError("NOAA feed has no usable records")
    return max(rows, key=lambda r: str(r.get("time_tag") or ""))


def fetch():
    """One live reading from NOAA (or the quiet-sun fallback)."""
    try:
        h = {"User-Agent": config.HTTP_USER_AGENT}
        kp_row = _latest(requests.get(KP_URL, timeout=10, headers=h).json())
        # flux at 2800 MHz is the F10.7 index
        f107_row = _latest(requests.get(F107_URL, timeout=10, headers=h).json(),
                           only=lambda r: r.get("frequency", 2800) == 2800 and r.get("flux") is not None)
        kp = float(kp_row.get("Kp", kp_row.get("kp_index", 0)))
        ap = float(kp_row.get("a_running", kp_row.get("ap", 7.0)))
        observed = str(kp_row.get("time_tag", "")).replace("Z", "")
        return {"observed_at": datetime.fromisoformat(observed) if observed else datetime.now(timezone.utc).replace(tzinfo=None),
                "kp": kp, "ap": ap, "f107": float(f107_row["flux"]), "activity": activity_level(kp),
                "drag_scalar": drag_scalar(ap), "source": "NOAA SWPC", "live": True}
    except Exception as exc:  # noqa: BLE001 - never let space weather break a caller
        log.warning("NOAA space weather unavailable, using quiet-sun values: %s", exc)
        return {"observed_at": datetime.now(timezone.utc).replace(tzinfo=None), **QUIET,
                "activity": "QUIET", "drag_scalar": 1.0, "source": "quiet-sun fallback", "live": False}


# --------------------------------------------------------------------------- shared cache of the live reading
# Used when no recent reading is stored in MySQL (the scheduler is not running): the public space-weather chip and the
# decision agent then ask NOAA directly. Two NOAA requests can take up to 20 s when NOAA is slow or down, so the answer
# is kept in a small file: every process (web server, scheduler, a second web worker) and a restart reuse it.
CACHE_FILE = config.CACHE_DIR / "space_weather_live.json"
LIVE_TTL_S = 600        # a live reading is reused for 10 minutes
FALLBACK_TTL_S = 60     # NOAA unreachable: try again after a minute
_cache_lock = threading.Lock()


def _read_cache():
    try:
        d = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        d["observed_at"] = datetime.fromisoformat(d["observed_at"])
        return d
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cached_fetch():
    """fetch(), at most once per TTL for every process together. Requests arriving meanwhile share the answer."""
    with _cache_lock:
        d = _read_cache()
        if d is not None:
            age = time.time() - float(d.pop("_fetched_at", 0))
            if 0 <= age < (LIVE_TTL_S if d.get("live") else FALLBACK_TTL_S):
                return d
        value = fetch()
        try:
            tmp = CACHE_FILE.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps({**value, "observed_at": value["observed_at"].isoformat(), "_fetched_at": time.time()}),
                           encoding="utf-8")
            os.replace(tmp, CACHE_FILE)          # atomic: another process never reads half a file
        except OSError as exc:
            log.warning("space weather cache not written: %s", exc)
        return value
