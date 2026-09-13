from typing import List, Tuple, Dict, Any, Optional
from services.propagation.app.config import settings
from services.propagation.app.ingestion.parser import parse_tle_orbital_elements


class CoarseFilter:
    def __init__(self, buffer_km: Optional[float] = None):
        self.buffer_km = buffer_km or settings.COARSE_ALTITUDE_BUFFER_KM

    def calculate_altitude_range(self, obj: Dict[str, Any]) -> Tuple[float, float]:
        """
        Extract or estimate (perigee_altitude_km, apogee_altitude_km) for an object.
        """
        line2 = obj.get("tle_line_2")
        if line2 and len(line2) >= 68:
            try:
                elements = parse_tle_orbital_elements(line2)
                return (elements["perigee_altitude_km"], elements["apogee_altitude_km"])
            except Exception:
                pass

        # Fallback to current snapshot state altitude if available
        state = obj.get("state")
        if state and hasattr(state, "altitude_km"):
            alt = state.altitude_km
            return (alt - 10.0, alt + 10.0)

        # Default fallback altitude band
        return (400.0, 500.0)

    def _expanded_band(self, obj: Dict[str, Any]) -> Tuple[float, float]:
        """Buffered (min, max) altitude band for one object -- the part of
        is_candidate_pair that's actually expensive (a TLE reparse), and the
        only part that doesn't depend on which pair is being checked."""
        min_a, max_a = self.calculate_altitude_range(obj)
        return (min_a - self.buffer_km, max_a + self.buffer_km)

    def is_candidate_pair(self, obj_a: Dict[str, Any], obj_b: Dict[str, Any]) -> bool:
        """
        Check if object A and object B have overlapping altitude bands (+/- buffer_km).
        """
        band_a = self._expanded_band(obj_a)
        band_b = self._expanded_band(obj_b)

        # Overlap condition: max(min_a, min_b) <= min(max_a, max_b)
        overlap = max(band_a[0], band_b[0]) <= min(band_a[1], band_b[1])
        return overlap

    def filter_pairs(
        self,
        primaries: List[Dict[str, Any]],
        secondaries: List[Dict[str, Any]]
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """
        Perform coarse filtering over primary objects (e.g. satellites) vs secondary objects (e.g. debris).
        Returns list of candidate pairs (primary, secondary).
        """
        # A band only depends on the object itself, not on which pairing is
        # being tested -- precompute each object's band (and catalog_id) once
        # here instead of inside the P*S loop below. Calling is_candidate_pair
        # there instead re-parses both objects' TLEs on every single pairing:
        # against the live catalog (~16k primaries x ~150 secondaries) that
        # was ~5M redundant TLE reparses and the entire cost of a /screen
        # call (90s+, regularly exceeding the frontend's fetch timeout).
        primary_bands = [self._expanded_band(p) for p in primaries]
        primary_ids = [p.get("catalog_id") for p in primaries]
        secondary_bands = [self._expanded_band(s) for s in secondaries]
        secondary_ids = [s.get("catalog_id") for s in secondaries]

        candidate_pairs = []
        for p, band_p, p_id in zip(primaries, primary_bands, primary_ids):
            for s, band_s, s_id in zip(secondaries, secondary_bands, secondary_ids):
                # Do not screen object against itself
                if p_id == s_id:
                    continue

                if max(band_p[0], band_s[0]) <= min(band_p[1], band_s[1]):
                    candidate_pairs.append((p, s))

        return candidate_pairs
