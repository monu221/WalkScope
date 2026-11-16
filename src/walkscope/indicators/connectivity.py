"""Connectivity: how dense the street grid is around a segment.

Rules (docs/method.md, values in config/indicators.yaml):
- an intersection is a node where `min_street_count` (3) or more *streets*
  meet. Streets are the segments that are not routing-only (no separately
  drawn sidewalks, crossings, driveways, parking aisles) and, unless
  `count_pedestrian_only` is set, not pedestrian-only (footways, paths);
- count the intersections within `radius_m` (400 m, straight line) of the
  segment's measuring node and divide by the circle's area -> per km²;
- linear from `density_zero` (30/km², 0 points) to `density_full` (150/km², 100 points).

OSMnx's own `street_count` is not used: it counts every way at a node, so each
sidewalk-crossing junction would be an intersection and every segment in a
city would score 100.
"""

from __future__ import annotations

import math
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import STRtree

from walkscope.tags import is_pedestrian_only


def intersection_nodes(segments: pd.DataFrame, params: dict[str, Any]) -> pd.Index:
    """Ids of the nodes where at least `min_street_count` streets meet."""
    cfg = params["connectivity"]
    streets = ~segments["routing_only"].astype(bool)
    if not cfg["count_pedestrian_only"]:
        pedestrian = segments["highway"].map(
            lambda h: is_pedestrian_only(h, params["pedestrian_only_highways"])).astype(bool)
        streets &= ~pedestrian
    s = segments.loc[streets & (segments["u"] != segments["v"])]
    degree = pd.concat([s["u"], s["v"]]).value_counts()
    return degree.index[degree >= cfg["min_street_count"]]


def intersection_density(nodes: gpd.GeoDataFrame, segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Intersections per km² within the radius of each segment's measuring node.

    `nodes` is the projected node table (index = node id); `segments` must be
    the whole segments table (including routing-only and out-of-area rows),
    since intersections are found from it.
    """
    radius = float(params["connectivity"]["radius_m"])
    intersections = nodes.geometry.loc[intersection_nodes(segments, params)]

    centres = segments["measure_node"].unique()
    counts = np.zeros(len(centres))
    if len(intersections):
        tree = STRtree(intersections.to_numpy())
        centre_pos, _ = tree.query(nodes.geometry.loc[centres].to_numpy(), predicate="dwithin", distance=radius)
        counts = np.bincount(centre_pos, minlength=len(centres)).astype(float)

    circle_km2 = math.pi * radius**2 / 1e6
    per_node = pd.Series(counts / circle_km2, index=centres)
    return segments["measure_node"].map(per_node).astype(float).rename("intersection_density")


def density_score(density: pd.Series, params: dict[str, Any]) -> pd.Series:
    """0-100, linear between density_zero and density_full, flat outside."""
    cfg = params["connectivity"]
    score = np.interp(density.to_numpy(dtype=float), [cfg["density_zero"], cfg["density_full"]], [0, 100])
    return pd.Series(score, index=density.index, name="connectivity")


def connectivity_score(nodes: gpd.GeoDataFrame, segments: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Connectivity score, 0-100, per segment."""
    return density_score(intersection_density(nodes, segments, params), params)
