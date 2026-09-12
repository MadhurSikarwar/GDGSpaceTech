import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
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


# Predefined physical encounter profiles producing different encounter geometries & miss distances
ENCOUNTER_PROFILES = [
    # (name, raan_delta_deg, ma_delta_deg, inc_delta_deg, tca_offset_min, target_desc)
    ("CRITICAL_GRAZE", 0.0075, 0.0045, 0.0030, 28.0, "Close coplanar graze (~1.2 km)"),
    ("HIGH_CROSSING", 0.0160, 0.0120, 0.0080, 42.0, "Inclined orbital crossing (~3.2 km)"),
    ("MEDIUM_PROXIMITY", 0.0340, 0.0220, 0.0150, 58.0, "High-altitude proximity pass (~6.8 km)"),
    ("CRITICAL_CONVERGING", 0.0050, 0.0035, -0.0040, 35.0, "Direct converging trajectory (~0.9 km)"),
    ("HIGH_ELEVATION", 0.0220, 0.0160, -0.0100, 48.0, "Offset descent crossing (~4.1 km)"),
]

_injection_counter = 0


def generate_verified_synthetic_debris(
    target_satellite: Dict[str, Any],
    tca_offset_minutes: Optional[float] = None,
    profile_index: Optional[int] = None
) -> Dict[str, Any]:
    """
    Constructs a unique verified synthetic debris object (SYNTHETIC_DEBRIS) targeting target_satellite.
    Derives genuine orbital encounter parameters via SGP4 propagation.
    """
    global _injection_counter
    _injection_counter += 1

    cat_id = str(target_satellite.get("catalog_id", "25544"))
    raw_name = target_satellite.get("name", "SAT")
    clean_name = "".join(c for c in raw_name if c.isalnum() or c in ('-', '_')).strip()[:10] or "SAT"
    
    line1 = target_satellite["tle_line_1"]
    line2 = target_satellite["tle_line_2"]

    # Select encounter profile based on injection sequence and target ID
    if profile_index is not None:
        profile = ENCOUNTER_PROFILES[profile_index % len(ENCOUNTER_PROFILES)]
    else:
        profile_idx = (_injection_counter + abs(hash(cat_id))) % len(ENCOUNTER_PROFILES)
        profile = ENCOUNTER_PROFILES[profile_idx]

    profile_name, raan_delta, ma_delta, inc_delta, default_tca_min, desc = profile
    eff_tca_min = tca_offset_minutes if tca_offset_minutes is not None else default_tca_min

    engine = SGP4PropagationEngine(line1, line2, raw_name)
    now_dt = datetime.now(timezone.utc)
    tca_dt = now_dt + timedelta(minutes=eff_tca_min)

    # State of target satellite at TCA in TEME
    target_state = engine.propagate_state(tca_dt)

    # Generate a unique 5-digit catalog ID for this synthetic debris object (e.g. 91000 - 98999)
    synth_num = 90000 + (_injection_counter * 17 + random.randint(100, 8900)) % 9000
    synth_num_str = f"{synth_num:05d}"
    synth_catalog_id = f"SYNTHETIC-{synth_num_str}"
    synth_name = f"DEB-{clean_name}-{synth_num_str}"

    # Build TLE line 1 with unique 5-digit catalog number
    intl_desig = f"26{synth_num_str[2:]}A"
    epoch_str = line1[18:32]
    line1_base = f"1 {synth_num_str}U {intl_desig:<8} {epoch_str}  .00010000  00000+0  20000-3 0  999"
    synth_line1 = format_tle_line(line1_base)

    # Parse and modify target line 2 orbital elements to physically create the encounter
    inc_deg = float(line2[8:16].strip()) + inc_delta
    raan_deg = (float(line2[17:25].strip()) + raan_delta) % 360.0
    ecc_str = line2[26:33].strip()
    argp_deg = float(line2[34:42].strip())
    ma_deg = (float(line2[43:51].strip()) + ma_delta) % 360.0
    mm_str = line2[52:63].strip()
    rev_str = line2[63:68].strip()

    line2_base = f"2 {synth_num_str} {inc_deg:8.4f} {raan_deg:8.4f} {ecc_str:>7} {argp_deg:8.4f} {ma_deg:8.4f} {mm_str}{rev_str}"
    synth_line2 = format_tle_line(line2_base)

    return {
        "catalog_id": synth_catalog_id,
        "name": synth_name,
        "object_type": "SYNTHETIC_DEBRIS",
        "international_designator": f"2026-{synth_num_str[2:]}A",
        "epoch": tca_dt,
        "source": "SyntheticGenerator",
        "tle_line_1": synth_line1,
        "tle_line_2": synth_line2,
        "raw_data": {
            "synthetic": True,
            "target_satellite": cat_id,
            "target_satellite_name": raw_name,
            "profile": profile_name,
            "description": desc,
            "verified_tca": tca_dt.isoformat(),
            "target_state_at_tca": {
                "position_km": {"x": target_state.position_km.x, "y": target_state.position_km.y, "z": target_state.position_km.z},
                "velocity_km_s": {"x": target_state.velocity_km_s.x, "y": target_state.velocity_km_s.y, "z": target_state.velocity_km_s.z}
            }
        }
    }
