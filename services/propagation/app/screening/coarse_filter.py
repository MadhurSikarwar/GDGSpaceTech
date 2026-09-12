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

    def is_candidate_pair(self, obj_a: Dict[str, Any], obj_b: Dict[str, Any]) -> bool:
        """
        Check if object A and object B have overlapping altitude bands (+/- buffer_km).
        """
        min_a, max_a = self.calculate_altitude_range(obj_a)
        min_b, max_b = self.calculate_altitude_range(obj_b)

        # Expand altitude range by buffer
        band_a = (min_a - self.buffer_km, max_a + self.buffer_km)
        band_b = (min_b - self.buffer_km, max_b + self.buffer_km)

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
        candidate_pairs = []
        for p in primaries:
            for s in secondaries:
                # Do not screen object against itself
                if p.get("catalog_id") == s.get("catalog_id"):
                    continue

                if self.is_candidate_pair(p, s):
                    candidate_pairs.append((p, s))

        return candidate_pairs
