"""Orbital mechanics helpers built on the SGP4 library.

Element sets are handled in the CCSDS OMM form that CelesTrak and
Space-Track publish as JSON (the 5-digit TLE text format is running out of
catalogue numbers). An SGP4 satellite record is initialised straight from
the stored mean elements, so MySQL's Current_Orbit row is all the
screening job needs; no TLE text is stored.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sgp4.api import WGS72, Satrec, SatrecArray, jday

MU_EARTH = 398600.4418          # km^3/s^2
EARTH_RADIUS_KM = 6378.137      # WGS84 equatorial radius
WGS84_F = 1.0 / 298.257223563
_EPOCH0 = datetime(1949, 12, 31)
_NDOT_UNITS = 1036800.0 / math.pi          # rev/day^2  -> rad/min^2 (as in sgp4.omm)
_NDDOT_UNITS = 2985984000.0 / 2.0 / math.pi  # rev/day^3 -> rad/min^3

ELEMENT_FIELDS = ("mean_motion", "eccentricity", "inclination", "raan", "arg_perigee",
                  "mean_anomaly", "bstar", "mean_motion_dot", "mean_motion_ddot")


def utc_naive(dt):
    """UTC datetime without tzinfo (how MySQL DATETIME columns store it)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_epoch(value):
    if isinstance(value, datetime):
        return utc_naive(value)
    text = str(value).strip().replace("Z", "")
    return datetime.fromisoformat(text)


def derived_altitudes(mean_motion, eccentricity):
    """Semi-major-axis based perigee/apogee/mean altitude (km) and period (min).

    Same formula as the generated columns of current_orbit, so Python and
    MySQL always agree on which orbital region an object is in.
    """
    n_rad_s = mean_motion * 2.0 * math.pi / 86400.0
    a = (MU_EARTH / n_rad_s ** 2) ** (1.0 / 3.0)
    return {
        "semi_major_axis_km": a,
        "perigee_km": a * (1.0 - eccentricity) - EARTH_RADIUS_KM,
        "apogee_km": a * (1.0 + eccentricity) - EARTH_RADIUS_KM,
        "mean_altitude_km": a - EARTH_RADIUS_KM,
        "period_min": 1440.0 / mean_motion,
    }


def parse_omm(rec):
    """Normalise one OMM JSON record (CelesTrak numbers or Space-Track strings)."""
    def num(key, default=0.0):
        v = rec.get(key)
        return float(v) if v not in (None, "") else default

    out = {
        "norad_id": int(rec["NORAD_CAT_ID"]),
        "name": (rec.get("OBJECT_NAME") or "").strip() or f"NORAD {rec['NORAD_CAT_ID']}",
        "intl_designator": (rec.get("OBJECT_ID") or "").strip() or None,
        "epoch": parse_epoch(rec["EPOCH"]),
        "mean_motion": num("MEAN_MOTION"),
        "eccentricity": num("ECCENTRICITY"),
        "inclination": num("INCLINATION"),
        "raan": num("RA_OF_ASC_NODE"),
        "arg_perigee": num("ARG_OF_PERICENTER"),
        "mean_anomaly": num("MEAN_ANOMALY"),
        "bstar": num("BSTAR"),
        "mean_motion_dot": num("MEAN_MOTION_DOT"),
        "mean_motion_ddot": num("MEAN_MOTION_DDOT"),
        "element_set_no": int(float(rec["ELEMENT_SET_NO"])) if rec.get("ELEMENT_SET_NO") not in (None, "") else None,
        "rev_at_epoch": int(float(rec["REV_AT_EPOCH"])) if rec.get("REV_AT_EPOCH") not in (None, "") else None,
    }
    if out["mean_motion"] <= 0 or not (0 <= out["eccentricity"] < 1):
        raise ValueError(f"unusable element set for {out['norad_id']}")
    out.update(derived_altitudes(out["mean_motion"], out["eccentricity"]))
    return out


def _tle_exp(field):
    """TLE 'assumed decimal point, signed exponent' notation: ' 12345-4' -> 0.12345e-4."""
    s = field.strip()
    if not s or s in ("0", "00000-0", "00000+0", "-00000-0"):
        return 0.0
    sign = -1.0 if s[0] == "-" else 1.0
    s = s.lstrip("+-")
    mantissa, exp = s[:-2], s[-2:]
    return sign * float(f"0.{mantissa}") * 10.0 ** int(exp)


def tle_checksum_ok(line):
    digits = sum(int(c) for c in line[:68] if c.isdigit()) + line[:68].count("-")
    return len(line) >= 69 and line[68].isdigit() and digits % 10 == int(line[68])


