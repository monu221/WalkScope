import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkscope.config import load_indicator_params, load_personas
from walkscope.indicators.infrastructure import (
    crossing_subscore,
    infrastructure_score,
    infrastructure_subscores,
    kerb_subscore,
    lit_subscore,
    sidewalk_subscore,
    sidewalk_values,
    split_street_points,
    surface_subscore,
    worst_value,
)

PARAMS = load_indicator_params()
PERSONAS = load_personas()
SIDES = PARAMS["infrastructure"]["sidewalk_sides"]
CRS = "EPSG:25833"   # metres


def frame(rows: list[dict]) -> pd.DataFrame:
    cols = ["highway", "sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right",
            "sidewalk:surface", "surface", "lit"]
    return pd.DataFrame([{c: r.get(c) for c in cols} for r in rows])


def test_worst_value():
    assert worst_value(["sett", "asphalt"], {"sett": 30, "asphalt": 100}) == 30
    assert worst_value(["yes", "unknown"], {"yes": 100}) == 100
    assert np.isnan(worst_value(["unknown"], {"yes": 100}))
    assert np.isnan(worst_value([], {"yes": 100}))


# --- sidewalk -------------------------------------------------------------------

@pytest.mark.parametrize("tags, expected", [
    ({"sidewalk": "both"}, ["both"]),
    ({"sidewalk": "separate", "sidewalk:left": "no"}, ["separate"]),     # main tag wins
    ({"sidewalk:left": "no", "sidewalk:right": "separate"}, ["right"]),  # common in Berlin
    ({"sidewalk:left": "yes", "sidewalk:right": "yes"}, ["both"]),
    ({"sidewalk:both": "separate"}, ["both"]),
    ({"sidewalk:both": "no"}, ["no"]),
    ({"sidewalk:both": "no", "sidewalk:left": "yes"}, ["left"]),         # side tag overrides :both
    ({"sidewalk:left": "no"}, []),                                       # other side unknown
    ({"sidewalk:right": "yes"}, ["right"]),
    ({}, []),
])
def test_sidewalk_values(tags, expected):
    assert sidewalk_values(tags, SIDES) == expected


def test_sidewalk_subscore():
    seg = frame([
        {"sidewalk": "both"},
        {"sidewalk": "separate"},
        {"sidewalk": "left"},
        {"sidewalk": "no"},
        {},                                                     # missing -> 50
        {"sidewalk": "yes"},                                    # not in the config -> 50
        {"sidewalk:left": "no", "sidewalk:right": "separate"},  # one side -> 60
        {"sidewalk:both": "no"},
        {"sidewalk": ["right", "no"]},                          # merged: worst
    ])
    assert sidewalk_subscore(seg, PARAMS).tolist() == [100, 100, 60, 0, 50, 50, 60, 0, 0]


# --- surface and lit ------------------------------------------------------------

def test_surface_subscore():
    seg = frame([
        {"surface": "asphalt"},
        {"surface": "sett"},
        {"surface": "compacted"},
        {"surface": "dirt"},
        {},
        {"surface": "woodchips"},                               # unknown value -> 50
        {"surface": "sett", "sidewalk:surface": "paving_stones"},  # sidewalk:surface first
        {"surface": "sett;asphalt"},                            # worst
    ])
    assert surface_subscore(seg, PARAMS).tolist() == [100, 30, 60, 0, 50, 50, 100, 30]


def test_surface_without_sidewalk_surface_column():
    seg = frame([{"surface": "asphalt"}]).drop(columns="sidewalk:surface")
    assert surface_subscore(seg, PARAMS).tolist() == [100]


def test_lit_subscore():
    seg = frame([{"lit": "yes"}, {"lit": "24/7"}, {"lit": "automatic"}, {"lit": "no"}, {},
                 {"lit": ["no", "yes"]}, {"lit": "limited"}])
    assert lit_subscore(seg, PARAMS).tolist() == [100, 100, 100, 0, 50, 0, 50]


# --- crossings and kerbs --------------------------------------------------------

