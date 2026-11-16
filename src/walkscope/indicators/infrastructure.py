"""Pedestrian infrastructure: sidewalks, crossings, kerbs, surface and lighting.

Rules (docs/method.md, values in config/indicators.yaml):
- base score: pedestrian-only segments 100; others 60 % sidewalk + 40 % crossing;
- sidewalk from `sidewalk`, or else from `sidewalk:left/right/both`;
- crossing: marked crossing node within 50 m = 100, only unmarked = 50, none = 0;
- kerb: worst kerb node within 50 m;
- surface: `sidewalk:surface`, else `surface`;
- lit: `lit`.
A missing or unreadable tag scores `missing_score` (50). A merged segment
with several values gets the worst one.

The sub-scores don't depend on the persona. `infrastructure_score` mixes them
with a persona's `infrastructure_mix`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree

from walkscope.config import Persona
from walkscope.tags import as_list, is_pedestrian_only, road_classes

SUBSCORES = ("sidewalk", "crossing", "kerb", "surface", "lit", "base")


def subscore_column(part: str) -> str:
    """Column name of a sub-score; prefixed so it doesn't clash with the OSM tag columns."""
    return f"infra_{part}"


def worst_value(values: Iterable[str], scores: Mapping[str, float]) -> float:
    """Lowest score among the values that have one; NaN if none do."""
    known = [float(scores[v]) for v in values if v in scores]
    return min(known) if known else np.nan


# --- Sidewalk -----------------------------------------------------------------

def sidewalk_values(tags: Mapping[str, Any], sides: Mapping[str, Iterable[str]]) -> list[str]:
    """The segment's sidewalk value(s), in terms of the `sidewalk=` tag.

    Uses `sidewalk` if present. Otherwise builds one value from the side tags
    (`sidewalk:left`, `sidewalk:right`, falling back to `sidewalk:both` for
    each side): both sides present -> "both", one side -> "left"/"right",
    both sides absent -> "no", anything else -> [] (unknown). `sides` lists
    the values that mean present (`yes`, `separate`) and absent (`no`, `none`).
    """
    main = as_list(tags.get("sidewalk"))
    if main:
        return main

    present, absent = set(sides["present"]), set(sides["absent"])
    both = as_list(tags.get("sidewalk:both"))

    def side(name: str) -> bool | None:
        values = as_list(tags.get(f"sidewalk:{name}")) or both
        if any(v in present for v in values):
            return True
        if values and all(v in absent for v in values):
            return False
        return None

    left, right = side("left"), side("right")
    if left and right:
        return ["both"]
    if left:
        return ["left"]
    if right:
        return ["right"]
    if left is False and right is False:
        return ["no"]
    return []


