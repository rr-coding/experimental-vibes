import json
import logging
import os
import re

logger = logging.getLogger(__name__)

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>UK Military Air Weapons Ranges</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #fff; color: #222; }

    #topbar {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 16px;
      background: #1a1a2e;
      color: #fff;
      flex-wrap: wrap;
    }
    #topbar h1 { font-size: 1rem; font-weight: 600; flex: 1; white-space: nowrap; }
    #postcode-form { display: flex; gap: 8px; }
    #postcode-input {
      padding: 7px 11px;
      border: none;
      border-radius: 4px;
      font-size: 0.95rem;
      width: 190px;
    }
    #postcode-form button {
      padding: 7px 14px;
      background: #4e9af1;
      color: #fff;
      border: none;
      border-radius: 4px;
      font-size: 0.95rem;
      cursor: pointer;
      white-space: nowrap;
    }
    #postcode-form button:hover { background: #2d7ed4; }
    #postcode-form button:disabled { opacity: 0.6; cursor: default; }

    #status-box {
      display: none;
      padding: 12px 16px;
      font-size: 0.95rem;
      line-height: 1.4;
      border-left: 5px solid transparent;
    }
    #status-box.green { background: #d4edda; border-color: #28a745; color: #155724; }
    #status-box.amber { background: #fff3cd; border-color: #e0a800; color: #6d4c00; }
    #status-box.red   { background: #f8d7da; border-color: #dc3545; color: #721c24; }

    #map { height: 70vh; }

    .popup-season { font-size: 0.75rem; color: #666; margin: 6px 0 2px; font-style: italic; }
    .popup-table { border-collapse: collapse; width: 100%; font-size: 0.82rem; }
    .popup-table td { padding: 1px 6px 1px 0; }
    .popup-table tr:nth-child(even) td { background: #f5f5f5; }
    .popup-note { font-size: 0.82rem; color: #555; margin-top: 6px; font-style: italic; }
    .leaflet-popup-content { min-width: 210px; max-height: 260px; overflow-y: auto; }
  </style>
</head>
<body>
  <div id="topbar">
    <h1>UK Military Air Weapons Ranges</h1>
    <div id="postcode-form">
      <input id="postcode-input" type="text" placeholder="Postcode, e.g. LN12 2QP" />
      <button id="lookup-btn" onclick="lookupPostcode()">Find nearest range</button>
    </div>
  </div>

  <div id="status-box"></div>
  <div id="map"></div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const locations = {{ locations | tojson }};

    // ── Map setup ──────────────────────────────────────────────
    const map = L.map('map').setView([55.5, -3.5], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // ── Schedule popup builder ─────────────────────────────────
    function buildScheduleHtml(schedule) {
      if (!schedule || schedule.length === 0) return '';

      // Group entries by notes (season label)
      const groups = new Map();
      schedule.forEach(e => {
        const key = e.notes || '';
        if (!groups.has(key)) groups.set(key, []);
        if (e.day) groups.get(key).push(e);
        else if (!e.day && e.notes) groups.set(key, groups.get(key)); // note-only
      });

      let html = '';
      for (const [season, entries] of groups) {
        // Note-only row (e.g. Cape Wrath)
        if (entries.length === 0) {
          html += `<p class="popup-note">${season}</p>`;
          continue;
        }
        if (season) html += `<p class="popup-season">${season}</p>`;
        html += '<table class="popup-table">';
        entries.forEach(e => {
          const times = e.open ? `${e.open}–${e.close}` : 'Closed';
          html += `<tr><td>${e.day}</td><td>${times}</td></tr>`;
        });
        html += '</table>';
      }
      return html;
    }

    // ── Range markers ─────────────────────────────────────────
    locations.forEach(loc => {
      if (loc.lat === null) return;
      const content = `<strong>${loc.name}</strong>${buildScheduleHtml(loc.schedule)}`;
      L.marker([loc.lat, loc.lon]).addTo(map).bindPopup(content);
    });

    // ── Postcode lookup ────────────────────────────────────────
    let userMarker = null;
    let rangeLine  = null;

    async function lookupPostcode() {
      const postcode = document.getElementById('postcode-input').value.trim();
      if (!postcode) return;

      const btn = document.getElementById('lookup-btn');
      btn.textContent = 'Looking up…';
      btn.disabled = true;

      try {
        const res = await fetch('/lookup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ postcode })
        });
        const data = await res.json();

        if (data.error) { alert(data.error); return; }

        // Status box
        const box = document.getElementById('status-box');
        box.className = data.status;
        box.innerHTML = data.message;
        box.style.display = 'block';

        // Remove previous overlays
        if (userMarker) map.removeLayer(userMarker);
        if (rangeLine)  map.removeLayer(rangeLine);

        // Blue circle at user's location
        userMarker = L.circleMarker([data.user_lat, data.user_lon], {
          radius: 9, color: '#1a6ed8', fillColor: '#4e9af1', fillOpacity: 0.9, weight: 2
        }).addTo(map).bindPopup(`<strong>Your location</strong><br>${postcode.toUpperCase()}`);

        // Dashed line to nearest range
        const nr = data.nearest;
        rangeLine = L.polyline(
          [[data.user_lat, data.user_lon], [nr.lat, nr.lon]],
          { color: '#e63946', weight: 2, dashArray: '6 4' }
        ).addTo(map);

        map.fitBounds(
          [[data.user_lat, data.user_lon], [nr.lat, nr.lon]],
          { padding: [60, 60] }
        );

      } catch (e) {
        alert('Request failed: ' + e.message);
      } finally {
        btn.textContent = 'Find nearest range';
        btn.disabled = false;
      }
    }

    document.getElementById('postcode-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') lookupPostcode();
    });
  </script>
</body>
</html>
"""


class MapBuilderAgent:
    def run(self):
        logger.info("[MapBuilderAgent] Starting")

        with open("data/geocoded_locations.json") as f:
            locations = json.load(f)

        os.makedirs("templates", exist_ok=True)
        with open("templates/map.html", "w") as f:
            f.write(TEMPLATE)

        valid = sum(1 for loc in locations if loc["lat"] is not None)
        logger.info(f"[MapBuilderAgent] Done — template written to templates/map.html ({valid} mappable ranges)")

        # Also update the inline data in the static docs/index.html
        static_path = "docs/index.html"
        if os.path.exists(static_path):
            with open(static_path) as f:
                html = f.read()
            locations_json = json.dumps(locations)
            replacement = f'const locations = {locations_json};'
            html = re.sub(
                r'const locations = \[.*?\];',
                lambda m: replacement,
                html,
                flags=re.DOTALL,
            )
            # Also copy geocoded data to docs/data/
            os.makedirs("docs/data", exist_ok=True)
            with open("docs/data/geocoded_locations.json", "w") as f:
                json.dump(locations, f, indent=2)
            with open(static_path, "w") as f:
                f.write(html)
            logger.info(f"[MapBuilderAgent] Updated {static_path} and docs/data/geocoded_locations.json")

        return locations
