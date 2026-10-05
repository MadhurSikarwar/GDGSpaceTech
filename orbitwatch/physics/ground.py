"""Ground-station visibility windows (AOS/LOS), ported from OrbitalGuard's ground_stations.py.

OrbitalGuard used Skyfield; here the satellite is propagated with SGP4,
rotated into the Earth-fixed frame, and the elevation above each station's
local horizon is computed directly. Stations come from the MySQL
ground_station table.
"""
from datetime import timedelta

import numpy as np

from orbitwatch import orbital

WGS84_A = 6378.137
WGS84_E2 = 0.00669437999014


def station_ecef(lat_deg, lon_deg, alt_m):
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(lat) ** 2)
    h = alt_m / 1000.0
    pos = np.array([(n + h) * np.cos(lat) * np.cos(lon), (n + h) * np.cos(lat) * np.sin(lon),
                    (n * (1 - WGS84_E2) + h) * np.sin(lat)])
    up = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    return pos, up


def passes(elements, stations, start, hours=24.0, step_s=30.0):
    """Contiguous windows with elevation >= each station's mask, sorted by AOS."""
    sat = orbital.satrec_from_elements(elements)
    offsets = np.arange(0.0, hours * 3600.0 + step_s, step_s)
    jd, fr = orbital.time_grid(start, offsets)
    err, r, _ = orbital.propagate([sat], jd, fr)
    ecef = orbital.teme_to_ecef(r[0], jd, fr)
    out = []
    for st in stations:
        pos, up = station_ecef(float(st["latitude"]), float(st["longitude"]), float(st["altitude_m"]))
        rho = ecef - pos
        elev = np.degrees(np.arcsin(np.clip((rho @ up) / np.linalg.norm(rho, axis=1), -1, 1)))
        elev[err[0] != 0] = -90.0
        above = elev >= float(st["min_elevation_deg"])
        i = 0
        while i < len(above):
            if above[i]:
                j = i
                while j + 1 < len(above) and above[j + 1]:
                    j += 1
                peak = i + int(np.argmax(elev[i:j + 1]))
                out.append({"station_id": st["station_id"], "station": st["name"],
                            "aos": start + timedelta(seconds=float(offsets[i])),
                            "los": start + timedelta(seconds=float(offsets[j])),
                            "max_elevation_deg": round(float(elev[peak]), 1),
                            "duration_min": round((offsets[j] - offsets[i]) / 60.0, 1)})
                i = j + 1
            else:
                i += 1
    return sorted(out, key=lambda p: p["aos"])
