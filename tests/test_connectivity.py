import copy
import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from walkscope.config import load_indicator_params
from walkscope.indicators.connectivity import (
    connectivity_score,
    density_score,
    intersection_density,
    intersection_nodes,
)
from walkscope.network import build_network

PARAMS = load_indicator_params()
CIRCLE_KM2 = math.pi * 0.4**2


def with_pedestrian(flag: bool) -> dict:
    params = copy.deepcopy(PARAMS)
    params["connectivity"]["count_pedestrian_only"] = flag
    return params


def seg_table(rows):
    """rows: (u, v, highway, routing_only)"""
    return pd.DataFrame(rows, columns=["u", "v", "highway", "routing_only"]).assign(measure_node=0)


@pytest.mark.parametrize("rows, roads_only, with_footways", [
    ([(0, 1, "residential", False), (0, 2, "residential", False), (0, 3, "primary", False)], [0], [0]),
    ([(0, 1, "residential", False), (0, 2, "residential", False), (0, 3, "footway", False)], [], [0]),
    ([(0, 1, "residential", False), (0, 2, "residential", False), (0, 3, "footway", True)], [], []),
    ([(0, 1, "residential", False), (0, 2, "residential", False), (0, 0, "residential", False)], [], []),
    ([(0, 1, "residential", False), (0, 2, ["residential", "footway"], False),
      (0, 3, "service", False)], [0], [0]),                       # merged road+footway counts as a road
])
def test_intersection_nodes(rows, roads_only, with_footways):
    segs = seg_table(rows)
    assert list(intersection_nodes(segs, with_pedestrian(False))) == roads_only
    assert list(intersection_nodes(segs, with_pedestrian(True))) == with_footways


def test_density_counts_intersections_within_radius():
    # Node 0 at the origin is the measuring node. Intersections (3 roads each)
    # at nodes 10 (300 m away) and 20 (500 m away); only node 10 is within 400 m.
    nodes = gpd.GeoDataFrame(
        index=[0, 10, 11, 12, 13, 20, 21, 22, 23],
        geometry=[Point(0, 0), Point(300, 0), Point(300, 50), Point(300, -50), Point(350, 0),
                  Point(500, 0), Point(500, 50), Point(500, -50), Point(550, 0)],
        crs="EPSG:25833",
    )
    segs = seg_table([(10, 11, "residential", False), (10, 12, "residential", False),
                      (10, 13, "residential", False), (20, 21, "residential", False),
                      (20, 22, "residential", False), (20, 23, "residential", False)])
    density = intersection_density(nodes, segs, PARAMS)
    assert density.tolist() == pytest.approx([1 / CIRCLE_KM2] * 6)
    assert density.name == "intersection_density"


@pytest.mark.parametrize("density, score", [(0, 0), (30, 0), (90, 50), (150, 100), (400, 100)])
def test_density_score(density, score):
    assert density_score(pd.Series([float(density)]), PARAMS).iloc[0] == pytest.approx(score)


def test_grid_fixture(area_data):
    # The grid's rows are residential streets and its columns footways. Counting
    # roads only, no node has 3 streets; counting footways too, the centre node
    # has 4 and the four edge-midpoint nodes have 3.
    net = build_network(area_data)
    assert intersection_density(net.nodes, net.segments, PARAMS).eq(0).all()
    density = intersection_density(net.nodes, net.segments, with_pedestrian(True))
    assert density.tolist() == pytest.approx([5 / CIRCLE_KM2] * 12)
    assert connectivity_score(net.nodes, net.segments, PARAMS).eq(0).all()
