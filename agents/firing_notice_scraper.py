"""
Generic scraper for all UK military firing range notices listed at:
https://www.gov.uk/government/collections/firing-notice

Handles every publication EXCEPT Lulworth (covered by LulworthScraperAgent).
"""
import logging
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gov.uk"
COLLECTIONS_URL = "https://www.gov.uk/government/collections/firing-notice"

# Publication slugs already handled by dedicated agents
SKIP_PUB_SLUGS = frozenset(["lulworth-firing-notice"])

MONTH_NAMES = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
}

# Cell text → no activity (checked case-insensitively after strip)
NO_ACTIVITY_EXACT = frozenset([
    'no firing', 'nil', 'no activity', 'open to public', 'open',
    'not in use', 'no entry', 'no access', 'n/a', '-', '',
    'closed to public - no firing', 'closed - no firing',
    'no firing - open to public',
])

# Coordinate lookup: longest-match-first substring against column header / doc title
# (lat, lon, display_name)
RANGE_COORDS = {
    # Scotland
    "garelochhead":   (56.03, -4.77, "Garelochhead Range"),
    "cape wrath":     (58.63, -4.99, "Cape Wrath Range"),
    # Wales
    "castlemartin":   (51.63, -4.93, "Castlemartin Range"),
    "manorbier":      (51.64, -4.79, "Manorbier Range"),
    "penally":        (51.67, -4.71, "Penally Range"),
    "sennybridge":    (51.95, -3.60, "Sennybridge Range"),
    "sealand":        (53.22, -2.98, "Sealand Range"),
    # NE England
    "battle hill":    (54.37, -1.62, "Battle Hill Ranges (Catterick)"),
    "bellerby":       (54.35, -1.77, "Bellerby Ranges (Catterick)"),
    "feldom":         (54.42, -1.70, "Feldom Ranges (Catterick)"),
    "otterburn":      (55.26, -2.15, "Otterburn Range"),
    "ponteland":      (55.04, -1.74, "Ponteland Range"),
    # NW England
    "holcombe":       (53.68, -2.39, "Holcombe Moor Range"),
    # West Midlands
    "kingsbury":      (52.56, -1.69, "Kingsbury Range"),
    "upper hulme":    (53.10, -1.98, "Leek & Upper Hulme Range"),
    "leek":           (53.08, -1.97, "Leek & Upper Hulme Range"),
    "whittington":    (52.69, -1.88, "Whittington Range"),
    # East England
    "thetford":       (52.42,  0.74, "Thetford Range"),
    "fingringhoe":    (51.85,  0.94, "Fingringhoe Range"),
    "barton":         (52.49,  0.67, "Barton Road Range"),
    "beckingham":     (53.41, -0.79, "Beckingham Range"),
    # South East
    "ash ranges":     (51.26, -0.68, "Ash Ranges"),
    "aldershot":      (51.25, -0.77, "Aldershot Range"),
    "ash":            (51.26, -0.68, "Ash Ranges"),
    # South West
    "chickerell":     (50.64, -2.47, "Chickerell Range"),
    "okehampton":     (50.74, -4.00, "Okehampton Range"),
    "willsworthy":    (50.59, -4.07, "Willsworthy Range"),
    "merrivale":      (50.55, -4.04, "Merrivale Range"),
    "langport":       (51.04, -2.83, "Langport Range"),
    "millpool":       (50.45, -4.47, "Millpool Range"),
    "straight point": (50.62, -3.40, "Straight Point Range"),
    "tregantle":      (50.37, -4.28, "Tregantle Range"),
    "yoxter":         (51.24, -2.77, "Yoxter Range"),
    # SPTA
    "larkhill":       (51.20, -1.79, "Larkhill Range (SPTA)"),
    "westdown":       (51.25, -1.87, "Westdown Range (SPTA)"),
    "bulford":        (51.18, -1.71, "Bulford Range (SPTA)"),
    "warminster":     (51.20, -2.18, "Warminster Range (SPTA)"),
    "areas 6":        (51.13, -2.04, "Chitterne/Areas 6–8 (SPTA)"),
    "chitterne":      (51.13, -2.04, "Chitterne/Areas 6–8 (SPTA)"),
}

