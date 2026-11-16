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
| scenario | `add_crossing(state, point)` / `add_amenity(state, point, category)` | → updated segments, rescoring only affected ones |
| mapview | `make_map(segments, column)` | → Folium map coloured by score |
| report | `build_report(...)` / `to_pdf(markdown)` | → Markdown string / PDF bytes |

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
10. **Report:** Markdown, then PDF.
11. **Timing and validation:** a 2 km² area in under 60 s; correlation with pedestrian counts for the validation city.
