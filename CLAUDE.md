# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A multi-agent pipeline that scrapes, geocodes, and visualizes UK military ranges with real-time activity schedules. The output is a static web app deployable to GitHub Pages, styled with the GOV.UK Design System.

## Running the Project

**Full pipeline (fetches live data, geocodes, and updates the static app):**
```bash
python3 main.py
```

**Run individual agents manually:**
```bash
python3 -c "from agents.scraper import ScraperAgent; ScraperAgent().run()"
python3 -c "from agents.lulworth_scraper import LulworthScraperAgent; LulworthScraperAgent().run()"
python3 -c "from agents.olf_scraper import OLFScraperAgent; OLFScraperAgent().run()"
python3 -c "from agents.firing_notice_scraper import FiringNoticeScraperAgent; FiringNoticeScraperAgent().run()"
python3 -c "from agents.geocoder import GeocoderAgent; GeocoderAgent().run()"
python3 -c "from agents.map_builder import MapBuilderAgent; MapBuilderAgent().run()"
```

**Legacy Flask app (no longer primary):**
```bash
python3 app.py  # Serves on http://localhost:5000
```

**Static app:** Open `docs/index.html` directly in a browser — no server needed.

There is no test suite. Functional validation: enter postcode `LN12 2QP` (near Wainfleet range) and verify a RED/AMBER status appears with a distance line on the map. For Lulworth, use a Dorset postcode (e.g. `BH20 5QS`) on a day with scheduled firing.

## Architecture

```
ScraperAgent              →  data/raw_locations.json  (air weapons ranges, day-of-week schedules)
LulworthScraperAgent       ↘
OLFScraperAgent            → merged into data/raw_locations.json  (date-specific entries)
FiringNoticeScraperAgent   ↗
GeocoderAgent         →  data/geocoded_locations.json
MapBuilderAgent       →  templates/map.html (Jinja2/Flask template, not used by static app)
                      →  docs/index.html (inline data updated in-place)
                      →  docs/data/geocoded_locations.json (copy)
```

The **live deployment** is `docs/index.html` — a fully self-contained static HTML file that:
- Embeds the geocoded data inline as a JS constant (updated automatically by `MapBuilderAgent`)
- Fetches postcode coordinates from `api.postcodes.io`
- Renders an interactive map with Leaflet.js
- Calculates distances with the Haversine formula client-side
- Uses the GOV.UK Design System (govuk-frontend@6.1.0 via unpkg CDN) for all UI chrome
- Shows status via GOV.UK notification banners: green (success) / blue (amber) / blue+warning-icon (red)

`app.py` is a legacy Flask backend that duplicates this logic server-side; it is not the current deployment target.

## Key Implementation Details

- **Geocoding:** Nominatim (OSM) API with a mandatory 1-second delay per request (rate limit). Entries with `lat` already set (e.g. Lulworth) are skipped.
- **Schedule parsing — air weapons ranges:** `agents/scraper.py` parses GOV.UK HTML tables with day-of-week schedules and seasonal notes (May–Aug = summer/BST, Sept–Apr = winter/GMT).
- **Schedule parsing — Lulworth firing notices:** `agents/lulworth_scraper.py` fetches the monthly firing notice page. Tables are date-columned (columns = days, rows = areas). The "Lulworth Ranges" row is extracted; multiple firing windows per day (e.g. day + night) produce separate schedule entries with `"date": "YYYY-MM-DD"`.
- **Date-specific vs day-of-week schedules:** Entries with a `date` field are matched by exact ISO date in the JS (`getTodaysSessions`). Entries without `date` use day-name + season matching as before. Multiple sessions on the same date are all returned.
- **`docs/index.html` update:** `MapBuilderAgent` rewrites the `const locations = [...];` line in `docs/index.html` in-place using a regex, so running the pipeline is sufficient to update the static app — no manual copy step needed.
- **Lulworth coordinates:** Hardcoded at `50.6412, -2.2327` (Lulworth Camp, Dorset) in `agents/lulworth_scraper.py`.
- **OLF scraper:** `agents/olf_scraper.py` fetches the index page at `https://www.gov.uk/government/publications/operational-low-flying-training-timetable`, follows all linked weekly timetable documents, and parses a single table per document (columns = 3 TTAs, rows = Mon–Fri). The week start date is parsed from the page h1. Only entries from today onwards are included. Locations are only added when at least one day shows non-"No activity" text. Activity times are parsed if present; when only "Activity" appears (no times), `open` is set to the sentinel string `"Activity"` and `close` to `""`. The JS `buildScheduleHtml` renders this as just `"Activity"` with no dash. Hardcoded TTA coordinates: Central Wales (52.20, -3.60), Northern Scotland (57.50, -4.20), Borders (55.30, -2.80).
- **Firing notice scraper:** `agents/firing_notice_scraper.py` handles all remaining firing ranges from `https://www.gov.uk/government/collections/firing-notice` (excluding Lulworth). It fetches the collections index, follows each publication link, navigates sub-document links where needed, and parses firing timetable tables. Column headers are matched against a `RANGE_COORDS` dict (longest-key-first substring matching) to assign coordinates and names. Falls back to the document title for generic column headers like "Timings". Handles both `<thead>` and first-tbody-row table formats, multiple sessions per day, "Day"/"Night"/"Day and night" sentinels, and wrong-year correction in SPTA documents. 0.4s delay between requests. Only adds a location if it has ≥1 entry on or after today. Hardcoded coordinates for ~22 ranges; skips the `lulworth-firing-notice` publication slug.
- **`re.sub` replacement safety:** `MapBuilderAgent` uses a lambda (`lambda m: replacement`) rather than a plain string as the `re.sub` replacement, so unicode escapes in JSON (e.g. `\u2013`) are not misinterpreted as regex escape sequences.
- **Source URLs:**
  - Air weapons ranges: `https://www.gov.uk/government/publications/military-low-flying-air-weapons-ranges-activity/...`
  - Lulworth firing notice: `https://www.gov.uk/government/publications/lulworth-firing-notice/lulworth-march-2026-firing-times`
  - OLF timetable: `https://www.gov.uk/government/publications/operational-low-flying-training-timetable`
  - Firing notices collections: `https://www.gov.uk/government/collections/firing-notice`
  - WebFetch access is pre-approved in `.claude/settings.local.json`.

## Dependencies

No `requirements.txt` exists. Dependencies are: `flask`, `requests`, `beautifulsoup4`. Install via:
```bash
pip install flask requests beautifulsoup4
```
