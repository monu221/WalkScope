"""Amenity access: how many everyday categories are within a 10-minute walk.

Rules (docs/method.md, values in config/indicators.yaml):
- categories and their OSM tags are listed under `amenity.categories`;
- a category is reached if any of its points is within `walking.max_distance_m`
  (800 m) along the network from the segment's measuring node, walking both
  directions on one-way streets;
- score = categories reached / number of categories * 100.

One network search per category, from all of its points at once.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import networkx as nx
import pandas as pd

from walkscope.network import nearest_nodes, walk_distances


def reached_column(category: str) -> str:
    return f"amenity_{category}"


def amenities_reached(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, segments: pd.DataFrame,
                      pois: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """One boolean column per category (`amenity_groceries`, ...): reached within the walking distance.

    `pois` has a `category` column (see fetch.classify_features); a point
    that matches several categories appears once per category.
    """
    cutoff = float(params["walking"]["max_distance_m"])
    poi_nodes = nearest_nodes(nodes, pois.geometry)
    out = {}
    for category in params["amenity"]["categories"]:
        sources = poi_nodes[(pois["category"] == category).to_numpy()]
        reachable = walk_distances(graph, sources, cutoff)
        out[reached_column(category)] = segments["measure_node"].map(lambda n: n in reachable).astype(bool)
    return pd.DataFrame(out, index=segments.index)


def amenity_score(reached: pd.DataFrame) -> pd.Series:
    """0-100: share of categories reached."""
    return (reached.sum(axis=1) / reached.shape[1] * 100).astype(float).rename("amenity")
