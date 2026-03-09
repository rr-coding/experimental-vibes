import logging
import re
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gov.uk"
INDEX_URL = (
    "https://www.gov.uk/government/publications/"
    "operational-low-flying-training-timetable"
)

MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

WEEKDAY_OFFSET = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2,
    'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6,
}

# Approximate centre-point coordinates for each Tactical Training Area
TTA_INFO = {
    'wales':    (52.20, -3.60, 'Low Flying Area: Central Wales'),
    'scotland': (57.50, -4.20, 'Low Flying Area: Northern Scotland'),
    'border':   (55.30, -2.80, 'Low Flying Area: Borders'),
}

NOTES_LABEL = "Upcoming low flying training"


def _tta_key(col_header):
    """Map a column header string to a TTA_INFO key, or None if unrecognised."""
    h = col_header.lower()
    if 'wales' in h:
        return 'wales'
    if 'border' in h or ('scotland' in h and 'southern' in h):
        return 'border'
    if 'scotland' in h:
        return 'scotland'
    return None


def _parse_week_start(title):
    """
    Extract the Monday start date from a title such as:
      'Operational Low Flying Training Timetable 2 to 6 March 2026'
      'Operational Low Flying Training Timetable 27 to 31 October 2025'
    Returns a date object or None.
    """
    t = title.lower()
    month_pat = '|'.join(MONTH_NAMES.keys())

    # Pattern 1: "N to N MonthName YYYY"  (same month)
    m = re.search(
        rf'(\d+)\s+to\s+\d+\s+({month_pat})\s+(\d{{4}})', t
    )
    if m:
        day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
        try:
            return date(year, MONTH_NAMES[month_str], day)
        except ValueError:
            pass

    # Pattern 2: "N MonthName to N MonthName YYYY"  (cross-month)
    m = re.search(
        rf'(\d+)\s+({month_pat})\s+to\s+\d+\s+\w+\s+(\d{{4}})', t
    )
    if m:
        day, month_str, year = int(m.group(1)), m.group(2), int(m.group(3))
        try:
            return date(year, MONTH_NAMES[month_str], day)
        except ValueError:
            pass

    return None


def _parse_activity(cell_text):
    """
    Parse a table cell.
    Returns (open_str, close_str) or None if no activity.
    open_str may be 'Activity' when times are unspecified.
    """
    t = cell_text.strip()
    if not t or t.lower() == 'no activity':
        return None

    # Try military-style "HHMM-HHMM" or "HH:MM to HH:MM"
    m = re.search(
        r'(\d{3,4})(?::(\d{2}))?\s*(?:[-\u2013]|to)\s*(\d{3,4})(?::(\d{2}))?',
        t, re.IGNORECASE,
    )
    if m:
        def hhmm(raw, mins):
            raw = raw.zfill(4)
            h, mn = int(raw[:2]), int(raw[2:] if not mins else mins)
            return f'{h:02d}:{mn:02d}'
        return hhmm(m.group(1), m.group(2)), hhmm(m.group(3), m.group(4))

    tl = t.lower()
    if tl == 'am':
        return '09:00', '12:00'
    if tl == 'pm':
        return '13:00', '17:00'
    if 'all day' in tl:
        return '09:00', '17:00'

    # Activity confirmed, times unknown — use sentinel value
    return 'Activity', ''


class OLFScraperAgent:
    def run(self):
        today = date.today()
        logger.info("[OLFScraperAgent] Starting (today = %s)", today)

        doc_urls = self._get_document_urls()
        logger.info("[OLFScraperAgent] Found %d timetable document(s)", len(doc_urls))

        # Accumulate entries per TTA key
        tta_entries = {k: [] for k in TTA_INFO}

        for url in doc_urls:
            self._scrape_timetable(url, today, tta_entries)

        # Build location objects for TTAs that have at least one upcoming entry
        locations = []
        for key, entries in tta_entries.items():
            if not entries:
                continue
            lat, lon, name = TTA_INFO[key]
            locations.append({
                'name': name,
                'schedule': entries,
                'lat': lat,
                'lon': lon,
            })
            logger.info(
                "[OLFScraperAgent]   %s — %d session(s)", name, len(entries)
            )

        logger.info(
            "[OLFScraperAgent] Done — %d TTA(s) with upcoming activity",
            len(locations),
        )
        return locations

    # ── helpers ────────────────────────────────────────────────────────────

    def _get_document_urls(self):
        r = requests.get(INDEX_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        seen, urls = set(), []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/operational-low-flying-training-timetable/' in href:
                full = (BASE_URL + href) if href.startswith('/') else href
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        return urls

    def _scrape_timetable(self, url, today, tta_entries):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            r.raise_for_status()
        except Exception as e:
            logger.warning("[OLFScraperAgent] Failed to fetch %s: %s", url, e)
            return

        soup = BeautifulSoup(r.text, 'html.parser')

        # Week start from h1 title
        h1 = soup.find('h1')
        if not h1:
            logger.warning("[OLFScraperAgent] No h1 at %s", url)
            return
        title = h1.get_text(strip=True)
        week_start = _parse_week_start(title)
        if not week_start:
            logger.warning(
                "[OLFScraperAgent] Could not parse date from '%s'", title
            )
            return

        week_end = week_start + timedelta(days=6)
        if week_end < today:
            logger.info(
                "[OLFScraperAgent] Skipping past week: %s", title
            )
            return

        logger.info("[OLFScraperAgent] Parsing: %s", title)

        content = soup.find('div', class_='govspeak') or soup.find('main') or soup
        table = content.find('table')
        if not table:
            logger.warning("[OLFScraperAgent] No table at %s", url)
            return

        rows = table.find_all('tr')
        if not rows:
            return

        header_cells = rows[0].find_all(['th', 'td'])
        col_keys = []
        for cell in header_cells:
            col_keys.append(_tta_key(cell.get_text(strip=True)))

        for row in rows[1:]:
            cells = row.find_all(['th', 'td'])
            if not cells:
                continue

            day_name = cells[0].get_text(strip=True).lower()
            offset = WEEKDAY_OFFSET.get(day_name)
            if offset is None:
                continue

            row_date = week_start + timedelta(days=offset)
            if row_date < today:
                continue

            date_str = row_date.strftime('%Y-%m-%d')
            date_display = row_date.strftime('%-d %b')

            for i, key in enumerate(col_keys):
                if key is None or i >= len(cells):
                    continue
                cell_text = cells[i].get_text(strip=True)
                activity = _parse_activity(cell_text)
                if activity is None:
                    continue

                open_t, close_t = activity
                tta_entries[key].append({
                    'date': date_str,
                    'day': date_display,
                    'open': open_t,
                    'close': close_t,
                    'notes': NOTES_LABEL,
                })
