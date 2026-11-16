"""Turn downloaded OSM data into the projected network and the segments table.

The segments table is the core data structure: one row per street segment,
in a metric CRS, with its OSM tags, its measuring node and whether it lies in
the study area. Indicators add columns to it in later steps.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
from shapely import STRtree
from shapely.geometry.base import BaseGeometry

from walkscope.fetch import EXTRA_WAY_TAGS, AreaData

# Tag columns every segments table has, even if no street in the area carries the tag.
TAG_COLUMNS = ("osmid", "name", "highway", "maxspeed", "lanes", *EXTRA_WAY_TAGS)


@dataclass
class Network:
    """All data for one area, projected to the same metric CRS."""

    crs: str
    area: BaseGeometry            # study area (projected)
    graph: nx.MultiDiGraph        # walking graph (projected; edge `length` in metres)
    nodes: gpd.GeoDataFrame       # graph nodes, index = OSM node id, column `street_count`
    segments: gpd.GeoDataFrame    # one row per street segment, see graph_to_segments
    pois: gpd.GeoDataFrame
    stops: gpd.GeoDataFrame
    street_points: gpd.GeoDataFrame


def nearest_nodes(nodes: gpd.GeoDataFrame, points: gpd.GeoSeries) -> pd.Series:
    """Id of the nearest graph node to each point (both in the same metric CRS)."""
    if len(points) == 0:
        return pd.Series([], index=points.index, dtype=nodes.index.dtype)
    tree = STRtree(nodes.geometry.to_numpy())
    idx = tree.query_nearest(points.to_numpy(), return_distance=False, all_matches=False)
    # idx[0] = position in `points`, idx[1] = position in `nodes`
    ids = pd.Series(nodes.index.values[idx[1]], index=points.index[idx[0]])
    return ids.loc[points.index]


def graph_to_segments(graph: nx.MultiDiGraph, nodes: gpd.GeoDataFrame, area: BaseGeometry) -> gpd.GeoDataFrame:
    """Build the segments table from a projected walking graph.

    Columns: u, v, key, length, geometry, the TAG_COLUMNS, `measure_node`
    (graph node nearest the segment midpoint) and `in_area` (midpoint inside
    the study area). Index: `segment_id`, 0..n-1.

    OSMnx walk graphs store every street in both directions; the graph is made
    undirected first so each street appears once. Tags merged by OSMnx
    simplification can be lists (e.g. two names); they are kept as is.
    """
    undirected = ox.convert.to_undirected(graph)
    edges = ox.graph_to_gdfs(undirected, nodes=False, fill_edge_geometry=True).reset_index()

    for col in TAG_COLUMNS:
        if col not in edges.columns:
            edges[col] = None

    midpoints = edges.geometry.interpolate(0.5, normalized=True)
    edges["measure_node"] = nearest_nodes(nodes, midpoints).to_numpy()
    edges["in_area"] = midpoints.within(area).to_numpy()

    cols = ["u", "v", "key", "length", *TAG_COLUMNS, "measure_node", "in_area", "geometry"]
    segments = gpd.GeoDataFrame(edges[cols], geometry="geometry", crs=edges.crs)
    segments.index.name = "segment_id"
    return segments


def build_network(data: AreaData) -> Network:
    """Project graph, area and features to the graph's local UTM zone and build segments."""
    graph = ox.project_graph(data.graph)
    crs = graph.graph["crs"]
    nodes = ox.graph_to_gdfs(graph, edges=False)
    if "street_count" not in nodes.columns:
        nodes["street_count"] = pd.Series(ox.stats.count_streets_per_node(graph))
    area = gpd.GeoSeries([data.area], crs="EPSG:4326").to_crs(crs).iloc[0]

    return Network(
        crs=str(crs),
        area=area,
        graph=graph,
        nodes=nodes[["street_count", "geometry"]],
        segments=graph_to_segments(graph, nodes, area),
        pois=data.pois.to_crs(crs),
        stops=data.stops.to_crs(crs),
        street_points=data.street_points.to_crs(crs),
    )
