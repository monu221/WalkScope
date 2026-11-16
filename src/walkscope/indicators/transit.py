"""Transit access: walking distance to the nearest stop, with rail stations reaching further.

Rules (docs/method.md, values in config/indicators.yaml):
- stops are snapped to their nearest graph node; distance is walked along the
  network (both directions on one-way streets) from the segment's measuring node;
- any stop (bus, tram, rail): 100 within `stop.full_score_m` (150 m), 0 at
  `stop.zero_score_m` (800 m);
- rail stations (rail, U-Bahn, S-Bahn; `rail_station` column of the stops):
  100 within `rail_station.full_score_m` (300 m), 0 at `rail_station.zero_score_m` (1,000 m);
- the score is the better of the two; nothing within reach scores 0.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from walkscope.network import nearest_nodes, walk_distances


def nearest_distance(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                     points: gpd.GeoDataFrame, cutoff_m: float) -> pd.Series:
    """Walking distance in metres from each segment's measuring node to the nearest point; NaN beyond cutoff."""
    dist = walk_distances(graph, nearest_nodes(nodes, points.geometry), cutoff_m)
    return segments["measure_node"].map(dist).astype(float)


def stop_distances(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                   stops: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Columns `transit_distance_m` (any stop) and `station_distance_m` (rail station); NaN beyond reach."""
    cfg = params["transit"]
    rail = stops["rail_station"].to_numpy(dtype=bool) if "rail_station" in stops.columns \
        else np.zeros(len(stops), dtype=bool)
    return pd.DataFrame({
        "transit_distance_m": nearest_distance(graph, nodes, segments, stops,
                                               cfg["stop"]["zero_score_m"]),
        "station_distance_m": nearest_distance(graph, nodes, segments, stops[rail],
                                               cfg["rail_station"]["zero_score_m"]),
    }, index=segments.index)


def distance_score(distance_m: pd.Series, full_m: float, zero_m: float) -> pd.Series:
    """100 within full_m, linear to 0 at zero_m; NaN (unreachable) scores 0."""
    d = np.nan_to_num(distance_m.to_numpy(dtype=float), nan=np.inf)
    return pd.Series(np.interp(d, [full_m, zero_m], [100, 0]), index=distance_m.index)


def transit_score(distances: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    """Transit-access score, 0-100: the better of the stop score and the rail-station score."""
    cfg = params["transit"]
    stop = distance_score(distances["transit_distance_m"], cfg["stop"]["full_score_m"], cfg["stop"]["zero_score_m"])
    rail = distance_score(distances["station_distance_m"], cfg["rail_station"]["full_score_m"],
                          cfg["rail_station"]["zero_score_m"])
    return np.maximum(stop, rail).rename("transit")
