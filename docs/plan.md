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
11. **Timing and validation:** scoring time and download time measured separately (see product-brief.md); correlation with pedestrian counts for the validation city.

## Download robustness

Done:
- The Overpass server is set by `WALKSCOPE_OVERPASS_URL` (default `https://overpass-api.de/api`).
- Each request (geocoding, street graph, features) gets 3 tries with 5 s and 10 s waits between them.
- If every try fails, `ServerBusyError` is raised, which the app shows as "The map data server did not answer after several minutes.", with the hint to try again later or open one of the example areas.
- **Waiting limits** (build step 11). A slow success is better than a quick failure, so the limits are set from measurements on overpass-api.de (`scripts/measure_timing.py --no-limits`, 2026-11-16): the street-graph request took about **70 s**, the features request **105–181 s**, calculation 3–4 s; about 3–4 minutes per new area. Each HTTP request times out after **300 s** (the Overpass query's own `[timeout:]` is 300 too), above the slowest features request measured. All tries, our pauses and OSMnx's own pauses for one area download (graph and features together) share a **600 s budget**: the slowest download measured (about 250 s) plus a retry of the slowest request. Finding a place name keeps **45 s** (measured: about 1 s). OSMnx pauses on its own before each request while the server's status says all slots are taken, and for 55 s before re-sending after a 429 or 504 reply, with no limit; inside the budget such a pause raises instead of running past it. A try only starts with at least 10 s left, and the last one gets only the time left. Worst case before the busy message: about 11 minutes for a place name (45 + 600 s), 10 minutes for a drawn area. The first limits (60 s per request, 150 s budget) made every download end busy, since the features request alone needs over 100 s.
- **Live progress.** The app scores in a worker thread and redraws the status every second: the current step (finding the place, downloading streets, downloading shops, stops and crossings, scoring), each finished step's time, the elapsed time, a note when the server is busy and a request is retried, and a note that the public map server often takes 3–5 minutes for a new area and up to 10 when busy.
- **Bundled examples.** The three examples ship with their downloaded data in `data/examples/` (gzipped `AreaData` pickles plus `index.json` with each outline, cache key and download date), so their buttons open in seconds without Nominatim or Overpass. `fetch_area` uses a bundled file when its cache key matches (same area, buffer, tags and `CACHE_VERSION`), otherwise it downloads as usual; `tests/test_examples.py` fails when the bundle no longer matches the config. `scripts/refresh_examples.py` downloads them again. The data is © OpenStreetMap contributors under the ODbL (not the code's MIT licence); the notice is in `data/examples/README.md`, the app shows it with the download date.

Possible later improvements:
- **Lighter features request** (checked 2026-11-16, not changed yet). The features request takes 105–181 s, the street graph about 70 s. Responses are small (at most 1.6 MB and 11,400 elements in the cached responses), so the time goes to the server: evaluating the query, or waiting for a slot. The timing script can't tell which, because OSMnx's slot pauses are counted inside the request time. What OSMnx sends today, for the Aachen example: 66 statements (22 tag values × node, way and relation), each carrying the 69-vertex buffer polygon, 89,600 characters in all. Three cuts that leave the scores unchanged:
  1. **Crossings and kerbs as nodes only.** `classify_features` keeps only point geometries for street points, and these come only from nodes. Today ways tagged `crossing` or `kerb` (mostly `footway=crossing`) are downloaded with all their nodes and then dropped: up to about half of the elements in the cached responses (2–52 %, an upper bound).
  2. **One statement per key**, with a regular expression over its values (`nwr["shop"~"^(supermarket|…)$"]`): 9 statements instead of 66, 12,300 characters. Same elements.
  3. **Bounding box instead of the polygon.** OSMnx clips the result to the polygon after downloading, so the features are identical. Around a square study area the box adds about 9 % to the area queried. With cut 2: 750 characters.

  All three need our own Overpass query in place of `ox.features_from_polygon`; OSMnx's parser can still build the GeoDataFrame. Not cuttable: amenities and stops mapped as ways or relations (parks, school grounds, platforms) and the nodes needed for their geometry.
  **Still to measure:** how much faster these variants are, and that they give identical scores. The first comparison (2026-11-16, 22:30) got HTTP 406 from overpass-api.de on every request because the script didn't send OSMnx's User-Agent, so no query ran and there are no timings yet. Repeat it with `scripts/compare_feature_queries.py`, which now sends OSMnx's User-Agent, when the server is less busy (early morning CET). Run the current query again at the end, compare each variant's POIs, stops, street points and segment scores with it, and run it several times, since server load swings the times.
- **Fallback server.** A second public Overpass server for when the main one stays busy. Before relying on one, check that it returns the same data as the main server. Tested so far: `overpass.private.coffee` timed out repeatedly (3 × 180 s) on the Aachen test area, and `maps.mail.ru` was not tested. The code would only need to loop `with_retry` over a list of servers.
- **Own Overpass instance.** For heavy use or the validation runs, point `WALKSCOPE_OVERPASS_URL` at a local Overpass server.
