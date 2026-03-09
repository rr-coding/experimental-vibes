import json
import logging
import re
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
    "thur": "Thursday", "thu": "Thursday", "fri": "Friday",
    "sat": "Saturday", "sun": "Sunday",
}


def _parse_time(raw):
    """Convert '9am', '4.30pm', '10pm', 'midday', 'noon' to 'HH:MM'. Returns '' if unrecognised."""
    t = raw.strip().lower()
    if t in ("midday", "noon"):
        return "12:00"
    is_pm = t.endswith("pm")
    is_am = t.endswith("am")
    t = t.replace("pm", "").replace("am", "")
    try:
        if "." in t:
            h, m = t.split(".")
            h, m = int(h), int(m)
        else:
            h, m = int(t), 0
    except ValueError:
        return raw  # return as-is if we can't parse
    if is_pm and h != 12:
        h += 12
    if is_am and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def _full_day(abbr):
    return DAY_NAMES.get(abbr.strip().lower(), abbr)


def _parse_table(table, season_note):
    """Parse a schedule table, returning a list of day entries."""
    entries = []
    tbody = table.find("tbody")
    if not tbody:
        return entries

    for tr in tbody.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue

        day_raw = cells[0].get_text(strip=True)
        times_raw = cells[1].get_text(strip=True)

        # "Weekend" row → expand to Saturday and Sunday
        if day_raw.lower() == "weekend":
            day_list = ["Saturday", "Sunday"]
        else:
            day_list = [_full_day(day_raw)]

        closed = times_raw.lower() in ("closed", "")
        if closed:
            open_t, close_t = "", ""
        else:
            parts = re.split(r"\s+to\s+", times_raw, flags=re.IGNORECASE)
            if len(parts) == 2:
                open_t = _parse_time(parts[0])
                close_t = _parse_time(parts[1])
            else:
                open_t, close_t = times_raw, ""

        for day in day_list:
            entries.append({"day": day, "open": open_t, "close": close_t, "notes": season_note})

    return entries


class ScraperAgent:
    URL = "https://www.gov.uk/government/publications/military-low-flying-air-weapons-ranges-activity/air-weapons-ranges-normal-opening-times"
    SKIP_HEADINGS = {"contents", "range activity times", "safety guide"}

    def run(self):
        logger.info("[ScraperAgent] Starting")

        response = requests.get(self.URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Scope to the main GOV.UK content wrapper to avoid nav/footer <h2> tags
        content = soup.find("div", class_="govspeak")
        if not content:
            content = soup.find("main") or soup
            logger.warning("[ScraperAgent] Could not find .govspeak — falling back to <main>")

        h2_tags = [h for h in content.find_all("h2")
                   if h.get_text(strip=True).lower() not in self.SKIP_HEADINGS]

        logger.info(f"[ScraperAgent] Found {len(h2_tags)} range heading(s)")

        locations = []
        for h2 in h2_tags:
            raw_name = h2.get_text(strip=True)
            # Strip parentheticals: "Cape Wrath (including Garvie Island)" → "Cape Wrath"
            name = re.sub(r"\s*\(.*?\)", "", raw_name).strip()

            # Collect all sibling elements until the next h2
            siblings = []
            for sib in h2.find_next_siblings():
                if sib.name == "h2":
                    break
                siblings.append(sib)

            schedule = []
            current_season = ""
            parsed_any_table = False

            for el in siblings:
                if el.name in ("h3", "h4"):
                    current_season = el.get_text(strip=True)

                elif el.name == "p":
                    text = el.get_text(strip=True)
                    if not text:
                        continue
                    if text.endswith(":"):
                        # Treat as a season header
                        current_season = text.rstrip(":")
                    else:
                        # Informational note (e.g. Cape Wrath's "No fixed activity hours…")
                        schedule.append({"day": "", "open": "", "close": "", "notes": text})

                elif el.name == "table":
                    rows = _parse_table(el, current_season)
                    if rows:
                        schedule.extend(rows)
                        parsed_any_table = True
                    else:
                        logger.warning(f"  [{name}] Table found but yielded no rows")

            if not parsed_any_table and not schedule:
                logger.warning(f"  [{name}] Could not parse any schedule — including with empty schedule")

            locations.append({"name": name, "schedule": schedule})
            logger.info(f"  [{name}] {len(schedule)} schedule entries")

        output_path = "data/raw_locations.json"
        with open(output_path, "w") as f:
            json.dump(locations, f, indent=2)

        logger.info(f"[ScraperAgent] Done — {len(locations)} ranges saved to {output_path}")
        return locations
