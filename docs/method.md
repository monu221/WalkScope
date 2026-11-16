# WalkScope – Scoring Method

Each street segment gets five indicator scores from 0 to 100, combined as a weighted average. Persona weights live in `config/personas.yaml`; every threshold, default and tag mapping lives in `config/indicators.yaml`. A persona is a different set of weights plus, for some personas, extra tags inside the infrastructure score.

## Indicators

| Indicator | What it measures | Data (all from OpenStreetMap) |
| --- | --- | --- |
| Amenity access | How many everyday categories (groceries, school, pharmacy, park, café, doctor) are within a 10-minute walk along the network | Points of interest and the walking network |
| Connectivity | Intersection density within 400 m; short blocks score higher | Walking network |
| Pedestrian infrastructure | Sidewalk present, marked crossings nearby, pedestrian-only street | `sidewalk`, `crossing`, `highway=footway/pedestrian` tags |
| Traffic stress | Speed limit, road class (as a stand-in for traffic volume) and lanes; a residential street scores above a main road at the same speed | `highway`, `maxspeed`, `lanes` tags |
| Transit access | Walking distance to the nearest bus or rail stop | `public_transport` and `railway` tags |

## Study area and measuring point

- The OSM data is downloaded for the study area plus an **800 m buffer**, so that streets near the edge can reach amenities and stops outside it. Only segments inside the study area are scored and shown.
- Distances for a segment are measured from the **network node nearest to the segment midpoint**.
- Footways, pedestrian streets, paths and steps are scored as segments like any other street. Together they form the *pedestrian-only* class used below.

## Routing-only segments

Some segments are kept in the walking network, so they count for walking distances to amenities and stops, but they are **not scored and not shown**. A segment is routing-only if it has one of these tags and every value of that tag is in the list (`routing_only` in the config):

| Tag | Values | Why |
| --- | --- | --- |
| `footway` (pedestrian-only segments only) | `sidewalk`, `crossing`, `traffic_island` | Part of a road; the road carries their quality (see below) |
| `service` | `parking_aisle`, `driveway` | Not streets people walk along |
| `access` | `private` | Not public. OSMnx already leaves most of these out of the download |
| `highway` | `corridor` | Indoor passages |

A merged segment that is only partly one of these, such as `steps` + `corridor`, stays scored.

## Separately mapped sidewalks

OpenStreetMap has two ways to map a sidewalk:

- **As a tag on the road:** `sidewalk=both/left/right/no`. This is common in most German cities.
- **As its own way:** `highway=footway` + `footway=sidewalk` next to the road, with the road tagged `sidewalk=separate`. This is common in Berlin.

OSMnx's built-in walking network drops every road tagged `sidewalk=separate`. In Berlin that removes nearly all streets. Only the sidewalk ways remain, and they would count as pedestrian-only, scoring 100 on traffic stress and infrastructure even beside a four-lane main road. A 480 m × 450 m test area in Kreuzberg contained 306 segments, but no residential or main street, and only 5 had a name.

WalkScope therefore downloads with OSMnx's walk filter **minus** the `sidewalk=separate` clauses (`walk_filter()` in `fetch.py`). Roads are kept and scored, and their separately drawn sidewalks and crossings are routing-only. A road tagged `sidewalk=separate` scores 100 on the sidewalk sub-score, and its own speed and lanes set its traffic stress.

Kreuzberg test area (about 2 km², lon 13.383–13.403, lat 52.484–52.4965), after this change:

| | Segments |
| --- | --- |
| In the study area | 2,996 |
| Routing-only | 1,723 (1,148 sidewalk/crossing ways, 568 driveways and parking aisles, 7 corridors) |
| **Scored** | **1,273**, of which 59 % have a name |
| Scored by class | 438 residential, 390 footway, 155 secondary, 80 service, 72 tertiary, 60 primary, 30 path, 48 other (steps, links, merged) |

Of the 1,399 roads (non-pedestrian segments) in the area before driveways and parking aisles were removed, 53 % had a sidewalk tag, and 51 % were `sidewalk=separate`.

## Indicator definitions

### Amenity access

- Six categories: groceries, school, pharmacy, park, café, doctor. The OSM tags for each category are listed in the config.
- A category counts as reached if at least one of its points lies within **800 m along the walking network**. In v1 this applies to every persona. A walk speed is kept in the config for later versions, but it is not used yet.
- Score = categories reached ÷ 6 × 100.

### Connectivity

- Intersections are network nodes where three or more streets meet. **Streets** here are the roads only: routing-only segments (separately drawn sidewalks and crossings, driveways, parking aisles, corridors) and pedestrian-only segments (footways, paths, steps) are not counted (`count_pedestrian_only: false` in the config).
- Count the intersections within a **400 m straight-line radius** of the measuring node and divide by the circle's area to get intersections per km².
- Score is linear from **30 per km² (0 points) to 150 per km² (100 points)**, clipped to 0–100.

