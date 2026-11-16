"""What-if scenarios: add a crossing or an amenity and rescore only the segments it affects.

Rules (docs/method.md, "Scenarios"):
- an added crossing counts as a **marked** crossing for every segment within
  the crossing search radius (50 m), so their crossing sub-score, base
  infrastructure score and infrastructure score are recalculated;
- an added amenity is snapped to the nearest graph node; for every segment
  that reaches it within `amenity.zero_score_m` (800 m) along the network,
  the category distance becomes the shorter of the old and the new one, and
  the category score and amenity score are recalculated.

Edits are applied to a copy of the segments, in order; the scored network
itself is never changed (it is shared between app sessions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from walkscope.config import load_personas
from walkscope.indicators.amenity import amenity_score, category_scores, distance_column, score_column
from walkscope.indicators.infrastructure import infrastructure_score, points_near
from walkscope.network import Network, nearest_nodes, walk_distances
from walkscope.pipeline import DEFAULT_PERSONA
from walkscope.scoring import display_name, length_weighted_mean
from walkscope.tags import is_pedestrian_only

CROSSING = "crossing"
AMENITY = "amenity"
# A clicked amenity must be at most this far from the walking network.
MAX_SNAP_M = 100.0


class ScenarioError(ValueError):
    """The edit can't be placed here (message is shown to the user)."""


@dataclass(frozen=True)
class Edit:
    kind: str                       # CROSSING or AMENITY
    lon: float
    lat: float
    category: str | None = None     # amenity category (AMENITY only)

    def label(self) -> str:
        return "Marked crossing" if self.kind == CROSSING else f"Amenity: {self.category}"


def to_network_crs(net: Network, lon: float, lat: float) -> Point:
    return gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(net.crs).iloc[0]


def _pedestrian(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    allowed = params["pedestrian_only_highways"]
    return segments["highway"].map(lambda h: is_pedestrian_only(h, allowed)).astype(bool)


def add_crossing(net: Network, segments: gpd.GeoDataFrame, point: Point,
                 params: dict[str, Any]) -> pd.Index:
    """Add a marked crossing at `point` (network CRS), in place on `segments`. Returns affected ids.

    Raises ScenarioError if no road (not pedestrian-only) is within the search radius.
    """
    cfg = params["infrastructure"]
    radius = cfg["crossing"]["search_radius_m"]
    pts = gpd.GeoDataFrame(geometry=[point], crs=segments.crs)
    seg_pos, _ = points_near(segments, pts, radius)
    near = segments.index[np.unique(seg_pos)]
    roads = near[~_pedestrian(segments.loc[near], params).to_numpy()]
    if len(roads) == 0:
        raise ScenarioError(f"A crossing needs a road within {radius:g} m. Click on or next to a road.")

    marked = float(cfg["crossing"]["marked_score"])
    changed = roads[segments.loc[roads, "infra_crossing"].to_numpy() < marked]
    if len(changed):
        mix = cfg["base_mix"]
        segments.loc[changed, "infra_crossing"] = marked
        segments.loc[changed, "infra_base"] = (segments.loc[changed, "infra_sidewalk"] * mix["sidewalk"]
                                               + marked * mix["crossing"]) / 100
        _refresh_infrastructure(segments, changed)
    return changed


def add_amenity(net: Network, segments: gpd.GeoDataFrame, point: Point, category: str,
                params: dict[str, Any]) -> pd.Index:
    """Add an amenity of `category` at `point` (network CRS), in place on `segments`. Returns affected ids.

    Raises ScenarioError for an unknown category or a point too far from the network.
    """
    cfg = params["amenity"]
    if category not in cfg["categories"]:
        raise ScenarioError(f"Unknown amenity category: {category}.")
    node = nearest_nodes(net.nodes, gpd.GeoSeries([point], crs=net.nodes.crs)).iloc[0]
    if net.nodes.geometry.loc[node].distance(point) > MAX_SNAP_M:
        raise ScenarioError(f"Place the amenity within {MAX_SNAP_M:g} m of a street or path.")

    reach = walk_distances(net.graph, [node], float(cfg["zero_score_m"]))
    new = segments["measure_node"].map(reach).astype(float)
    col = distance_column(category)
    old = segments[col]
    better = new.notna() & (old.isna() | (new < old))
    changed = segments.index[better.to_numpy()]
    if len(changed):
        segments.loc[changed, col] = new[changed]
        distances = segments.loc[changed, [distance_column(c) for c in cfg["categories"]]]
        scores = category_scores(distances, params)
        segments.loc[changed, scores.columns] = scores
        segments.loc[changed, "amenity"] = amenity_score(scores)
    return changed


def _refresh_infrastructure(segments: pd.DataFrame, ids: pd.Index) -> None:
    """Keep the stored `infrastructure` column (default persona) in step with the sub-scores."""
    persona = load_personas()[DEFAULT_PERSONA]
    segments.loc[ids, "infrastructure"] = infrastructure_score(segments.loc[ids], persona)


def apply_edits(net: Network, edits: Iterable[Edit], params: dict[str, Any]) -> tuple[gpd.GeoDataFrame, pd.Index]:
    """Copy of net.segments with all edits applied in order, and the ids of every changed segment.

    Raises ScenarioError naming the first edit that can't be placed.
    """
    segments = net.segments.copy()
    changed: set = set()
    for edit in edits:
        point = to_network_crs(net, edit.lon, edit.lat)
        if edit.kind == CROSSING:
            ids = add_crossing(net, segments, point, params)
        elif edit.kind == AMENITY:
            ids = add_amenity(net, segments, point, edit.category, params)
        else:
            raise ScenarioError(f"Unknown edit: {edit.kind}.")
        changed.update(ids)
    return segments, pd.Index(sorted(changed), name=segments.index.name)


def check_edit(net: Network, edits: list[Edit], edit: Edit, params: dict[str, Any]) -> None:
    """Raise ScenarioError if `edit` can't be placed (checked on a scratch copy)."""
    apply_edits(net, [*edits, edit], params)


CHANGE_COLUMNS = ["Street", "Before", "After", "Change"]


def changed_streets(before: pd.DataFrame, after: pd.DataFrame, changed: pd.Index, column: str,
                    n: int = 10) -> pd.DataFrame:
    """Named streets whose `column` changed, most improved first: Street, Before, After, Change.

    Per street, the length-weighted mean over its changed scored segments,
    rounded; streets whose rounded change is 0 are left out.
    """
    ids = changed.intersection(before.index[before["scored"]])
    rows = pd.DataFrame({
        "Street": before.loc[ids, "name"].map(display_name),
        "length": before.loc[ids, "length"],
        "Before": before.loc[ids, column],
        "After": after.loc[ids, column],
    }).dropna(subset=["Street"])
    if rows.empty:
        return pd.DataFrame(columns=CHANGE_COLUMNS)
    by_street = rows.groupby("Street").apply(
        lambda g: pd.Series({"Before": length_weighted_mean(g["Before"], g["length"]),
                             "After": length_weighted_mean(g["After"], g["length"])}),
        include_groups=False).reset_index()
    by_street["Change"] = by_street["After"] - by_street["Before"]
    by_street = by_street[by_street["Change"].round() != 0].sort_values("Change", ascending=False)
    return by_street.head(n).round().astype({"Before": int, "After": int, "Change": int})[CHANGE_COLUMNS]
