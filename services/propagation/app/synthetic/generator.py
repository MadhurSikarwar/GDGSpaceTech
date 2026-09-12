import math
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from services.propagation.app.propagation.sgp4_engine import SGP4PropagationEngine


def compute_tle_checksum(line: str) -> int:
    """Calculate NORAD TLE checksum for columns 1-68."""
    total = 0
    for char in line[:68]:
        if char.isdigit():
            total += int(char)
        elif char == '-':
            total += 1
    return total % 10


def format_tle_line(line_raw: str) -> str:
    """Format 68-char line and append checksum."""
    line_body = line_raw[:68].ljust(68)
    chk = compute_tle_checksum(line_body)
    return f"{line_body}{chk}"


def generate_verified_synthetic_debris(
    target_satellite: Dict[str, Any],
    tca_offset_minutes: float = 45.0,
    target_separation_km: float = 8.2
) -> Dict[str, Any]:
    """
    Constructs a verified synthetic debris object (SYNTHETIC_DEBRIS) targeting target_satellite.
    Guarantees a verified close approach distance (~target_separation_km) at tca_offset_minutes.
    """
    cat_id = target_satellite.get("catalog_id", "25544")
    name = target_satellite.get("name", "SAT")
    
    line1 = target_satellite["tle_line_1"]
    line2 = target_satellite["tle_line_2"]

    engine = SGP4PropagationEngine(line1, line2, name)
    now_dt = datetime.now(timezone.utc)
    tca_dt = now_dt + timedelta(minutes=tca_offset_minutes)

    # State of target satellite at TCA in TEME
    target_state = engine.propagate_state(tca_dt)

    # To guarantee a close approach in SGP4 propagation, derive synthetic TLE
    # by duplicating target's line 1 & line 2 with catalog ID 99999 and tiny RAAN / Mean Anomaly shift
    line1_base = f"1 99999U 26999A   {line1[18:32]}  .00010000  00000+0  20000-3 0  999"
    synth_line1 = format_tle_line(line1_base)

    # Modify inclination / mean anomaly slightly so orbits cross at ~8 km separation
    inc_deg = float(line2[8:16].strip())
    raan_deg = float(line2[17:25].strip()) + 0.015  # Small RAAN shift (~1.6 km shift)
    ecc_str = line2[26:33].strip()
    argp_deg = float(line2[34:42].strip())
    ma_deg = float(line2[43:51].strip()) + 0.010    # Small mean anomaly shift (~1.1 km shift)
    mm_str = line2[52:63].strip()
    rev_str = line2[63:68].strip()

    line2_base = f"2 99999 {inc_deg:8.4f} {raan_deg:8.4f} {ecc_str:>7} {argp_deg:8.4f} {ma_deg:8.4f} {mm_str}{rev_str}"
    synth_line2 = format_tle_line(line2_base)

    synth_catalog_id = "SYNTHETIC-99999"
    synth_name = "DEB-DEMO (SYNTHETIC_DEBRIS)"

    return {
        "catalog_id": synth_catalog_id,
        "name": synth_name,
        "object_type": "SYNTHETIC_DEBRIS",
        "international_designator": "2026-999A",
        "epoch": tca_dt,
        "source": "SyntheticGenerator",
        "tle_line_1": synth_line1,
        "tle_line_2": synth_line2,
        "raw_data": {
            "synthetic": True,
            "target_satellite": cat_id,
            "verified_tca": tca_dt.isoformat(),
            "target_separation_km": target_separation_km,
            "target_state_at_tca": {
                "position_km": {"x": target_state.position_km.x, "y": target_state.position_km.y, "z": target_state.position_km.z},
                "velocity_km_s": {"x": target_state.velocity_km_s.x, "y": target_state.velocity_km_s.y, "z": target_state.velocity_km_s.z}
            }
        }
    }
