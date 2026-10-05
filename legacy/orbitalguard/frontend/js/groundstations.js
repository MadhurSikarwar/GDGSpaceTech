// Pure ground-station math + API access -- no Three.js objects here.
// globe.js owns the actual cone meshes (it owns the scene graph); this
// module only computes where a station sits at a given instant and fetches
// station/pass data from the backend.
//
// Ground stations are fixed to the Earth's surface (they rotate WITH
// Earth's real rotation), while the satellite cloud lives in the true
// inertial TEME frame (see globe.js). Earth's own mesh in globe.js spins
// cosmetically (`earthGroup.rotation.y += dt*0.006`), NOT tied to real
// sidereal time -- so a station's scene position cannot be derived from that
// mesh's rotation. Instead each station's fixed geodetic lat/lon is
// converted to a true TEME position via a standard GMST (Greenwich Mean
// Sidereal Time) rotation, which is exactly the same Earth-fixed <-> TEME
// relationship SGP4 itself relies on.

const EARTH_RADIUS_KM = 6378.137;

// Standard IAU/Vallado GMST-from-Julian-Date formula, degrees. Reference:
// Vallado, "Fundamentals of Astrodynamics and Applications", eq. 3-45.
export function gmstDegrees(date) {
  const JD = date.getTime() / 86400000 + 2440587.5;
  const T = (JD - 2451545.0) / 36525.0;
  let gmst = 280.46061837
    + 360.98564736629 * (JD - 2451545.0)
    + 0.000387933 * T * T
    - (T * T * T) / 38710000.0;
  gmst = gmst % 360.0;
  if (gmst < 0) gmst += 360.0;
  return gmst;
}

// Station geodetic lat/lon/altitude -> TEME km position at `date`.
export function stationTemeKm(station, date) {
  const gmst = gmstDegrees(date);
  const lonTemeDeg = station.lon_deg + gmst; // Earth-fixed longitude -> TEME
  const latRad = (station.lat_deg * Math.PI) / 180;
  const lonRad = (lonTemeDeg * Math.PI) / 180;
  const r = EARTH_RADIUS_KM + (station.altitude_m || 0) / 1000.0;
  return {
    x: r * Math.cos(latRad) * Math.cos(lonRad),
    y: r * Math.cos(latRad) * Math.sin(lonRad),
    z: r * Math.sin(latRad),
  };
}

// Outward normal (zenith direction) at the station, in TEME at `date` --
// the cone's axis. Geocentric approximation (station position vector itself
// is "up"); fine at this visualization's altitude/radius scale.
export function stationZenithTeme(station, date) {
  const p = stationTemeKm(station, date);
  const mag = Math.hypot(p.x, p.y, p.z);
  return { x: p.x / mag, y: p.y / mag, z: p.z / mag };
}

// Is `atMinutes` (minutes since trajectory/sim epoch) inside any pass window
// for this station, given the object's generation instant? Pass timestamps
// are absolute UTC; the sim clock is generatedAtMs + atMinutes.
export function isStationActiveAt(passes, stationId, generatedAtMs, atMinutes) {
  const nowMs = generatedAtMs + atMinutes * 60000;
  return passes.some((w) => {
    if (w.station_id !== stationId) return false;
    const aos = new Date(w.aos).getTime();
    const los = new Date(w.los).getTime();
    return nowMs >= aos && nowMs <= los;
  });
}
