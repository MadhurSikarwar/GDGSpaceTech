"""
Three real polar/high-latitude ground stations and Skyfield-based
line-of-sight (AOS/LOS) window computation.

Coordinates (WGS84):
  - Svalbard (SvalSat), Norway     : 78.23 N,  15.39 E
  - Fairbanks (Gilmore Creek), AK  : 64.98 N, 147.50 W
  - McMurdo Station, Antarctica    : 77.85 S, 166.67 E

All three are real, operational near-polar ground stations, chosen
specifically because they sit near the poles: a high-inclination satellite
passes over all three on nearly every orbit, which is precisely why such
stations exist (polar-orbiting sun-synchronous missions need exactly this).
A ~51.6-degree-inclination orbit like the ISS's has a ground track that never
reaches these stations' latitudes (a ground track's max latitude is bounded
by inclination) -- but that alone doesn't guarantee zero *visibility*: at a
shallow enough elevation, a LEO horizon footprint can extend a couple of
thousand km past the subsatellite point, occasionally grazing a station a
little beyond the ground track's own reach. A 10-degree mask (rather than a
looser 5) keeps this module's outputs matching that intuition cleanly: at
that mask, the ISS reports zero passes at all three stations, while TERRA/
AQUA (NORAD 25994 / 27424, ~98 deg inclination) get real, repeated,
well-elevated passes at all three -- the point of a polar-orbit demo target.

Elevation-angle geometry (accounting for Earth's rotation) is delegated
entirely to Skyfield -- already a dependency, already used for propagation --
via the standard "satellite minus ground station" topocentric difference
vector. No manual GMST/sidereal-time math is needed server-side.
"""

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np
from skyfield.api import wgs84

from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine, ts
from shared.schemas.ground_station import GroundStation, PassWindow

GROUND_STATIONS: List[GroundStation] = [
    GroundStation(
        station_id="SVALBARD", name="Svalbard Satellite Station (SvalSat)",
        lat_deg=78.23, lon_deg=15.39, altitude_m=458.0, min_elevation_deg=10.0,
    ),
    GroundStation(
        station_id="FAIRBANKS", name="Gilmore Creek Station, Fairbanks AK",
        lat_deg=64.98, lon_deg=-147.50, altitude_m=200.0, min_elevation_deg=10.0,
    ),
    GroundStation(
        station_id="MCMURDO", name="McMurdo Ground Station, Antarctica",
        lat_deg=-77.85, lon_deg=166.67, altitude_m=10.0, min_elevation_deg=10.0,
    ),
]

_STATIONS_BY_ID = {s.station_id: s for s in GROUND_STATIONS}


def _build_window(station_id: str, times: List[datetime], elevations: np.ndarray, i0: int, i1: int) -> PassWindow:
    peak_offset = int(np.argmax(elevations[i0:i1 + 1]))
    return PassWindow(
        station_id=station_id,
        aos=times[i0],
        los=times[i1],
        max_elevation_deg=float(elevations[i0 + peak_offset]),
    )


def compute_passes_for_object(
    tle_line1: str,
    tle_line2: str,
    name: str,
    start_dt: datetime,
    horizon_minutes: int,
    step_seconds: float = 30.0,
) -> List[PassWindow]:
    """
    Sample elevation angle from each ground station to the object across the
    horizon at step_seconds resolution, and extract contiguous AOS-to-LOS
    windows where elevation >= that station's min_elevation_deg. 30s sampling
    keeps a 90-minute horizon x 3 stations to ~540 Skyfield observations
    total -- fast, and fine enough that a real LEO pass (typically several
    minutes above a 5-degree mask) is never missed between samples.
    """
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    satellite = SGP4PropagationEngine(tle_line1, tle_line2, name).satellite
    num_steps = int((horizon_minutes * 60.0) / step_seconds) + 1
    times = [start_dt + timedelta(seconds=i * step_seconds) for i in range(num_steps)]
    t = ts.from_datetimes(times)

    windows: List[PassWindow] = []
    for station in GROUND_STATIONS:
        topos = wgs84.latlon(station.lat_deg, station.lon_deg, elevation_m=station.altitude_m)
        alt, _az, _distance = (satellite - topos).at(t).altaz()
        elevations = alt.degrees

        above = elevations >= station.min_elevation_deg
        in_pass = False
        pass_start_idx = 0
        for i, is_above in enumerate(above):
            if is_above and not in_pass:
                in_pass = True
                pass_start_idx = i
            elif not is_above and in_pass:
                in_pass = False
                windows.append(_build_window(station.station_id, times, elevations, pass_start_idx, i - 1))
        if in_pass:
            # Pass still in progress at the end of the horizon -- report the
            # partial window rather than dropping a real, ongoing pass.
            windows.append(_build_window(station.station_id, times, elevations, pass_start_idx, len(times) - 1))

    windows.sort(key=lambda w: w.aos)
    return windows
