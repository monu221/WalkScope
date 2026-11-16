"""Traffic stress: how calm the motor traffic next to a pedestrian is.

Rules (docs/method.md, values in config/indicators.yaml):
- pedestrian-only segments score 100;
- otherwise the speed limit is mapped through a piecewise-linear curve,
  using `maxspeed` or, if missing/unreadable, the default for the road class;
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


def effective_speed(segments: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Speed used for each segment and whether it came from a `maxspeed` tag.

    Columns: `speed_kmh` (float), `speed_tagged` (bool).
    """
    cfg = params["traffic_stress"]
    tagged = segments["maxspeed"].map(lambda v: parse_maxspeed(v, cfg["maxspeed_aliases"])).astype(float)
    defaults = segments["highway"].map(lambda h: default_speed(h, cfg["default_speed_kmh"])).astype(float)
    return pd.DataFrame(
        {"speed_kmh": tagged.fillna(defaults), "speed_tagged": tagged.notna()},
        index=segments.index,
    )


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

    speed = effective_speed(segments, params)["speed_kmh"]
    factor = segments["highway"].map(lambda h: class_factor(h, cfg["class_factor"])).astype(float)
    score = pd.Series(speed_score(speed, cfg["speed_curve"]), index=segments.index) * factor

    lanes = segments["lanes"].map(parse_lanes).astype(float)
    extra = (lanes - cfg["lanes_free"]).clip(lower=0).fillna(0)
    score -= extra * cfg["lane_penalty"]

    score[pedestrian] = cfg["pedestrian_only_score"]
    return score.clip(0, 100).rename("traffic_stress")