# Generic column-header labels that don't name a range; fall back to doc title
GENERIC_COL_HEADERS = frozenset([
    'timings', 'firing times', 'firing', 'times', 'closure times',
    'range', 'activity', 'active training', 'status',
])


# ── Module-level helpers ────────────────────────────────────────────────────

def _parse_time_str(raw):
    """Convert '9am', '10:30am', '4pm', '4:30pm' → 'HH:MM'. Returns '' if unparseable."""
    t = raw.strip().lower()
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$', t)
    if not m:
        return ''
    h, mins, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem == 'pm' and h != 12:
        h += 12
    if meridiem == 'am' and h == 12:
        h = 0
    return f'{h:02d}:{mins:02d}'


def _lookup_coords(text):
    """Return (lat, lon, display_name) for the first matching key in RANGE_COORDS, or None."""
    h = text.lower()
    # Longest key first for most-specific match
    for key in sorted(RANGE_COORDS, key=len, reverse=True):
        if key in h:
            return RANGE_COORDS[key]
    return None


def _is_no_activity(text):
    tl = text.strip().lower()
    if tl in NO_ACTIVITY_EXACT:
        return True
    if re.search(r'\bno\s+firing\b', tl):
        return True
    if re.fullmatch(r'open(?:\s+to\s+public)?', tl):
        return True
    return False


def _parse_activity(cell_text):
    """
    Return (open_str, close_str) or None if no activity.
    open_str may be a sentinel like 'Day', 'Night', 'Day and night', or 'Closed'
    when specific times are not available.
    """
    t = cell_text.strip()
    if _is_no_activity(t):
        return None

    tl = t.lower()

    # Named day/night markers (check before time parsing)
    if re.search(r'\bday\s+and\s+night\b', tl) or re.search(r'\bday\s*&\s*night\b', tl):
        return 'Day and night', ''
    if re.fullmatch(r'(?:firing\s+)?\(?\s*day\s*\)?', tl):
        return 'Day', ''
    if re.fullmatch(r'(?:firing\s+)?\(?\s*night\s*\)?', tl):
        return 'Night', ''

    # Time ranges in "Xam to Xpm" or "X:XXam to X:XXpm" format
    time_tok = r'\d{1,2}(?::\d{2})?\s*(?:am|pm)'
    windows = re.findall(
        rf'({time_tok})\s*(?:to|-|–)\s*({time_tok})',
        tl, re.IGNORECASE,
    )
    if windows:
        open_t = _parse_time_str(windows[0][0])
        close_t = _parse_time_str(windows[0][1])
        if open_t and close_t:
            return open_t, close_t

    # Military time "HHMM to HHMM" or "HHMM-HHMM"
    mil = re.search(r'(\d{3,4})\s*(?:to|-|–)\s*(\d{3,4})', t)
    if mil:
        def from_mil(s):
            s = s.zfill(4)
            return f'{int(s[:2]):02d}:{s[2:]}'
        return from_mil(mil.group(1)), from_mil(mil.group(2))

    # Closed to public (range in use, times may be embedded)
    if 'closed' in tl and 'no firing' not in tl:
        windows = re.findall(
            rf'({time_tok})\s*(?:to|-|–)\s*({time_tok})',
            tl, re.IGNORECASE,
        )
        if windows:
            open_t = _parse_time_str(windows[0][0])
            close_t = _parse_time_str(windows[0][1])
            if open_t and close_t:
                return open_t, close_t
        return 'Closed', ''

    # Unknown activity text — report it as-is (truncated)
    short = t[:25].strip()
    if short:
        return short, ''

    return None


def _extract_month_year(title):
    """Return (month_int_or_None, year_int_or_None) from a document title."""
    t = title.lower()
    year_m = re.search(r'\b(20\d{2})\b', title)
    year = int(year_m.group(1)) if year_m else None
    for name, num in MONTH_NAMES.items():
        if name in t:
            return num, year
    return None, year


