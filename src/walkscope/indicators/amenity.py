"""Amenity access: how close the everyday categories are on foot.

Rules (docs/method.md, values in config/indicators.yaml):
- categories and their OSM tags are listed under `amenity.categories`;
- for each category, the walking distance along the network (both directions
  on one-way streets) from the segment's measuring node to its nearest point;
- each category scores 100 within `full_score_m` (200 m), falling linearly
  to 0 at `zero_score_m` (800 m);
- the amenity score is the average of the category scores.

One network search per category, from all of its points at once.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd

from walkscope.network import nearest_nodes, walk_distances


def distance_column(category: str) -> str:
    return f"amenity_{category}_m"


def score_column(category: str) -> str:
    return f"amenity_{category}"


def amenity_distances(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                      pois: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Walking distance in metres to the nearest point of each category (`amenity_groceries_m`, ...).

    NaN if none is within `zero_score_m`. `pois` has a `category` column (see
    fetch.classify_features); a point in several categories appears once per category.
    """
    cutoff = float(params["amenity"]["zero_score_m"])
    poi_nodes = nearest_nodes(nodes, pois.geometry)
    out = {}
    for category in params["amenity"]["categories"]:
        sources = poi_nodes[(pois["category"] == category).to_numpy()]
        dist = walk_distances(graph, sources, cutoff)
        out[distance_column(category)] = segments["measure_node"].map(dist).astype(float)
    return pd.DataFrame(out, index=segments.index)


def category_scores(distances: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """0-100 per category from the distances; NaN (none within reach) scores 0."""
    cfg = params["amenity"]
    out = {}
    for category in cfg["categories"]:
        d = distances[distance_column(category)].to_numpy(dtype=float)
        out[score_column(category)] = np.interp(np.nan_to_num(d, nan=np.inf),
                                                [cfg["full_score_m"], cfg["zero_score_m"]], [100, 0])
    return pd.DataFrame(out, index=distances.index)


def amenity_score(scores: pd.DataFrame) -> pd.Series:
    """0-100: average of the category scores."""
    return scores.mean(axis=1).astype(float).rename("amenity")
