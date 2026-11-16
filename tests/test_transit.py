import math

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point

from walkscope.config import load_indicator_params
from walkscope.indicators.transit import distance_score, stop_distance, transit_score
from walkscope.network import walk_distances

PARAMS = load_indicator_params()
CRS = "EPSG:25833"


@pytest.fixture
def line():
    """Nodes 0..10 on a straight line, 100 m apart, joined by ONE-WAY edges 0->1->...->10."""
    g = nx.MultiDiGraph(crs=CRS)
    for i in range(11):
        g.add_node(i, x=i * 100.0, y=0.0)
    for i in range(10):
        g.add_edge(i, i + 1, key=0, length=100.0)
    nodes = gpd.GeoDataFrame(index=range(11), geometry=[Point(i * 100, 0) for i in range(11)], crs=CRS)
    segments = pd.DataFrame({"measure_node": range(11)})
    return g, nodes, segments


def stops_at(*xy):
    return gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in xy], crs=CRS)


def test_walk_distances_ignore_one_way(line):
    g, _, _ = line
    dist = walk_distances(g, [10], cutoff=450)          # against the one-way direction
    assert dist == {10: 0, 9: 100, 8: 200, 7: 300, 6: 400}


def test_walk_distances_from_several_sources(line):
    g, _, _ = line
    assert walk_distances(g, [0, 10], cutoff=1000)[4] == 400
    assert walk_distances(g, [0, 10], cutoff=1000)[6] == 400
    assert walk_distances(g, [], cutoff=1000) == {}


def test_stop_distance_and_score(line):
    g, nodes, segments = line
    stops = stops_at((1005, 20))                         # snaps to node 10
    dist = stop_distance(g, nodes, segments, stops, PARAMS)
    expected = [math.nan, math.nan] + [800.0 - 100 * i for i in range(9)]   # nodes 0, 1 beyond 800 m
    pd.testing.assert_series_equal(dist, pd.Series(expected, name="transit_distance_m"), check_index_type=False)

    score = transit_score(g, nodes, segments, stops, PARAMS)
    # 900+ m and 800 m -> 0; 700 -> 20; 600 -> 40; 500 -> 60; 400 -> 80; 300 and closer -> 100
    assert score.tolist() == pytest.approx([0, 0, 0, 20, 40, 60, 80, 100, 100, 100, 100])
    assert score.name == "transit"


def test_nearest_of_several_stops(line):
    g, nodes, segments = line
    dist = stop_distance(g, nodes, segments, stops_at((0, 0), (1000, 0)), PARAMS)
    assert dist.tolist() == [0, 100, 200, 300, 400, 500, 400, 300, 200, 100, 0]


def test_no_stops_scores_zero(line):
    g, nodes, segments = line
    empty = gpd.GeoDataFrame(geometry=[], crs=CRS)
    assert stop_distance(g, nodes, segments, empty, PARAMS).isna().all()
    assert transit_score(g, nodes, segments, empty, PARAMS).eq(0).all()


@pytest.mark.parametrize("d, score", [(0, 100), (300, 100), (550, 50), (800, 0), (math.nan, 0)])
def test_distance_score(d, score):
    assert distance_score(pd.Series([d]), PARAMS).iloc[0] == pytest.approx(score)
