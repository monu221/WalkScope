# WalkScope – Build Plan

Approved plan for version 1. The scoring rules are in [method.md](method.md); every number they use is in `config/indicators.yaml` and `config/personas.yaml`.

## Repo structure

```
WalkScope/
├── app/
│   └── streamlit_app.py        # UI only: inputs, state, calls into the package
├── src/walkscope/
│   ├── config.py               # load + validate personas.yaml and indicators.yaml
│   ├── fetch.py                # place/polygon → OSM graph + features, with disk cache
│   ├── network.py              # graph → segments table (one row per street segment)
│   ├── indicators/
│   │   ├── amenity.py
│   │   ├── connectivity.py
│   │   ├── infrastructure.py
│   │   ├── traffic.py
│   │   └── transit.py
│   ├── scoring.py              # combine indicators with persona weights
│   ├── completeness.py         # data-completeness % per area
│   ├── scenario.py             # add crossing / amenity, rescore what's affected
│   ├── mapview.py              # Folium map + weakest-streets table
│   └── report.py               # one-page Markdown + PDF (fpdf2)
├── config/
│   ├── personas.yaml
│   └── indicators.yaml
├── tests/
│   ├── fixtures/               # tiny hand-made graph + one small saved OSM extract
│   └── test_*.py
├── data/cache/                 # gitignored
├── docs/
├── pyproject.toml
├── Dockerfile
└── .github/workflows/ci.yml
```

**Core design:** a single segments GeoDataFrame, with one row per street segment. It holds the geometry, the OSM tags, and one column per indicator and sub-score. Indicator scores don't depend on the persona and are computed once. Switching persona only re-weights existing columns.

## Main functions

| Module | Function | Input → Output |
| --- | --- | --- |
| config | `load_personas(path)` | YAML → personas (weights + infrastructure mix); raises an error if a row doesn't sum to 100 |
| config | `load_indicator_params(path)` | YAML → thresholds, defaults, tag mappings |
| fetch | `area_from_place(name)` | place name → boundary polygon (EPSG:4326) |
| fetch | `fetch_area(polygon, buffer_m)` | polygon → walking graph, amenity points, transit stops, crossing/kerb nodes; cached |
| network | `graph_to_segments(graph, area)` | graph → segments table in a metric CRS, with tags, measuring node, in-area flag |
| indicators | `amenity_score(graph, segments, pois, params)` | → 0–100 per segment (one network search per category, cut off at 800 m) |
| indicators | `connectivity_score(graph, segments, params)` | → 0–100 per segment |
| indicators | `infrastructure_subscores(segments, crossings, kerbs, params)` | → sidewalk, crossing, kerb, surface, lit sub-scores |
| indicators | `infrastructure_score(subscores, persona)` | → 0–100 using the persona's mix |
| indicators | `traffic_score(segments, params)` | → 0–100 |
| indicators | `transit_score(graph, segments, stops, params)` | → 0–100 |
| scoring | `score_segments(segments, persona)` | → `score` column = Σ(indicator × weight) / 100 |
| scoring | `weakest(segments, n)` | → n lowest-scoring *named* segments |
| completeness | `completeness(segments)` | → sidewalk %, kerb %, lit %, and their mean |
| scenario | `add_crossing(net, segments, point, params)` / `add_amenity(...)` / `apply_edits(net, edits, params)` | → updated segments copy and changed ids, rescoring only affected ones |
| drawing | `draw_map(outline)` / `polygon_from_drawings(drawings)` | → Folium map with draw tools / last drawn polygon (lat/lon) for `score_area` |
| mapview | `make_map(segments, column)` | → Folium map coloured by score |
| report | `build_report(net, place, persona, edits)` / `to_markdown(report)` / `to_pdf(report)` | → Report / Markdown string / PDF bytes, rendered from the same Report |

## Build order

Each step ends with passing tests and a commit.

1. **Skeleton:** `pyproject.toml`, CI running pytest, `config.py`, `personas.yaml`, `indicators.yaml`, config tests.
2. **`fetch` + `network`:** segments table from a place name, with caching. Tests use a saved fixture, never the live API.
3. **`traffic_score`:** tags only; settles how missing tags are handled.
4. **Thin end-to-end version:** `scoring` + `mapview` + a minimal Streamlit page (type a place, see a map scored on one indicator).
5. **Remaining indicators:** infrastructure, connectivity, transit, amenity.
6. **`completeness`**, shown in the UI.
7. **Persona switch.**
8. **Scenarios:** add a crossing or an amenity.
9. **Draw an area on the map** (streamlit-folium Draw plugin).
   The Aachen example is a preset drawn area (Aachen's districts are over 5 km² or don't resolve
   to an area): a 1.5 km² square centred between the Dom and the Markt.
10. **Report:** Markdown, then PDF.
11. **Timing and validation:** a 2 km² area in under 60 s; correlation with pedestrian counts for the validation city.

## Download robustness

Done:
- The Overpass server is set by `WALKSCOPE_OVERPASS_URL` (default `https://overpass-api.de/api`).
- Each request (geocoding, street graph, features) gets 3 tries with 5 s and 10 s waits between them.
- If every try fails, `ServerBusyError` is raised, which the app shows as "The map data server is busy. Please try again in a minute."
- **Waiting limits** (build step 11). Each HTTP request times out after **60 s** (was OSMnx's 180 s; the Overpass query's own `[timeout:]` is 60 too). All tries, our pauses and OSMnx's own pauses for one area download (graph and features together) share a **150 s budget**, and finding a place name has **45 s**. OSMnx pauses on its own before each request while the server's status says all slots are taken, and for 55 s before re-sending after a 429 or 504 reply, with no limit; inside the budget such a pause raises instead of running past it. A try only starts with at least 10 s left, and the last one gets only the time left. Worst case before the busy message: about 3 minutes for a place name (45 + 150 s), 2.5 minutes for a drawn area; before, at least 3 × 180 + 15 = 555 s (about 9 minutes), and unbounded while the server kept answering 504.

- **Bundled examples.** The three examples ship with their downloaded data in `data/examples/` (gzipped `AreaData` pickles plus `index.json` with each outline, cache key and download date), so their buttons open in seconds without Nominatim or Overpass. `fetch_area` uses a bundled file when its cache key matches (same area, buffer, tags and `CACHE_VERSION`), otherwise it downloads as usual; `tests/test_examples.py` fails when the bundle no longer matches the config. `scripts/refresh_examples.py` downloads them again. The data is © OpenStreetMap contributors under the ODbL (not the code's MIT licence); the notice is in `data/examples/README.md`, the app shows it with the download date.

Possible later improvements:
- **Fallback server.** A second public Overpass server for when the main one stays busy. Before relying on one, check that it returns the same data as the main server. Tested so far: `overpass.private.coffee` timed out repeatedly (3 × 180 s) on the Aachen test area, and `maps.mail.ru` was not tested. The code would only need to loop `with_retry` over a list of servers.
- **Own Overpass instance.** For heavy use or the validation runs, point `WALKSCOPE_OVERPASS_URL` at a local Overpass server.