def parse_tle(line1, line2, name=None):
    """A two-line element set as the same dict parse_omm returns (TLE fields are fixed-column)."""
    line1, line2 = line1.rstrip(), line2.rstrip()
    if not (line1.startswith("1 ") and line2.startswith("2 ")):
        raise ValueError("not a two-line element set")
    if not (tle_checksum_ok(line1) and tle_checksum_ok(line2)):
        raise ValueError("TLE checksum mismatch")
    norad = int(line1[2:7])
    if int(line2[2:7]) != norad:
        raise ValueError("TLE lines belong to different objects")
    yy, day = int(line1[18:20]), float(line1[20:32])
    epoch = datetime(2000 + yy if yy < 57 else 1900 + yy, 1, 1) + timedelta(days=day - 1.0)
    intl = line1[9:17].strip()
    intl_designator = None
    if len(intl) >= 5 and intl[:5].isdigit():
        year = int(intl[:2])
        intl_designator = f"{2000 + year if year < 57 else 1900 + year}-{intl[2:]}"
    set_no = line1[64:68].strip()
    rev = line2[63:68].strip()
    out = {
        "norad_id": norad,
        "name": (name or "").strip() or f"NORAD {norad}",
        "intl_designator": intl_designator,
        "epoch": epoch,
        "mean_motion": float(line2[52:63]),
        "eccentricity": float("0." + line2[26:33].strip()),
        "inclination": float(line2[8:16]),
        "raan": float(line2[17:25]),
        "arg_perigee": float(line2[34:42]),
        "mean_anomaly": float(line2[43:51]),
        "bstar": _tle_exp(line1[53:61]),
        "mean_motion_dot": float(line1[33:43]),
        "mean_motion_ddot": _tle_exp(line1[44:52]),
        "element_set_no": int(set_no) if set_no.isdigit() else None,
        "rev_at_epoch": int(rev) if rev.isdigit() else None,
    }
    if out["mean_motion"] <= 0 or not (0 <= out["eccentricity"] < 1):
        raise ValueError(f"unusable element set for {norad}")
    out.update(derived_altitudes(out["mean_motion"], out["eccentricity"]))
    return out


def satrec_from_elements(el):
    """SGP4 record from a dict holding norad_id, epoch and the ELEMENT_FIELDS."""
    sat = Satrec()
    epoch = (parse_epoch(el["epoch"]) - _EPOCH0).total_seconds() / 86400.0
    deg = math.pi / 180.0
    try:
        satnum = min(int(el["norad_id"]), 339999)
    except (TypeError, ValueError):
        satnum = 0          # synthetic demo objects carry a 'SYN-...' designation instead of a NORAD number
    sat.sgp4init(
        WGS72, "i", satnum,  # satnum is only a label inside SGP4
        epoch,
        float(el["bstar"]),
        float(el["mean_motion_dot"]) / _NDOT_UNITS,
        float(el["mean_motion_ddot"]) / _NDDOT_UNITS,
        float(el["eccentricity"]),
        float(el["arg_perigee"]) * deg,
        float(el["inclination"]) * deg,
        float(el["mean_anomaly"]) * deg,
        float(el["mean_motion"]) / 720.0 * math.pi,
        float(el["raan"]) * deg,
    )
    return sat


def julian(dt):
    dt = utc_naive(dt)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    return jd, fr


def time_grid(start, seconds):
    """(jd, fr) numpy arrays for start + each offset in `seconds`."""
    jd0, fr0 = julian(start)
    fr = fr0 + np.asarray(seconds, dtype=float) / 86400.0
    return np.full(fr.shape, jd0), fr


def propagate(satrecs, jd, fr):
    """TEME position/velocity (km, km/s) for many satellites at many times.

    Returns (err, r, v) with shapes (N, T), (N, T, 3), (N, T, 3); err != 0
    marks an SGP4 failure (e.g. a decayed orbit) and its r/v are NaN.
    """
    arr = SatrecArray(list(satrecs))
    return arr.sgp4(np.ascontiguousarray(jd, dtype=float), np.ascontiguousarray(fr, dtype=float))


def gmst(jd, fr):
    """Greenwich mean sidereal time (rad), IAU-82, UT1 taken as UTC."""
    t = ((jd - 2451545.0) + fr) / 36525.0
    seconds = (67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t
               + 0.093104 * t * t - 6.2e-6 * t * t * t)
    return np.mod(np.radians(seconds / 240.0), 2.0 * np.pi)


def teme_to_ecef(r, jd, fr):
    """Rotate TEME positions (..., 3) into the Earth-fixed frame (polar motion ignored)."""
    theta = gmst(jd, fr)
    c, s = np.cos(theta), np.sin(theta)
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    return np.stack([c * x + s * y, -s * x + c * y, z], axis=-1)


def ecef_to_geodetic(p):
    """WGS84 latitude/longitude (deg) and altitude (km) for ECEF km positions (..., 3)."""
    x, y, z = p[..., 0], p[..., 1], p[..., 2]
    a = EARTH_RADIUS_KM
    e2 = WGS84_F * (2 - WGS84_F)
    lon = np.arctan2(y, x)
    rho = np.hypot(x, y)
    lat = np.arctan2(z, rho * (1 - e2))
    for _ in range(5):
        n = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
        alt = rho / np.cos(lat) - n
        lat = np.arctan2(z, rho * (1 - e2 * n / (n + alt)))
    n = a / np.sqrt(1 - e2 * np.sin(lat) ** 2)
    alt = rho / np.cos(lat) - n
    return np.degrees(lat), np.degrees(lon), alt


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def minutes_from(start, minutes):
    return start + timedelta(minutes=minutes)