def sidewalk_subscore(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    cfg = params["infrastructure"]
    tag_cols = [c for c in ("sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right")
                if c in segments.columns]
    values = [
        worst_value(sidewalk_values(dict(zip(tag_cols, row)), cfg["sidewalk_sides"]), cfg["sidewalk"])
        for row in zip(*(segments[c] for c in tag_cols))
    ]
    return pd.Series(values, index=segments.index, dtype=float).fillna(cfg["missing_score"])


# --- Tag-only sub-scores ------------------------------------------------------

def surface_subscore(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Score of the first surface tag present (`sidewalk:surface`, then `surface`)."""
    cfg = params["infrastructure"]

    def score(row: tuple) -> float:
        for value in row:
            values = as_list(value)
            if values:
                return worst_value(values, cfg["surface"]["values"])
        return np.nan

    tag_cols = cfg["surface"]["tags"]
    cols = [segments[c] if c in segments.columns else pd.Series(None, index=segments.index)
            for c in tag_cols]
    values = [score(row) for row in zip(*cols)]
    return pd.Series(values, index=segments.index, dtype=float).fillna(cfg["missing_score"])


def lit_subscore(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    cfg = params["infrastructure"]
    values = segments["lit"].map(lambda v: worst_value(as_list(v), cfg["lit"]["values"]))
    return values.astype(float).fillna(cfg["missing_score"])


# --- Crossings and kerbs (points near the segment) ---------------------------

def split_street_points(street_points: gpd.GeoDataFrame,
                        params: dict[str, Any]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Crossing nodes (column `marked`) and kerb nodes (column `kerb_score`).

    A crossing is a node with `highway=crossing` or a `crossing` tag, except
    the values in `crossing.ignored_values` (`no`: crossing not possible here;
    `informal`: not a designated crossing). It is marked if any of its
    `crossing` values is in `marked_values`. Kerb nodes whose `kerb` values
    are all unknown (e.g. `kerb=yes`) are dropped.
    """
    cfg = params["infrastructure"]
    marked_values = set(cfg["crossing"]["marked_values"])
    ignored = set(cfg["crossing"]["ignored_values"])

    crossing_vals = street_points["crossing"].map(as_list)
    is_crossing_node = street_points["highway"].map(lambda h: "crossing" in road_classes(h))
    has_crossing_tag = crossing_vals.map(bool)
    ignore = crossing_vals.map(lambda vs: bool(vs) and all(v in ignored for v in vs))
    is_crossing = ((is_crossing_node | has_crossing_tag) & ~ignore).to_numpy(dtype=bool)
    crossings = street_points.loc[is_crossing, ["geometry"]].copy()
    crossings["marked"] = crossing_vals[is_crossing].map(
        lambda vs: any(v in marked_values for v in vs)).to_numpy(dtype=bool)

    kerb_score = street_points["kerb"].map(
        lambda v: worst_value(as_list(v), cfg["kerb"]["values"])).astype(float)
    has_kerb = kerb_score.notna().to_numpy()
    kerbs = street_points.loc[has_kerb, ["geometry"]].copy()
    kerbs["kerb_score"] = kerb_score[has_kerb].to_numpy()
    return gpd.GeoDataFrame(crossings, crs=street_points.crs), gpd.GeoDataFrame(kerbs, crs=street_points.crs)


def points_near(segments: gpd.GeoDataFrame, points: gpd.GeoDataFrame,
                radius_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Positions (segment, point) of every point within radius_m of a segment line."""
    if len(segments) == 0 or len(points) == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    tree = STRtree(points.geometry.to_numpy())
    seg_pos, pt_pos = tree.query(segments.geometry.to_numpy(), predicate="dwithin", distance=radius_m)
    return seg_pos, pt_pos


def _per_segment(segments: pd.DataFrame, seg_pos: np.ndarray, values: np.ndarray, how: str) -> pd.Series:
    """Aggregate point values per segment ('any' or 'min'); NaN for segments with no points."""
    grouped = pd.Series(values, index=seg_pos).groupby(level=0).agg(how)
    out = pd.Series(np.nan, index=range(len(segments)), dtype=float)
    out[grouped.index] = grouped.astype(float)
    return pd.Series(out.to_numpy(), index=segments.index)


def crossing_subscore(segments: gpd.GeoDataFrame, crossings: gpd.GeoDataFrame,
                      params: dict[str, Any]) -> pd.Series:
    cfg = params["infrastructure"]["crossing"]
    seg_pos, pt_pos = points_near(segments, crossings, cfg["search_radius_m"])
    marked = crossings["marked"].to_numpy(dtype=bool)[pt_pos]
    any_marked = _per_segment(segments, seg_pos, marked, "any")   # NaN: no crossing nearby
    return any_marked.map({1.0: cfg["marked_score"], 0.0: cfg["unmarked_score"]}).fillna(
        cfg["none_score"]).astype(float)


def kerb_subscore(segments: gpd.GeoDataFrame, kerbs: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.Series:
    cfg = params["infrastructure"]
    seg_pos, pt_pos = points_near(segments, kerbs, cfg["kerb"]["search_radius_m"])
    worst = _per_segment(segments, seg_pos, kerbs["kerb_score"].to_numpy(dtype=float)[pt_pos], "min")
    return worst.fillna(cfg["missing_score"])


# --- Combined -----------------------------------------------------------------

def infrastructure_subscores(segments: gpd.GeoDataFrame, crossings: gpd.GeoDataFrame,
                             kerbs: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """All sub-scores, 0-100, one column per part (`infra_sidewalk`, ..., `infra_base`).

    `crossings` and `kerbs` come from split_street_points, in the segments' CRS.
    """
    cfg = params["infrastructure"]
    out = pd.DataFrame({
        "sidewalk": sidewalk_subscore(segments, params),
        "crossing": crossing_subscore(segments, crossings, params),
        "kerb": kerb_subscore(segments, kerbs, params),
        "surface": surface_subscore(segments, params),
        "lit": lit_subscore(segments, params),
    }, index=segments.index)

    mix = cfg["base_mix"]
    base = (out["sidewalk"] * mix["sidewalk"] + out["crossing"] * mix["crossing"]) / 100
    pedestrian = segments["highway"].map(
        lambda h: is_pedestrian_only(h, params["pedestrian_only_highways"])).astype(bool)
    base[pedestrian] = float(cfg["pedestrian_only_base"])
    out["base"] = base
    return out.rename(columns=subscore_column)


def infrastructure_score(subscores: pd.DataFrame, persona: Persona) -> pd.Series:
    """Infrastructure score, 0-100, mixing the sub-scores by the persona's `infrastructure_mix`."""
    score = sum(subscores[subscore_column(part)] * share
                for part, share in persona.infrastructure_mix.items()) / 100
    return score.clip(0, 100).rename("infrastructure")