def _make_notes_label(title):
    """Derive a short notes label from a document title."""
    # "Sennybridge firing times March 2026" → "March 2026 firing times"
    m = re.search(
        r'(' + '|'.join(MONTH_NAMES.keys()) + r')\s+(20\d{2})',
        title, re.IGNORECASE,
    )
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)} firing times"
    # Multi-month: "2 March to 12 April 2026"
    year_m = re.search(r'(20\d{2})', title)
    year = year_m.group(1) if year_m else ''
    return f"{title.strip()} firing" if not year else f"{year} firing times"


def _title_is_relevant(title, today):
    """True if the document title suggests it may contain current or future data."""
    t = title.lower()
    years = [int(y) for y in re.findall(r'\b(20\d{2})\b', t)]
    if years:
        max_year = max(years)
        if max_year > today.year:
            return True
        if max_year < today.year:
            return False
        # Same year — check months
        months = [MONTH_NAMES[n] for n in MONTH_NAMES if n in t]
        if months:
            return max(months) >= today.month
    return True  # No date clues → include and let content parsing decide


def _parse_date_cell(text, doc_month, doc_year, today):
    """Parse a date string from a table cell. Returns date or None."""
    t = text.strip()
    if not t or t.lower() in ('date', 'day', 'dates', '–', '-'):
        return None

    month_pat = '|'.join(MONTH_NAMES.keys())

    # Full date: "2 March 2026" or "02 March 2026"
    m = re.match(rf'^(\d{{1,2}})\s+({month_pat})\s+(\d{{4}})$', t, re.IGNORECASE)
    if m:
        day = int(m.group(1))
        month = MONTH_NAMES[m.group(2).lower()]
        year = int(m.group(3))
        # Correct clearly wrong year (GOV.UK sometimes has previous year in data)
        if doc_year and year != doc_year and abs(year - doc_year) <= 2:
            year = doc_year
        try:
            return date(year, month, day)
        except ValueError:
            return None

    # Day number only: "1", "01" … "31"
    m = re.match(r'^(\d{1,2})$', t)
    if m and doc_month and doc_year:
        day = int(m.group(1))
        if 1 <= day <= 31:
            try:
                return date(doc_year, doc_month, day)
            except ValueError:
                return None

    return None


# ── Agent ───────────────────────────────────────────────────────────────────

