import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point

from walkscope.config import load_indicator_params
from walkscope.indicators.amenity import amenities_reached, amenity_score

PARAMS = load_indicator_params()
CATEGORIES = list(PARAMS["amenity"]["categories"])
CRS = "EPSG:25833"


@pytest.fixture
def line():
    """Nodes 0..20 on a line, 100 m apart, one-way edges 0->1->...->20."""
    g = nx.MultiDiGraph(crs=CRS)
    for i in range(21):
        g.add_node(i, x=i * 100.0, y=0.0)
    for i in range(20):
        g.add_edge(i, i + 1, key=0, length=100.0)
    nodes = gpd.GeoDataFrame(index=range(21), geometry=[Point(i * 100, 0) for i in range(21)], crs=CRS)
    segments = pd.DataFrame({"measure_node": [0, 5, 10, 20]}, index=[7, 8, 9, 10])
    return g, nodes, segments


def pois(*rows):
    """rows: (category, x)"""
    return gpd.GeoDataFrame({"category": [c for c, _ in rows]},
                            geometry=[Point(x, 15) for _, x in rows], crs=CRS)


def test_reached_within_800m_both_directions(line):
    g, nodes, segments = line
    p = pois(("groceries", 1000), ("groceries", 0), ("school", 1800), ("cafe", 500))
    reached = amenities_reached(g, nodes, segments, p, PARAMS)
    assert list(reached.columns) == [f"amenity_{c}" for c in CATEGORIES]
    assert list(reached.index) == [7, 8, 9, 10]
    # groceries at nodes 0 and 10: every measuring node is within 800 m of one (node 20 is 1000 m from both)
    assert reached["amenity_groceries"].tolist() == [True, True, True, False]
    # school at node 18: 800 m from node 10 (reached, against the one-way direction), 200 m from 20
    assert reached["amenity_school"].tolist() == [False, False, True, True]
    assert reached["amenity_cafe"].tolist() == [True, True, True, False]
    assert not reached["amenity_pharmacy"].any()

    score = amenity_score(reached)
    n = len(CATEGORIES)
    assert score.tolist() == pytest.approx([2 / n * 100, 2 / n * 100, 3 / n * 100, 1 / n * 100])
    assert score.name == "amenity"


def test_all_categories_reached_scores_100(line):
    g, nodes, segments = line
    p = pois(*[(c, 500) for c in CATEGORIES])
    score = amenity_score(amenities_reached(g, nodes, segments, p, PARAMS))
    assert score.tolist() == [100, 100, 100, 0]


def test_no_pois(line):
    g, nodes, segments = line
    empty = gpd.GeoDataFrame({"category": pd.Series(dtype=str)}, geometry=[], crs=CRS)
    reached = amenities_reached(g, nodes, segments, empty, PARAMS)
    assert not reached.any().any()
    assert amenity_score(reached).eq(0).all()
