"""Transit access: walking distance to the nearest bus, tram or rail stop.

Rules (docs/method.md, values in config/indicators.yaml):
- stops are snapped to their nearest graph node;
- distance is walked along the network (both directions on one-way streets)
  from the segment's measuring node to the nearest stop;
- 100 points within `full_score_m` (300 m), falling linearly to 0 at
  `zero_score_m` (800 m); no stop within reach scores 0.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from walkscope.network import nearest_nodes, walk_distances


def stop_distance(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                  stops: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.Series:
    """Walking distance in metres to the nearest stop; NaN if none within zero_score_m."""
    cutoff = float(params["transit"]["zero_score_m"])
    stop_nodes = nearest_nodes(nodes, stops.geometry)
    dist = walk_distances(graph, stop_nodes, cutoff)
    return segments["measure_node"].map(dist).astype(float).rename("transit_distance_m")


def distance_score(distance_m: pd.Series, params: dict[str, Any]) -> pd.Series:
    """0-100 from a walking distance; NaN (unreachable) scores 0."""
    cfg = params["transit"]
    d = distance_m.to_numpy(dtype=float)
    score = np.interp(np.nan_to_num(d, nan=np.inf), [cfg["full_score_m"], cfg["zero_score_m"]], [100, 0])
    return pd.Series(score, index=distance_m.index, name="transit")


def transit_score(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                  stops: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.Series:
    """Transit-access score, 0-100, per segment."""
    return distance_score(stop_distance(graph, nodes, segments, stops, params), params)
