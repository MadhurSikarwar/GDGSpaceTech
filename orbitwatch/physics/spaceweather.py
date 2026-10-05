"""Live space weather from NOAA SWPC, ported from OrbitalGuard's noaa_client.py.

Planetary Kp / a_running (3-hourly) and the F10.7 solar flux. a_running (Ap,
linear in geomagnetic energy) drives the drag-activity scalar that inflates
the in-track covariance growth used for Pc. On any failure a quiet-sun
snapshot marked live=False is returned instead.
"""
import logging
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


def _latest(rows):
    # The Kp product has been published both as objects and as a header row + lists.
    if rows and isinstance(rows[0], list):
        header = rows[0]
        rows = [dict(zip(header, r)) for r in rows[1:]]
    return rows[-1]


def fetch():
    """One live reading from NOAA (or the quiet-sun fallback)."""
    try:
        h = {"User-Agent": config.HTTP_USER_AGENT}
        kp_row = _latest(requests.get(KP_URL, timeout=10, headers=h).json())
        f107_row = requests.get(F107_URL, timeout=10, headers=h).json()[-1]
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