class FiringNoticeScraperAgent:

    def run(self):
        today = date.today()
        logger.info("[FiringNoticeScraperAgent] Starting (today = %s)", today)

        pub_links = self._get_publication_links()
        logger.info("[FiringNoticeScraperAgent] Found %d publication(s)", len(pub_links))

        locations_map = {}  # display_name → location dict

        for pub_url in pub_links:
            try:
                self._process_publication(pub_url, today, locations_map)
            except Exception as e:
                logger.warning("[FiringNoticeScraperAgent] Error processing %s: %s", pub_url, e)
            time.sleep(0.4)

        locations = list(locations_map.values())
        logger.info(
            "[FiringNoticeScraperAgent] Done — %d range(s) with upcoming activity",
            len(locations),
        )
        return locations

    # ── Publication discovery ───────────────────────────────────────────────

    def _get_publication_links(self):
        r = requests.get(COLLECTIONS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        seen, urls = set(), []
        for a in soup.find_all('a', href=True):
            href = a['href']
            # Publication index pages have exactly the form /government/publications/<slug>
            if re.fullmatch(r'/government/publications/[^/]+', href):
                slug = href.split('/')[-1]
                if slug in SKIP_PUB_SLUGS:
                    continue
                full = BASE_URL + href
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
        return urls

    def _get_doc_links(self, soup, pub_slug):
        """Return (title, url) pairs for document sub-pages of a publication."""
        seen, links = set(), []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if f'/{pub_slug}/' in href:
                full = (BASE_URL + href) if href.startswith('/') else href
                title = a.get_text(strip=True)
                if full not in seen and title:
                    seen.add(full)
                    links.append((title, full))
        return links

    # ── Per-publication processing ──────────────────────────────────────────

    def _process_publication(self, pub_url, today, locations_map):
        r = requests.get(pub_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        pub_slug = pub_url.rstrip('/').split('/')[-1]

        content = soup.find('div', class_='govspeak') or soup.find('main') or soup
        tables = content.find_all('table') if content else []

        if tables:
            # This IS a document page (direct URL to content)
            h1 = soup.find('h1')
            title = h1.get_text(strip=True) if h1 else ''
            self._parse_document(soup, title, today, locations_map)
        else:
            # Publication index — find and follow sub-document links
            doc_links = self._get_doc_links(soup, pub_slug)
            if not doc_links:
                logger.debug("[FiringNoticeScraperAgent] No sub-docs found at %s", pub_url)
                return
            for title, doc_url in doc_links:
                if not _title_is_relevant(title, today):
                    logger.debug(
                        "[FiringNoticeScraperAgent] Skipping (past): %s", title[:60]
                    )
                    continue
                try:
                    dr = requests.get(
                        doc_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10
                    )
                    dr.raise_for_status()
                    dsoup = BeautifulSoup(dr.text, 'html.parser')
                    dh1 = dsoup.find('h1')
                    dtitle = dh1.get_text(strip=True) if dh1 else title
                    self._parse_document(dsoup, dtitle, today, locations_map)
                    time.sleep(0.4)
                except Exception as e:
                    logger.warning(
                        "[FiringNoticeScraperAgent] Failed %s: %s", doc_url, e
                    )

    # ── Document parsing ────────────────────────────────────────────────────

    def _parse_document(self, soup, title, today, locations_map):
        content = soup.find('div', class_='govspeak') or soup.find('main') or soup
        if not content:
            return

        doc_month, doc_year = _extract_month_year(title)
        if doc_year is None:
            doc_year = today.year
        notes = _make_notes_label(title)

        for table in content.find_all('table'):
            self._parse_table(table, doc_month, doc_year, today, notes, title, locations_map)

    def _parse_table(self, table, doc_month, doc_year, today, notes, doc_title, locations_map):
        # Determine header row
        thead = table.find('thead')
        tbody = table.find('tbody')
        if thead:
            header_cells = thead.find_all(['th', 'td'])
            data_rows = tbody.find_all('tr') if tbody else table.find_all('tr')[1:]
        else:
            all_rows = table.find_all('tr')
            if not all_rows:
                return
            header_cells = all_rows[0].find_all(['th', 'td'])
            data_rows = all_rows[1:]

        if not header_cells:
            return

        headers = [c.get_text(strip=True) for c in header_cells]
        first_h = headers[0].lower().strip()

        # First column must look like a date column
        if first_h not in ('date', 'day', 'dates', 'day/date', ''):
            return

        # Map column index → (lat, lon, display_name)
        col_info = {}
        for i, h in enumerate(headers[1:], start=1):
            info = _lookup_coords(h)
            if info:
                col_info[i] = info

        # Fallback: single-range doc — identify range from document title
        if not col_info:
            fallback = _lookup_coords(doc_title)
            if fallback:
                # Use only the first non-date column
                for i in range(1, len(headers)):
                    if headers[i].lower().strip() in GENERIC_COL_HEADERS or headers[i]:
                        col_info[i] = fallback
                        break
            if not col_info:
                return  # Can't place on map

        for row in data_rows:
            cells = row.find_all(['th', 'td'])
            if not cells:
                continue

            date_text = cells[0].get_text(strip=True)
            row_date = _parse_date_cell(date_text, doc_month, doc_year, today)
            if row_date is None or row_date < today:
                continue

            date_str = row_date.strftime('%Y-%m-%d')
            date_display = row_date.strftime('%-d %b')

            for col_idx, (lat, lon, name) in col_info.items():
                if col_idx >= len(cells):
                    continue
                cell_text = cells[col_idx].get_text(separator=' ', strip=True)
                activity = _parse_activity(cell_text)
                if activity is None:
                    continue

                open_t, close_t = activity
                if name not in locations_map:
                    locations_map[name] = {
                        'name': name,
                        'schedule': [],
                        'lat': lat,
                        'lon': lon,
                    }
                locations_map[name]['schedule'].append({
                    'date': date_str,
                    'day': date_display,
                    'open': open_t,
                    'close': close_t,
                    'notes': notes,
                })
