import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import requests

from services.propagation.app.config import settings
from services.propagation.app.ingestion.parser import parse_tle_epoch, parse_tle_pair

logger = logging.getLogger(__name__)


class CelesTrakIngestionClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or settings.CELESTRAK_BASE_URL
        self.cache_file = settings.CACHE_FILE

    def fetch_group_tles(self, group: str = "active") -> List[Dict[str, Any]]:
        """
        Fetch TLEs from CelesTrak for a given group (e.g. 'active', 'stations', 'last-30-days').
        If OFFLINE_MODE is set or network fails, fallback to local cached dataset.
        """
        if settings.OFFLINE_MODE:
            logger.info("OFFLINE_MODE enabled. Loading CelesTrak dataset from local cache.")
            return self.load_cached_tles()

        url = f"{self.base_url}?GROUP={group}&FORMAT=tle"
        try:
            logger.info(f"Fetching CelesTrak TLEs from {url}")
            response = requests.get(url, timeout=10)
            if response.status_code == 200 and response.text.strip():
                return self.parse_tle_response(response.text, source=f"CelesTrak:{group}")
            else:
                logger.warning(f"CelesTrak request failed with status {response.status_code}. Falling back to cache.")
                return self.load_cached_tles()
        except Exception as e:
            logger.warning(f"Network error accessing CelesTrak ({e}). Falling back to local cache.")
            return self.load_cached_tles()

    def parse_tle_response(self, text_data: str, source: str = "CelesTrak") -> List[Dict[str, Any]]:
        """
        Parse 3-line or 2-line TLE response text into records.
        """
        lines = [l.strip() for l in text_data.splitlines() if l.strip()]
        results = []
        i = 0
        while i < len(lines):
            # Check for 3-line TLE (Line 0 = Name, Line 1, Line 2)
            if i + 2 < len(lines) and lines[i+1].startswith("1 ") and lines[i+2].startswith("2 "):
                name = lines[i]
                line1 = lines[i+1]
                line2 = lines[i+2]
                i += 3
            # Check for 2-line TLE (Line 1, Line 2 without Name line)
            elif i + 1 < len(lines) and lines[i].startswith("1 ") and lines[i+1].startswith("2 "):
                name = f"CAT-{lines[i][2:7].strip()}"
                line1 = lines[i]
                line2 = lines[i+1]
                i += 2
            else:
                i += 1
                continue

            try:
                parsed = parse_tle_pair(line1, line2, name)
                
                # Determine object type heuristic based on name/ID or source
                obj_type = "SATELLITE"
                if "DEB" in name.upper() or "DEBRIS" in name.upper():
                    obj_type = "DEBRIS"
                elif "R/B" in name.upper() or "ROCKET" in name.upper():
                    obj_type = "ROCKET_BODY"

                results.append({
                    "catalog_id": parsed["catalog_id"],
                    "name": parsed["name"],
                    "object_type": obj_type,
                    "international_designator": parsed["int_designator"],
                    "epoch": parsed["epoch"],
                    "source": source,
                    "tle_line_1": parsed["tle_line_1"],
                    "tle_line_2": parsed["tle_line_2"],
                    "raw_data": {
                        "name": parsed["name"],
                        "tle_line1": parsed["tle_line_1"],
                        "tle_line2": parsed["tle_line_2"],
                        "orbital_elements": parsed["orbital_elements"]
                    }
                })
            except Exception as ex:
                logger.debug(f"Skipping malformed TLE block for {name}: {ex}")

        return results

    def load_cached_tles(self) -> List[Dict[str, Any]]:
        """
        Load offline cached TLE dataset from json file.
        """
        if not self.cache_file.exists():
            logger.error(f"Cache file not found at {self.cache_file}")
            return []

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            results = []
            for item in data:
                line1 = item.get("TLE_LINE1")
                line2 = item.get("TLE_LINE2")
                name = item.get("OBJECT_NAME", "UNKNOWN")
                cat_id = item.get("NORAD_CAT_ID", "00000")
                obj_type = item.get("OBJECT_TYPE", "SATELLITE")

                if line1 and line2:
                    epoch = parse_tle_epoch(line1)
                    parsed = parse_tle_pair(line1, line2, name)
                    results.append({
                        "catalog_id": cat_id,
                        "name": name,
                        "object_type": obj_type,
                        "international_designator": item.get("INTLDES", ""),
                        "epoch": epoch,
                        "source": "CelesTrakCache",
                        "tle_line_1": line1,
                        "tle_line_2": line2,
                        "raw_data": item
                    })
            return results
        except Exception as e:
            logger.error(f"Failed loading cache file: {e}")
            return []
