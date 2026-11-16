"""Traffic stress: how calm the motor traffic next to a pedestrian is.

Rules (docs/method.md, values in config/indicators.yaml):
- pedestrian-only segments score 100;
- otherwise the speed limit is mapped through a piecewise-linear curve,
  using `maxspeed` or, if missing/unreadable, the default for the road class;
  a segment merged from several OSM ways gets the length-weighted average of
  its pieces' speed scores;
- that speed score is multiplied by the road-class factor (a stand-in for
  traffic volume: primary 0.6, residential 1.0, ...);
- each lane above `lanes_free` costs `lane_penalty` points;
- the result is clipped to 0-100.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from walkscope.tags import as_list, is_pedestrian_only, road_classes

MPH_TO_KMH = 1.609344
_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?$", re.IGNORECASE)


def parse_maxspeed(value: Any, aliases: dict[str, float]) -> float | None:
    """Speed limit in km/h from a maxspeed tag; None if missing or unreadable.

    Accepts numbers, "50", "30 mph", zone values via `aliases` (e.g. "DE:zone30"),
    and several values ("30;50" or a list), in which case the highest is used.
    """
    lower_aliases = {k.lower(): float(v) for k, v in aliases.items()}
    speeds: list[float] = []
    for item in as_list(value):
        alias = lower_aliases.get(item.lower())
        if alias is not None:
            speeds.append(alias)
            continue
        m = _NUMBER.match(item)
        if m:
            speed = float(m.group(1))
            if (m.group(2) or "").lower() == "mph":
                speed *= MPH_TO_KMH
            speeds.append(speed)
    return max(speeds) if speeds else None


def parse_lanes(value: Any) -> int | None:
    """Number of lanes; the highest if several values; None if missing or unreadable."""
    counts = []
    for item in as_list(value):
        try:
            counts.append(int(float(item)))
        except ValueError:
            continue
    return max(counts) if counts else None


def default_speed(highway: Any, defaults: dict[str, float]) -> float:
    """Default speed for the road class; the highest if the segment has several classes."""
    classes = road_classes(highway) or ["other"]
    return max(float(defaults.get(c, defaults["other"])) for c in classes)


def _parts(highway: Any, maxspeed: Any, length: Any, way_parts: Any) -> list[tuple[Any, Any, float]]:
    """The (highway, maxspeed, length) pieces of a segment; the whole segment if `way_parts` is missing."""
    if isinstance(way_parts, (list, tuple)) and len(way_parts) > 0:
        return [(h, m, float(l)) for h, m, l in way_parts]
    return [(highway, maxspeed, float(length) if length is not None else 1.0)]


def _weighted(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return float(np.mean(values))
    return float(np.dot(values, weights) / total)


def effective_speed(segments: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Speed used for each segment, its score, and how much of it came from `maxspeed` tags.

    A segment merged from several OSM ways (see fetch.simplify_with_way_parts)
    is scored piece by piece: each piece's speed is its own `maxspeed`, or the
    default for its own road class, and the segment's speed score is the
    length-weighted average of the pieces' scores. Pedestrian-only pieces of a
    road segment (e.g. a footway merged into it) score `pedestrian_only_score`.

    Columns:
    - `speed_score`: 0-100 before the road-class factor and lane penalty;
    - `speed_kmh`: length-weighted average speed of the road pieces;
    - `speed_min_kmh`, `speed_max_kmh`: lowest and highest piece speed;
    - `speed_tagged_share`: share of the road pieces' length with a readable `maxspeed`;
    - `speed_tagged`: True if all of it is tagged.
    """
    cfg = params["traffic_stress"]
    aliases, defaults, curve = cfg["maxspeed_aliases"], cfg["default_speed_kmh"], cfg["speed_curve"]
    pedestrian = params["pedestrian_only_highways"]
    parts_col = segments["way_parts"] if "way_parts" in segments.columns else pd.Series(None, index=segments.index)
    length_col = segments["length"] if "length" in segments.columns else pd.Series(1.0, index=segments.index)

    rows = []
    for highway, maxspeed, length, way_parts in zip(segments["highway"], segments["maxspeed"],
                                                    length_col, parts_col):
        scores, weights, speeds, road_len, tagged_len = [], [], [], [], 0.0
        for h, m, l in _parts(highway, maxspeed, length, way_parts):
            weights.append(l)
            if is_pedestrian_only(h, pedestrian):
                scores.append(float(cfg["pedestrian_only_score"]))
                continue
            tagged = parse_maxspeed(m, aliases)
            speed = tagged if tagged is not None else default_speed(h, defaults)
            scores.append(float(np.interp(speed, [p[0] for p in curve], [p[1] for p in curve])))
            speeds.append(speed)
            road_len.append(l)
            tagged_len += l if tagged is not None else 0.0
        if not speeds:   # only pedestrian pieces: describe it by the segment's own tags
            tagged = parse_maxspeed(maxspeed, aliases)
            speeds = [tagged if tagged is not None else default_speed(highway, defaults)]
            road_len, tagged_len = [1.0], 1.0 if tagged is not None else 0.0
        share = tagged_len / sum(road_len) if sum(road_len) > 0 else 0.0
        rows.append((_weighted(scores, weights), _weighted(speeds, road_len), min(speeds), max(speeds),
                     share, share >= 1.0 - 1e-9))
    return pd.DataFrame(rows, index=segments.index,
                        columns=["speed_score", "speed_kmh", "speed_min_kmh", "speed_max_kmh",
                                 "speed_tagged_share", "speed_tagged"]).astype(
        {"speed_tagged": bool, "speed_score": float, "speed_kmh": float})


def class_factor(highway: Any, factors: dict[str, float]) -> float:
    """Road-class factor; `_link` roads use their parent class, merged segments the lowest factor."""
    classes = road_classes(highway)
    return min((float(factors.get(c, 1.0)) for c in classes), default=1.0)


def speed_score(speed_kmh: pd.Series | np.ndarray, curve: list[list[float]]) -> np.ndarray:
    """Map speeds to 0-100 along the curve; flat beyond its first and last points."""
    xs = [p[0] for p in curve]
    ys = [p[1] for p in curve]
    return np.interp(np.asarray(speed_kmh, dtype=float), xs, ys)


def traffic_score(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Traffic-stress score, 0-100, per segment (higher is calmer)."""
    cfg = params["traffic_stress"]
    pedestrian = segments["highway"].map(
        lambda h: is_pedestrian_only(h, params["pedestrian_only_highways"])
    ).astype(bool)

    factor = segments["highway"].map(lambda h: class_factor(h, cfg["class_factor"])).astype(float)
    score = effective_speed(segments, params)["speed_score"] * factor

    lanes = segments["lanes"].map(parse_lanes).astype(float)
    extra = (lanes - cfg["lanes_free"]).clip(lower=0).fillna(0)
    score -= extra * cfg["lane_penalty"]

    score[pedestrian] = cfg["pedestrian_only_score"]
    return score.clip(0, 100).rename("traffic_stress")
