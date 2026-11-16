# WalkScope – Scoring Method

Each street segment gets five indicator scores from 0 to 100, combined as a weighted average. Persona weights live in `config/personas.yaml`; every threshold, default and tag mapping lives in `config/indicators.yaml`. A persona is a different set of weights plus, for some personas, extra tags inside the infrastructure score.

## Indicators

| Indicator | What it measures | Data (all from OpenStreetMap) |
| --- | --- | --- |
| Amenity access | How many everyday categories (groceries, school, pharmacy, park, café, doctor) are within a 10-minute walk along the network | Points of interest and the walking network |
| Connectivity | Intersection density within 400 m; short blocks score higher | Walking network |
| Pedestrian infrastructure | Sidewalk present, marked crossings nearby, pedestrian-only street | `sidewalk`, `crossing`, `highway=footway/pedestrian` tags |
| Traffic stress | Road class and speed limit; a 30 km/h residential street scores above a 50 km/h main road | `highway`, `maxspeed`, `lanes` tags |
| Transit access | Walking distance to the nearest bus or rail stop | `public_transport` and `railway` tags |

## Study area and measuring point

- The OSM data is downloaded for the study area plus an **800 m buffer**, so that streets near the edge can reach amenities and stops outside it. Only segments inside the study area are scored and shown.
- Distances for a segment are measured from the **network node nearest to the segment midpoint**.
- Footways and pedestrian streets are scored as segments like any other street.

## Indicator definitions

### Amenity access

- Six categories: groceries, school, pharmacy, park, café, doctor. The OSM tags for each category are listed in the config.
- A category counts as reached if at least one of its points lies within **800 m along the walking network**. In v1 this applies to every persona. A walk speed is kept in the config for later versions, but it is not used yet.
- Score = categories reached ÷ 6 × 100.

### Connectivity

- Intersections are network nodes where three or more streets meet.
- Count the intersections within a **400 m straight-line radius** of the measuring node and divide by the circle's area to get intersections per km².
- Score is linear from **30 per km² (0 points) to 150 per km² (100 points)**, clipped to 0–100.

### Pedestrian infrastructure

Base score:

- Pedestrian-only segments (`highway=footway` or `highway=pedestrian`, list in the config) score **100**.
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

`incline` is dropped for v1.

### Traffic stress

- Pedestrian-only segments score **100**.
- Speed comes from `maxspeed`. If it is missing, the **German default speed for the road class** (in the config) is used. Zone values such as `DE:zone30` and `DE:urban` are translated using the config. If a segment has several values, the highest one is used.
- Speed score, linear between these points: 10 km/h → 100, 30 km/h → 75, 50 km/h → 30, 70 km/h → 0.
- Lanes: −10 points for each lane above 2 (from `lanes`). If `lanes` is missing, there is no penalty.
- Clipped to 0–100.

### Transit access

- Walking distance along the network to the nearest bus, tram or rail stop.
- **100 within 300 m**, falling linearly to **0 at 800 m**. A segment with no stop reachable gets 0.

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
