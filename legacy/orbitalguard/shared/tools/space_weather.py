"""
Tool: get_space_weather
Exposes real-time NOAA Space Weather Prediction Center (SWPC) observations
(Planetary Kp, Ap running, and F10.7 cm solar radio flux) with thermospheric drag scalar.
Reuses existing services/propagation/app/spaceweather/noaa_client.py.
"""

from shared.schemas.space_weather import SpaceWeatherSnapshot
from shared.tools.errors import ComputationError
from services.propagation.app.spaceweather.noaa_client import get_space_weather as _get_space_weather


def get_space_weather(force_refresh: bool = False) -> SpaceWeatherSnapshot:
    """
    Retrieve the latest space weather telemetry snapshot and atmospheric drag activity scalar.

    Pulls live NOAA SWPC Planetary Kp index, running Ap geomagnetic index, and
    F10.7 cm solar flux. Returns quiet-sun fallback (tagged live=False) if NOAA is unreachable.

    Args:
        force_refresh: If True, bypasses internal in-process cache and fetches fresh NOAA data.

    Returns:
        SpaceWeatherSnapshot containing kp_index, ap_index, f107_sfu, activity_level,
        drag_activity_scalar, and live indicator.

    Raises:
        ComputationError: If space weather retrieval encounters an unrecoverable failure.
    """
    try:
        return _get_space_weather(force_refresh=force_refresh)
    except Exception as e:
        raise ComputationError(f"Space weather retrieval failed: {e}") from e
