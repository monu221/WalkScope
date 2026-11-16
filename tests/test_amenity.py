import math

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import Point

from walkscope.config import load_indicator_params
from walkscope.indicators.amenity import amenity_distances, amenity_score, category_scores

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


def scores_for(line, p):
    g, nodes, segments = line
    return category_scores(amenity_distances(g, nodes, segments, p, PARAMS), PARAMS)


def test_thresholds_in_config():
    assert (PARAMS["amenity"]["full_score_m"], PARAMS["amenity"]["zero_score_m"]) == (200, 800)


def test_distances_to_nearest_point_both_directions(line):
    g, nodes, segments = line
    p = pois(("groceries", 1000), ("groceries", 0), ("school", 1800), ("cafe", 500))
    dist = amenity_distances(g, nodes, segments, p, PARAMS)
    assert list(dist.columns) == [f"amenity_{c}_m" for c in CATEGORIES]
    assert list(dist.index) == [7, 8, 9, 10]
    # Measuring nodes 0, 5, 10, 20.
    assert dist["amenity_groceries_m"].tolist()[:3] == [0, 500, 0]
    assert math.isnan(dist["amenity_groceries_m"].iloc[3])            # 1,000 m: beyond reach
    assert dist["amenity_school_m"].tolist()[2:] == [800, 200]         # against the one-way direction
    assert dist["amenity_cafe_m"].tolist()[:3] == [500, 0, 500]
    assert dist["amenity_pharmacy_m"].isna().all()


def test_category_and_amenity_scores(line):
    p = pois(("groceries", 1000), ("groceries", 0), ("school", 1800), ("cafe", 500))
    scores = scores_for(line, p)
    assert list(scores.columns) == [f"amenity_{c}" for c in CATEGORIES]
    assert scores["amenity_groceries"].tolist() == pytest.approx([100, 50, 100, 0])
    assert scores["amenity_school"].tolist() == pytest.approx([0, 0, 0, 100])
    assert scores["amenity_cafe"].tolist() == pytest.approx([50, 100, 50, 0])
    total = amenity_score(scores)
    assert total.tolist() == pytest.approx([150 / 6, 150 / 6, 150 / 6, 100 / 6])
    assert total.name == "amenity"


@pytest.mark.parametrize("x, expected", [
    (0, 100), (200, 100), (500, 50), (700, 100 / 6), (800, 0), (900, 0),
])
def test_score_falls_linearly(line, x, expected):
    # Every category at the same distance x from measuring node 0.
    total = amenity_score(scores_for(line, pois(*[(c, x) for c in CATEGORIES])))
    assert total.iloc[0] == pytest.approx(expected)


def test_no_pois(line):
    empty = gpd.GeoDataFrame({"category": pd.Series(dtype=str)}, geometry=[], crs=CRS)
    assert amenity_score(scores_for(line, empty)).eq(0).all()
