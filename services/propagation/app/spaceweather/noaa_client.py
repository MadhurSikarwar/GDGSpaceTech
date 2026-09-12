"""
Live space-weather ingestion from NOAA's Space Weather Prediction Center.

Two public, unauthenticated JSON feeds (verified live shapes, 2026-09):
  - Planetary Kp:  https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
      -> chronological array of {time_tag, Kp, a_running, station_count}, 3-hourly.
  - F10.7 cm flux: https://services.swpc.noaa.gov/json/f107_cm_flux.json
      -> chronological array of {time_tag, frequency: 2800, flux, ...}; `flux`
         at 2800 MHz IS the F10.7 index, in solar flux units.

`a_running` (NOT raw Kp) drives the drag-activity scalar used to inflate LEO
along-track covariance growth (see physics/covariance.py): thermospheric
density models take Ap as their geomagnetic input specifically because it is
linear in geomagnetic energy, whereas Kp is quasi-logarithmic by
construction. NOAA's own `a_running` field is exactly the standard Kp->Ap
conversion-table value for that record, so it's available for free in the
same payload already being fetched for the Kp display value.

A short in-process cache (mirrors routes.py's `_objects_cache` pattern)
avoids hitting NOAA on every request; on any fetch failure this falls back
to a documented quiet-sun snapshot tagged `live=False`, following the same
LIVE-vs-SIMULATED honesty convention used throughout the frontend for the
Risk/Maneuver/Optimizer services.
"""

import logging
import time
from typing import Optional, Tuple

import httpx

from services.propagation.app.config import settings
from shared.schemas.space_weather import SpaceWeatherSnapshot

logger = logging.getLogger(__name__)

KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
F107_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"

# Quiet-time reference Ap (~7) is the standard "nothing going on" baseline;
# the divisor is an illustrative calibration knob, not a fitted constant --
# it keeps a moderate storm (ap ~50) around a ~1.9x covariance multiplier and
# a severe storm (ap ~200) around a ~4.9x multiplier, without letting a rare
# extreme event blow the covariance up unboundedly.
_AP_QUIET_REFERENCE = 7.0
_AP_SCALAR_DIVISOR = 50.0


def _classify_activity(kp: float) -> str:
    # Loosely follows NOAA's own G-scale storm boundaries (G1 minor = Kp 5).
    if kp >= 7.0:
        return "STORM"
    if kp >= 5.0:
        return "ELEVATED"
    if kp >= 4.0:
        return "MODERATE"
    return "QUIET"


def _drag_activity_scalar(ap: float) -> float:
    return 1.0 + max(0.0, (ap - _AP_QUIET_REFERENCE)) / _AP_SCALAR_DIVISOR


def _fetch_latest_kp_ap(client: httpx.Client) -> Tuple[float, float]:
    resp = client.get(KP_URL)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("NOAA Kp feed returned no records")
    latest = data[-1]  # feed is chronological; last record is most recent
    return float(latest["Kp"]), float(latest["a_running"])


def _fetch_latest_f107(client: httpx.Client) -> float:
    resp = client.get(F107_URL)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("NOAA F10.7 feed returned no records")
    return float(data[-1]["flux"])


def _quiet_sun_fallback() -> SpaceWeatherSnapshot:
    return SpaceWeatherSnapshot(
        kp_index=settings.QUIET_SUN_KP,
        ap_index=settings.QUIET_SUN_AP,
        f107_sfu=settings.QUIET_SUN_F107_SFU,
        activity_level="QUIET",
        drag_activity_scalar=1.0,
        source="QUIET_SUN_FALLBACK",
        live=False,
    )


_cache: Optional[SpaceWeatherSnapshot] = None
_cache_monotonic_time: float = 0.0


def get_space_weather(force_refresh: bool = False) -> SpaceWeatherSnapshot:
    global _cache, _cache_monotonic_time
    now = time.monotonic()
    if (
        not force_refresh
        and _cache is not None
        and (now - _cache_monotonic_time) < settings.SPACE_WEATHER_CACHE_TTL_SECONDS
    ):
        return _cache

    try:
        with httpx.Client(timeout=settings.SPACE_WEATHER_FETCH_TIMEOUT_SECONDS) as client:
            kp, ap = _fetch_latest_kp_ap(client)
            f107 = _fetch_latest_f107(client)
        snapshot = SpaceWeatherSnapshot(
            kp_index=kp,
            ap_index=ap,
            f107_sfu=f107,
            activity_level=_classify_activity(kp),
            drag_activity_scalar=_drag_activity_scalar(ap),
            source="NOAA_SWPC",
            live=True,
        )
    except Exception as exc:
        logger.warning(f"NOAA space-weather fetch failed, serving quiet-sun fallback: {exc}")
        snapshot = _quiet_sun_fallback()

    _cache = snapshot
    _cache_monotonic_time = now
    return snapshot
