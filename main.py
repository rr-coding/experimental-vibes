import json
import logging
from agents.scraper import ScraperAgent
from agents.lulworth_scraper import LulworthScraperAgent
from agents.olf_scraper import OLFScraperAgent
from agents.firing_notice_scraper import FiringNoticeScraperAgent
from agents.geocoder import GeocoderAgent
from agents.map_builder import MapBuilderAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    logging.info("=== Pipeline Starting ===")

    locations = ScraperAgent().run()
    locations.append(LulworthScraperAgent().run())
    locations.extend(OLFScraperAgent().run())
    locations.extend(FiringNoticeScraperAgent().run())
    with open("data/raw_locations.json", "w") as f:
        json.dump(locations, f, indent=2)
    logging.info("Merged all sources into data/raw_locations.json (%d total locations)", len(locations))

    GeocoderAgent().run()
    MapBuilderAgent().run()
    logging.info("=== Pipeline Complete ===")


if __name__ == "__main__":
    main()
