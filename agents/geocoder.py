import json
import logging
import time
import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "military-ranges-map/1.0 (educational demo)"}


class GeocoderAgent:
    def _geocode(self, name):
        params = {"q": f"{name}, UK", "format": "json", "limit": 1, "countrycodes": "gb"}
        response = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=10)
        response.raise_for_status()
        results = response.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
        return None, None

    def run(self):
        logger.info("[GeocoderAgent] Starting")

        with open("data/raw_locations.json") as f:
            locations = json.load(f)

        geocoded = []
        failed = []

        for loc in locations:
            name = loc["name"]
            if loc.get("lat") is not None:
                logger.info(f"  Skipping (pre-geocoded): {name} ({loc['lat']:.4f}, {loc['lon']:.4f})")
                geocoded.append(loc)
                continue
            logger.info(f"  Geocoding: {name}")
            try:
                lat, lon = self._geocode(name)
                if lat is not None:
                    logger.info(f"    -> ({lat:.4f}, {lon:.4f})")
                    geocoded.append({**loc, "lat": lat, "lon": lon})
                else:
                    logger.warning(f"    -> No result found for '{name}'")
                    geocoded.append({**loc, "lat": None, "lon": None})
                    failed.append(name)
            except Exception as e:
                logger.error(f"    -> Error geocoding '{name}': {e}")
                geocoded.append({**loc, "lat": None, "lon": None})
                failed.append(name)
            time.sleep(1)

        output_path = "data/geocoded_locations.json"
        with open(output_path, "w") as f:
            json.dump(geocoded, f, indent=2)

        logger.info(f"[GeocoderAgent] Done — {len(geocoded) - len(failed)}/{len(geocoded)} geocoded successfully")
        if failed:
            logger.warning(f"  Failed: {', '.join(failed)}")

        return geocoded
