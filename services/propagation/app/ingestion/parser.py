import math
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional


def parse_tle_epoch(tle_line1: str) -> datetime:
    """
    Parse epoch timestamp from TLE Line 1.
    Format: Columns 19-32 contain YYDDD.DDDDDDDD
    """
    epoch_str = tle_line1[18:32].strip()
    year_two_digit = int(epoch_str[:2])
    day_fraction = float(epoch_str[2:])

    # NORAD TLE year convention: 57-99 is 1957-1999, 00-56 is 2000-2056
    year = 1900 + year_two_digit if year_two_digit >= 57 else 2000 + year_two_digit

    # Calculate day of year and fraction of day
    start_of_year = datetime(year, 1, 1, tzinfo=timezone.utc)
    from datetime import timedelta
    epoch_dt = start_of_year + timedelta(days=day_fraction - 1)
    return epoch_dt


def parse_tle_orbital_elements(tle_line2: str) -> Dict[str, float]:
    """
    Extract key orbital elements from TLE Line 2 for coarse screening and metadata.
    """
    inclination_deg = float(tle_line2[8:16].strip())
    raan_deg = float(tle_line2[17:25].strip())
    eccentricity = float("0." + tle_line2[26:33].strip())
    arg_perigee_deg = float(tle_line2[34:42].strip())
    mean_anomaly_deg = float(tle_line2[43:51].strip())
    mean_motion_rev_day = float(tle_line2[52:63].strip())

    # Standard gravitational parameter for Earth mu (km^3 / s^2)
    MU_EARTH = 398600.4418
    # Convert mean motion (rev/day) to mean motion (rad/s)
    n_rad_s = (mean_motion_rev_day * 2 * math.pi) / 86400.0
    
    # Semi-major axis a = (mu / n^2)^(1/3) in km
    semi_major_axis_km = (MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0)
    
    # Earth mean equatorial radius in km
    EARTH_RADIUS_KM = 6378.137
    perigee_altitude_km = semi_major_axis_km * (1.0 - eccentricity) - EARTH_RADIUS_KM
    apogee_altitude_km = semi_major_axis_km * (1.0 + eccentricity) - EARTH_RADIUS_KM

    return {
        "inclination_deg": inclination_deg,
        "raan_deg": raan_deg,
        "eccentricity": eccentricity,
        "arg_perigee_deg": arg_perigee_deg,
        "mean_anomaly_deg": mean_anomaly_deg,
        "mean_motion_rev_day": mean_motion_rev_day,
        "semi_major_axis_km": semi_major_axis_km,
        "perigee_altitude_km": perigee_altitude_km,
        "apogee_altitude_km": apogee_altitude_km
    }


def parse_tle_pair(line1: str, line2: str, name: str = "UNKNOWN") -> Dict[str, Any]:
    """Parse TLE pair into canonical structure."""
    catalog_id = line1[2:7].strip()
    int_designator = line1[9:17].strip()
    epoch = parse_tle_epoch(line1)
    elements = parse_tle_orbital_elements(line2)

    return {
        "catalog_id": catalog_id,
        "name": name.strip(),
        "int_designator": int_designator,
        "epoch": epoch,
        "tle_line_1": line1.strip(),
        "tle_line_2": line2.strip(),
        "orbital_elements": elements
    }
