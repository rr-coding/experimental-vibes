import logging
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Lulworth Camp / Ranges coordinates (Dorset)
LULWORTH_LAT = 50.6412
LULWORTH_LON = -2.2327

MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def _parse_time(raw):
    """Convert '9:30am', '5pm', '11:59pm' to 'HH:MM'. Returns '' if unrecognised."""
    t = raw.strip().lower()
    match = re.match(r'^(\d+)(?::(\d+))?\s*(am|pm)$', t)
    if not match:
        return ''
    h, m, meridiem = match.groups()
    h, m = int(h), int(m or 0)
    if meridiem == 'pm' and h != 12:
        h += 12
    if meridiem == 'am' and h == 12:
        h = 0
    return f'{h:02d}:{m:02d}'


def _parse_firing_text(text):
    """
    Parse a cell like '9:30am to 5pm and 8pm to 11:59pm (see note 1)'
    into a list of (open, close) tuples.
    Returns [] for 'No firing' or empty.
    """
    # Strip footnote references like "(see note 1)"
    text = re.sub(r'\(see note[^)]*\)', '', text, flags=re.IGNORECASE).strip()
    if not text or text.lower() in ('no firing', 'closed', ''):
        return []

    windows = []
    # Split sessions on " and "
    for session in re.split(r'\s+and\s+', text, flags=re.IGNORECASE):
        session = session.strip()
        # Split on " to " (GOV.UK style)
        parts = re.split(r'\s+to\s+', session, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            open_t = _parse_time(parts[0])
            close_t = _parse_time(parts[1])
            if open_t and close_t:
                windows.append((open_t, close_t))
    return windows


def _parse_heading_start_date(heading_text, default_year):
    """
    Extract the first date from headings like:
      'Firing 2 March to 8 March' or '30 March to 5 April'
    Returns a datetime or None.
    """
    text = heading_text.lower()
    # Find "NN MonthName" pattern
    match = re.search(r'(\d+)\s+(' + '|'.join(MONTH_NAMES.keys()) + r')', text)
    if match:
        day = int(match.group(1))
        month = MONTH_NAMES[match.group(2)]
        return datetime(default_year, month, day)
    return None


class LulworthScraperAgent:
    URL = "https://www.gov.uk/government/publications/lulworth-firing-notice/lulworth-march-2026-firing-times"
    YEAR = 2026
    NOTES_LABEL = "March 2026 firing times"
    # Row label identifying the Lulworth main ranges (case-insensitive substring)
    RANGE_ROW_LABEL = "lulworth range"

    def run(self):
        logger.info("[LulworthScraperAgent] Starting — fetching %s", self.URL)
        response = requests.get(self.URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        content = soup.find("div", class_="govspeak") or soup.find("main") or soup

        schedule = []
        current_start_date = None

        # Walk through all elements, tracking headings and tables in order
        for el in content.find_all(["h2", "h3", "h4", "table"]):
            if el.name in ("h2", "h3", "h4"):
                heading_text = el.get_text(strip=True)
                parsed = _parse_heading_start_date(heading_text, self.YEAR)
                if parsed:
                    current_start_date = parsed
                    logger.debug("[LulworthScraperAgent] Section start date: %s (%s)", parsed.date(), heading_text)
                continue

            # It's a table — try to parse it
            if current_start_date is None:
                continue

            sessions = self._parse_week_table(el, current_start_date)
            schedule.extend(sessions)

        location = {
            "name": "Lulworth",
            "schedule": schedule,
            "lat": LULWORTH_LAT,
            "lon": LULWORTH_LON,
        }

        logger.info("[LulworthScraperAgent] Done — %d firing session(s) parsed", len(schedule))
        return location

    def _parse_week_table(self, table, start_date):
        """
        Parse a weekly table where:
          - First column is 'Area'
          - Remaining columns are 'DayName N' (e.g. 'Monday 2')
          - Rows are different areas; we want the 'Lulworth Ranges' row
        Headers may be in <thead> or in the first row of <tbody>.
        Returns a list of schedule entry dicts.
        """
        tbody = table.find("tbody")
        if not tbody:
            return []
        all_rows = tbody.find_all("tr")
        if not all_rows:
            return []

        # Check for a proper <thead>; fall back to using first tbody row as header
        thead = table.find("thead")
        if thead:
            header_cells = thead.find_all(["th", "td"])
            data_rows = all_rows
        else:
            header_cells = all_rows[0].find_all(["th", "td"])
            data_rows = all_rows[1:]

        if not header_cells:
            return []

        # Build a map of column index → date
        col_dates = {}
        prev_day_num = None
        current_date = start_date

        for i, cell in enumerate(header_cells):
            text = cell.get_text(strip=True)
            # Look for a day number in the header (e.g. "Monday 2" → 2)
            day_match = re.search(r'\b(\d{1,2})\b', text)
            if not day_match:
                continue
            day_num = int(day_match.group(1))

            # Detect month rollover (day number decreases)
            if prev_day_num is not None and day_num < prev_day_num:
                # Advance current_date to first day of next month with this day number
                # Add enough days to get past month end
                current_date = current_date.replace(day=1)
                # Move to next month
                if current_date.month == 12:
                    current_date = current_date.replace(year=current_date.year + 1, month=1)
                else:
                    current_date = current_date.replace(month=current_date.month + 1)
                current_date = current_date.replace(day=day_num)
            else:
                try:
                    current_date = current_date.replace(day=day_num)
                except ValueError:
                    pass

            col_dates[i] = current_date
            prev_day_num = day_num

        if not col_dates:
            logger.debug("[LulworthScraperAgent] No date columns found in table starting %s", start_date.date())
            return []

        # Find the 'Lulworth Ranges' row
        sessions = []
        for tr in data_rows:
            cells = tr.find_all(["th", "td"])
            if not cells:
                continue
            row_label = cells[0].get_text(strip=True).lower()
            if self.RANGE_ROW_LABEL not in row_label:
                continue

            # Found the right row — process each date column
            for col_idx, col_date in col_dates.items():
                if col_idx >= len(cells):
                    continue
                cell_text = cells[col_idx].get_text(strip=True)
                windows = _parse_firing_text(cell_text)
                for open_t, close_t in windows:
                    suffix = " (night)" if open_t >= "20:00" else ""
                    sessions.append({
                        "date": col_date.strftime("%Y-%m-%d"),
                        "day": col_date.strftime("%-d %b") + suffix,
                        "open": open_t,
                        "close": close_t,
                        "notes": self.NOTES_LABEL,
                    })
            break  # Only need the Lulworth Ranges row

        return sessions
