import math

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point

from walkscope.config import load_indicator_params
from walkscope.indicators.transit import distance_score, stop_distances, transit_score
from walkscope.network import walk_distances

PARAMS = load_indicator_params()
CRS = "EPSG:25833"
N = 16      # nodes 0..15, 100 m apart


@pytest.fixture
def line():
    """Nodes 0..15 on a straight line, 100 m apart, joined by ONE-WAY edges 0->1->...->15."""
    g = nx.MultiDiGraph(crs=CRS)
    for i in range(N):
        g.add_node(i, x=i * 100.0, y=0.0)
    for i in range(N - 1):
        g.add_edge(i, i + 1, key=0, length=100.0)
    nodes = gpd.GeoDataFrame(index=range(N), geometry=[Point(i * 100, 0) for i in range(N)], crs=CRS)
    segments = pd.DataFrame({"measure_node": range(N)})
    return g, nodes, segments


def stops(*rows):
    """rows: (x, rail_station)"""
    return gpd.GeoDataFrame({"rail_station": [r for _, r in rows]},
                            geometry=[Point(x, 20) for x, _ in rows], crs=CRS)


def lin(d, full, zero):
    return max(0.0, min(100.0, (zero - d) / (zero - full) * 100))


def test_walk_distances_ignore_one_way(line):
    g, _, _ = line
    dist = walk_distances(g, [10], cutoff=250)          # 9 and 8 are against the one-way direction
    assert dist == {10: 0, 9: 100, 8: 200, 11: 100, 12: 200}


def test_walk_distances_from_several_sources(line):
    g, _, _ = line
    assert walk_distances(g, [0, 10], cutoff=1000)[4] == 400
    assert walk_distances(g, [0, 10], cutoff=1000)[6] == 400
    assert walk_distances(g, [], cutoff=1000) == {}


def test_thresholds_in_config():
    t = PARAMS["transit"]
    assert (t["stop"]["full_score_m"], t["stop"]["zero_score_m"]) == (150, 800)
    assert (t["rail_station"]["full_score_m"], t["rail_station"]["zero_score_m"]) == (300, 1000)


def test_bus_stop_only(line):
    g, nodes, segments = line
    dist = stop_distances(g, nodes, segments, stops((0, False)), PARAMS)
    assert dist["transit_distance_m"].tolist()[:9] == [100.0 * i for i in range(9)]
    assert dist["transit_distance_m"].iloc[9:].isna().all()          # beyond 800 m
    assert dist["station_distance_m"].isna().all()
    score = transit_score(dist, PARAMS)
    assert score.tolist() == pytest.approx([lin(100 * i, 150, 800) for i in range(N)])
    assert score.iloc[1] == 100 and score.iloc[2] == pytest.approx(600 / 650 * 100)
    assert score.name == "transit"


def test_rail_station_reaches_further_and_better_score_wins(line):
    g, nodes, segments = line
    # Bus stop at node 0, rail station at node 15.
    dist = stop_distances(g, nodes, segments, stops((0, False), (1500, True)), PARAMS)
    station = dist["station_distance_m"]
    assert station.iloc[5:].tolist() == [100.0 * (15 - i) for i in range(5, N)]
    assert station.iloc[:5].isna().all()                              # beyond 1,000 m
    score = transit_score(dist, PARAMS)
    for i in range(N):
        d_stop = min(100 * i, 100 * (15 - i))                         # the station is a stop too
        expected = max(lin(d_stop, 150, 800), lin(100 * (15 - i), 300, 1000))
        assert score.iloc[i] == pytest.approx(expected), i
    # 700 m from the bus stop and 800 m from the station: the station wins.
    assert score.iloc[7] == pytest.approx(lin(800, 300, 1000))
    # Node 10: the bus stop is 1,000 m away (no score); the station, 500 m away,
    # scores 46 as a stop but 71 as a rail station.
    assert dist["transit_distance_m"].iloc[10] == 500                # nearest stop is the station
    assert score.iloc[10] == pytest.approx(lin(500, 300, 1000))
    assert score.iloc[10] > lin(500, 150, 800)


def test_no_stops_scores_zero(line):
    g, nodes, segments = line
    empty = gpd.GeoDataFrame({"rail_station": pd.Series(dtype=bool)}, geometry=[], crs=CRS)
    dist = stop_distances(g, nodes, segments, empty, PARAMS)
    assert dist.isna().all().all()
    assert transit_score(dist, PARAMS).eq(0).all()


def test_stops_without_rail_column_are_plain_stops(line):
    g, nodes, segments = line
    plain = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs=CRS)
    dist = stop_distances(g, nodes, segments, plain, PARAMS)
    assert dist["station_distance_m"].isna().all()


@pytest.mark.parametrize("d, score", [(0, 100), (150, 100), (475, 50), (800, 0), (math.nan, 0)])
def test_distance_score(d, score):
    assert distance_score(pd.Series([d]), 150, 800).iloc[0] == pytest.approx(score)