@pytest.fixture
def street_points() -> gpd.GeoDataFrame:
    rows = [
        # highway, crossing, kerb, x, y
        ("crossing", "marked", None, 50, 10),         # 10 m from road A -> marked
        ("crossing", "unmarked", None, 50, 140),      # 40 m from road B -> unmarked
        ("crossing", None, None, 50, 260),            # highway=crossing without crossing tag -> unmarked
        (None, "no", None, 50, 400),                  # crossing=no -> ignored
        ("crossing", "informal", None, 50, 410),      # informal -> ignored
        (None, "traffic_signals", None, 500, 0),      # far from everything
        (None, None, "lowered", 10, 5),               # kerbs near road A
        (None, None, "raised", 90, -5),
        (None, None, "flush", 50, 140),               # near road B
        (None, None, "yes", 50, 255),                 # unknown kerb value -> dropped
    ]
    idx = pd.MultiIndex.from_tuples([("node", i) for i in range(len(rows))], names=["element", "id"])
    return gpd.GeoDataFrame(
        {"highway": [r[0] for r in rows], "crossing": [r[1] for r in rows], "kerb": [r[2] for r in rows]},
        geometry=[Point(r[3], r[4]) for r in rows], index=idx, crs=CRS,
    )


@pytest.fixture
def roads() -> gpd.GeoDataFrame:
    """Four 100 m east-west roads at y = 0, 100, 250 and 400."""
    return gpd.GeoDataFrame(
        {"highway": ["residential"] * 4, "sidewalk": [None] * 4, "lit": [None] * 4, "surface": [None] * 4},
        geometry=[LineString([(0, y), (100, y)]) for y in (0, 100, 250, 400)],
        index=pd.Index([10, 11, 12, 13], name="segment_id"), crs=CRS,
    )


def test_split_street_points(street_points):
    crossings, kerbs = split_street_points(street_points, PARAMS)
    assert crossings["marked"].tolist() == [True, False, False, True]
    assert kerbs["kerb_score"].tolist() == [100, 0, 100]
    assert crossings.crs == kerbs.crs == street_points.crs


def test_crossing_subscore(roads, street_points):
    crossings, _ = split_street_points(street_points, PARAMS)
    # A: marked 10 m away; B: unmarked 40 m away; C: highway=crossing 10 m away;
    # D: only crossing=no / informal nearby -> none.
    assert crossing_subscore(roads, crossings, PARAMS).tolist() == [100, 50, 50, 0]


def test_crossing_radius_is_50m(roads):
    crossings = gpd.GeoDataFrame({"marked": [True, True]},
                                 geometry=[Point(50, 49), Point(50, 251 + 51)], crs=CRS)
    assert crossing_subscore(roads, crossings, PARAMS).tolist() == [100, 0, 0, 0]


def test_kerb_subscore_takes_worst_nearby(roads, street_points):
    _, kerbs = split_street_points(street_points, PARAMS)
    # A: lowered + raised -> 0; B: flush 40 m away -> 100; C: only kerb=yes -> missing 50; D: none -> 50.
    assert kerb_subscore(roads, kerbs, PARAMS).tolist() == [0, 100, 50, 50]


def test_no_street_points(roads):
    empty = gpd.GeoDataFrame({"highway": [], "crossing": [], "kerb": []}, geometry=[], crs=CRS)
    crossings, kerbs = split_street_points(empty, PARAMS)
    sub = infrastructure_subscores(roads, crossings, kerbs, PARAMS)
    assert sub["infra_crossing"].eq(0).all()
    assert sub["infra_kerb"].eq(50).all()


# --- base score and persona mix -------------------------------------------------

def test_base_score(roads, street_points):
    roads = roads.copy()
    roads["sidewalk"] = ["both", "no", None, "separate"]
    roads.loc[13, "highway"] = "footway"
    crossings, kerbs = split_street_points(street_points, PARAMS)
    sub = infrastructure_subscores(roads, crossings, kerbs, PARAMS)
    # 60 % sidewalk + 40 % crossing; footway 100 whatever its sub-scores.
    assert sub["infra_base"].tolist() == [100, 20, 50, 100]
    assert list(sub.index) == list(roads.index)
    assert set(sub.columns) == {f"infra_{p}" for p in
                                ("sidewalk", "crossing", "kerb", "surface", "lit", "base")}


def test_persona_mix():
    sub = pd.DataFrame({"infra_base": [80.0], "infra_kerb": [0.0], "infra_surface": [100.0],
                        "infra_lit": [0.0], "infra_sidewalk": [0.0], "infra_crossing": [0.0]})
    assert infrastructure_score(sub, PERSONAS["general_adult"]).iloc[0] == 80
    assert infrastructure_score(sub, PERSONAS["child"]).iloc[0] == 80
    assert infrastructure_score(sub, PERSONAS["wheelchair"]).iloc[0] == 0.5 * 80 + 0.25 * 0 + 0.25 * 100
    assert infrastructure_score(sub, PERSONAS["night"]).iloc[0] == 0.6 * 80 + 0.4 * 0
    assert infrastructure_score(sub, PERSONAS["night"]).name == "infrastructure"