Why roads only: the 30–150 scale is a street-network intersection density. Counting every way that meets at a node (OSMnx's `street_count`) made every scored segment score 100 in all three test areas, at about 650–1,400 intersections per km², because each sidewalk–crossing junction counts. Leaving out only the routing-only ways still gave a median of 215 (Kreuzberg), 511 (St. Pauli) and 545 (Aachen) per km², with 86–100 % of segments at 100, because footways in parks and courtyards form dense meshes. Counting roads only gives medians of 82, 119 and 147 per km² (10th–90th percentile about 55–190), which spreads across the scale. Footway shortcuts still count for amenity and transit access, which use walking distances along the full network.

### Pedestrian infrastructure

Base score:

- Pedestrian-only segments (`highway=footway`, `pedestrian`, `path` or `steps`; list in the config) score **100**.
- All other segments: **60 % sidewalk sub-score + 40 % crossing sub-score**.

Sub-scores (values set in the config):

| Sub-score | Rule | Missing tag |
| --- | --- | --- |
| Sidewalk | `both` or `separate` = 100, `left` / `right` = 60, `no` / `none` = 0 | **50 (unknown)**, counted as missing in data completeness |
| Crossing | Marked crossing node (`crossing=marked/zebra/traffic_signals/uncontrolled`) within 50 m of the segment = 100; only unmarked crossings within 50 m = 50; none = 0 | – (no crossing nearby scores 0) |
| Kerb (wheelchair) | Worst kerb within 50 m of the segment: `flush` / `lowered` / `no` = 100, `rolled` = 50, `raised` = 0 | 50 (unknown) |
| Surface (wheelchair) | `sidewalk:surface` if present, else `surface`: paved types = 100, sett / cobblestone = 30, compacted / fine gravel = 60, unpaved types = 0 | 50 (unknown) |
| Lit (night-time) | `yes` / `24/7` / `automatic` = 100, `no` = 0 | 50 (unknown) |

Persona mix of the infrastructure score:

| Persona | Base | Kerb | Surface | Lit |
| --- | --- | --- | --- | --- |
| General adult | 100 % | – | – | – |
| Wheelchair user | 50 % | 25 % | 25 % | – |
| Child walking to school | 100 % | – | – | – |
| Night-time | 60 % | – | – | 40 % |

Reading the tags:

- **Sidewalk side tags.** If `sidewalk` is missing, the sidewalk is read from `sidewalk:left` and `sidewalk:right`, each falling back to `sidewalk:both`. `yes` / `separate` means a sidewalk on that side, `no` / `none` means none. Both sides → `both` (100), one side → 60, both sides none → 0, otherwise missing (50). In Kreuzberg 262 roads are tagged only `sidewalk:left=no` + `sidewalk:right=separate`; without this rule they would all score the missing 50.
- **Not crossings.** Nodes tagged `crossing=no` (crossing not possible) or `crossing=informal` (not a designated crossing) don't count as crossings (`ignored_values` in the config). A `highway=crossing` node without a `crossing` tag counts as unmarked.
- **Several values.** A merged segment with several values (for example `lit=no;yes` or `surface=sett;asphalt`) gets the worst score among them. Values that are not in the config (for example `sidewalk=yes`, `kerb=yes`) are ignored; if no value is known, the missing score applies.

The infrastructure score depends on the persona, but its sub-scores do not; they are stored per segment (`infra_sidewalk`, `infra_crossing`, `infra_kerb`, `infra_surface`, `infra_lit`, `infra_base`) and mixed per persona.

`incline` is dropped for v1.

### Traffic stress

- Pedestrian-only segments score **100**.
- Speed comes from `maxspeed`. If it is missing, the **default speed for the road class** (in the config) is used: the German urban default (50 km/h) for most classes, but **30 km/h for residential streets** (see below). Zone values such as `DE:zone30` and `DE:urban` are translated using the config. If a segment has several values, the highest one is used.
- Speed score, linear between these points: 10 km/h → 100, 30 km/h → 75, 50 km/h → 30, 70 km/h → 0.
- **Road-class factor:** the speed score is multiplied by a factor for the road class: trunk 0.5, primary 0.6, secondary 0.7, tertiary 0.85; residential, living street and every other class 1.0. `_link` roads use their parent class's factor; a merged segment uses the lowest factor among its classes.
- Lanes: −10 points for each lane above 2 (from `lanes`). If `lanes` is missing, there is no penalty.
- Clipped to 0–100.

So score = speed score × class factor − lane penalty, clipped to 0–100. At 30 km/h with 2 lanes: residential 75, tertiary 64, secondary 53, primary 45, trunk 38. At 50 km/h: residential 30, tertiary 26, secondary 21, primary 18, trunk 15.

**Why a road-class factor.** Speed alone treats a 30 km/h main road like a 30 km/h residential street, but the main road carries far more traffic: more noise, more vehicles to cross between, more turning traffic at junctions. OSM has no traffic counts, so the road class stands in for traffic volume. Without the factor, Stresemannstraße in Sternschanze (primary, 4 lanes, tagged 30) scored 55, better than residential streets tagged 50 (30). With it, it scores 25 (75 × 0.6 − 20). The lane penalty also reflects volume, so a multi-lane main road is marked down by both; this is intended, since lanes add crossing distance as well as traffic. The average primary-road score in Sternschanze fell from 62 to 32; residential streets are unchanged.

**Residential default of 30 km/h.** The legal urban default in Germany is 50 km/h, but most residential streets are in 30 zones. Among residential and living-street segments that do have a `maxspeed` tag, the share at 30 km/h or lower is:

| Area | Tagged segments | 30 km/h or lower | Share by length | Untagged segments |
| --- | --- | --- | --- | --- |
| Kreuzberg, Berlin | 438 | 374 (85 %) | 90 % | 0 |
| St. Pauli, Hamburg | 335 | 292 (87 %) | 88 % | 53 |
| Sternschanze, Hamburg | 91 | 88 (97 %) | 95 % | 3 |
| Aachen city centre | 222 | 222 (100 %) | 100 % | 21 |

Scored segments only; the 800 m download buffers give the same picture (89–97 %). Nearly all the rest are tagged 50. So an untagged residential street is assumed to be 30 km/h. This is an assumption: streets that are left untagged may differ from tagged ones, and the default for every other road class stays at 50 km/h.

### Transit access

- Walking distance along the network to the nearest bus, tram or rail stop.
- **100 within 300 m**, falling linearly to **0 at 800 m**. A segment with no stop reachable gets 0.

### Walking distances

Transit and amenity access use the same walking distance:

- Each stop or amenity point is snapped to its nearest network node. The distance from the point to that node is not added.
- Distances are walked along the full network, routing-only segments included, and **in both directions on one-way streets**. The downloaded graph keeps one-way roads one-way (1,322 of 22,443 edges in the Kreuzberg download), which matters for cars, not for pedestrians.

## Persona weights

Starting point, to be tuned. Each row sums to 100.

| Persona | Amenity | Connectivity | Infrastructure | Traffic stress | Transit |
| --- | --- | --- | --- | --- | --- |
| General adult | 30 | 20 | 20 | 15 | 15 |
| Wheelchair user | 20 | 10 | 40 | 15 | 15 |
| Child walking to school | 20 | 10 | 25 | 35 | 10 |
| Night-time | 20 | 15 | 35 | 15 | 15 |

In v1 the child persona uses this weight row only. Schools get no extra weight.

## Combined score

For each segment: score = sum of (indicator score × persona weight) / 100.

**Impassable segments:** for the wheelchair persona, `highway=steps` segments get a combined score of **0**, whatever their indicator scores. This list is set per persona in `config/personas.yaml` (`impassable_highways`).

## Weakest streets

The weakest-streets table lists the lowest-scoring segments that have a `name`. Unnamed segments, such as most footways and service roads, are left out of the table but still appear on the map.

## Scenarios

- **Added crossing:** counts as a marked crossing for every segment within **50 m**, so their crossing sub-score is recalculated.
- **Added amenity:** counts for every segment that can reach it within **800 m along the network**, so their amenity score is recalculated.

## Data completeness

The tool shows, per area, the share of in-area segments that have:

- a `sidewalk` tag (pedestrian-only segments are left out of this share, because they need no sidewalk)
- a `kerb` value within 50 m
- a `lit` tag

It also shows the mean of the three as one headline figure. This way a low score caused by missing data is not mistaken for a bad street.

## Known limitation

OpenStreetMap tagging of sidewalks, kerbs and lighting is incomplete in many cities. Every missing tag is handled with the explicit assumption listed above, and counts toward the data-completeness figure where relevant.

**Separate sidewalk without a tag on the road.** A road can have a `footway=sidewalk` way drawn beside it, but no `sidewalk=separate` (or any other sidewalk tag) on the road itself. WalkScope does not detect this. Such a road gets the missing-tag sidewalk score of 50, and counts as missing in the data-completeness figure. In the Kreuzberg test area this does not happen on real streets: every residential, tertiary, secondary and primary road had a sidewalk tag. Of the 651 roads without a sidewalk tag, 628 were service roads. 432 of these had a sidewalk way within 15 m along at least half their length, but that was mostly the main street's sidewalk passing a driveway or courtyard entrance, not a sidewalk belonging to the service road. Most of those service roads are now routing-only anyway (driveways and parking aisles). If other cities show the problem on real streets, a geometric check (a sidewalk way within about 15 m along most of the road) can be added later.
