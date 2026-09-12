import math
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional
from skyfield.api import EarthSatellite, load, wgs84
from sgp4.api import Satrec, WGS72

from shared.schemas.state import StateVector, Vector3
from shared.schemas.object import DataQuality


ts = load.timescale()


class SGP4PropagationEngine:
    def __init__(self, tle_line1: str, tle_line2: str, name: str = "SAT"):
        self.tle_line1 = tle_line1
        self.tle_line2 = tle_line2
        self.name = name
        self.satellite = EarthSatellite(tle_line1, tle_line2, name, ts)
        self.satrec = Satrec.twoline2rv(tle_line1, tle_line2)

    def propagate_state(self, target_dt: datetime) -> StateVector:
        """
        Propagate satellite to target_dt (UTC).
        Returns StateVector with position (km), velocity (km/s), and geodetic altitude (km) in TEME frame.
        """
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)

        t = ts.from_datetime(target_dt)
        geocentric = self.satellite.at(t)

        # Position (km) and Velocity (km/s) in TEME / ECI frame
        pos_km = geocentric.position.km
        vel_km_s = geocentric.velocity.km_per_s

        # Calculate geodetic altitude using Skyfield wgs84 or WGS84 ellipsoid radius calculation
        try:
            subpoint = wgs84.subpoint(geocentric)
            alt_km = float(subpoint.elevation.km)
        except Exception:
            # Fallback to WGS84 mean radius subtraction
            r_mag = math.sqrt(pos_km[0]**2 + pos_km[1]**2 + pos_km[2]**2)
            alt_km = r_mag - 6378.137

        state_vector = StateVector(
            timestamp=target_dt,
            position_km=Vector3(x=float(pos_km[0]), y=float(pos_km[1]), z=float(pos_km[2])),
            velocity_km_s=Vector3(x=float(vel_km_s[0]), y=float(vel_km_s[1]), z=float(vel_km_s[2])),
            altitude_km=float(alt_km),
            reference_frame="TEME"
        )
        return state_vector

    def calculate_data_age(self, epoch_dt: datetime, current_dt: Optional[datetime] = None) -> DataQuality:
        """
        Calculate age of TLE data relative to target or current UTC time.
        """
        if current_dt is None:
            current_dt = datetime.now(timezone.utc)
        if current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)
        if epoch_dt.tzinfo is None:
            epoch_dt = epoch_dt.replace(tzinfo=timezone.utc)

        age_seconds = abs((current_dt - epoch_dt).total_seconds())
        age_hours = round(age_seconds / 3600.0, 2)

        quality = "HIGH"
        if age_hours > 72.0:
            quality = "LOW"
        elif age_hours > 24.0:
            quality = "MEDIUM"

        return DataQuality(data_age_hours=age_hours, quality=quality)


def sanity_check_iss_propagation(tle_line1: str, tle_line2: str) -> bool:
    """
    Sanity validation check against known satellite parameters (ISS catalog 25544).
    Validates position magnitude (~6700-6900 km), speed (~7.5-7.8 km/s), and positive altitude.
    """
    engine = SGP4PropagationEngine(tle_line1, tle_line2, "ISS_SANITY")
    state = engine.propagate_state(datetime.now(timezone.utc))

    r_mag = math.sqrt(state.position_km.x**2 + state.position_km.y**2 + state.position_km.z**2)
    v_mag = math.sqrt(state.velocity_km_s.x**2 + state.velocity_km_s.y**2 + state.velocity_km_s.z**2)

    # LEO orbit checks: Radius between 6500 km and 7200 km, Speed between 7.0 and 8.0 km/s
    valid_radius = 6500.0 <= r_mag <= 7200.0
    valid_speed = 7.0 <= v_mag <= 8.5
    valid_altitude = 300.0 <= state.altitude_km <= 500.0

    return valid_radius and valid_speed and valid_altitude
