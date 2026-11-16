import math

import pandas as pd
import pytest

from walkscope.config import load_indicator_params
from walkscope.indicators.traffic import (
    MPH_TO_KMH,
    class_factor,
    default_speed,
    effective_speed,
    parse_lanes,
    parse_maxspeed,
    traffic_score,
)
from walkscope.network import build_network

PARAMS = load_indicator_params()
ALIASES = PARAMS["traffic_stress"]["maxspeed_aliases"]
DEFAULTS = PARAMS["traffic_stress"]["default_speed_kmh"]


# --- parsing ------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("50", 50),
    (30, 30),
    ("30 km/h", 30),
    ("20 mph", 20 * MPH_TO_KMH),
    ("DE:zone30", 30),
    ("de:urban", 50),                 # alias lookup ignores case
    ("walk", 7),
    ("30;50", 50),                    # several values: highest
    (["30", "DE:zone:20"], 30),
    ("signals", None),
    ("none", None),
    (None, None),
    (math.nan, None),
])
def test_parse_maxspeed(value, expected):
    result = parse_maxspeed(value, ALIASES)
    assert result == (pytest.approx(expected) if expected is not None else None)


@pytest.mark.parametrize("value, expected", [
    ("2", 2), ("4", 4), (["2", "3"], 3), ("2;4", 4), ("1.5", 1), ("many", None), (None, None),
])
def test_parse_lanes(value, expected):
    assert parse_lanes(value) == expected


@pytest.mark.parametrize("highway, expected", [
    ("residential", 30),             # most residential streets are in 30 zones
    ("living_street", 7),
    ("trunk", 70),
    ("primary_link", 50),
    ("service", 30),
    ("corridor", 50),                 # unknown class falls back to `other`
    (None, 50),
    (["service", "trunk"], 70),       # merged segment: highest default
])
def test_default_speed(highway, expected):
    assert default_speed(highway, DEFAULTS) == expected


# --- traffic_score ------------------------------------------------------------

CASES = [
    # name, highway, maxspeed, lanes, expected score
    # speed score (10 -> 100, 30 -> 75, 50 -> 30, 70 -> 0) x class factor - 10 per lane above 2
    ("res 30", "residential", "30", None, 75),
    ("main 50", "primary", "50", None, 30 * 0.6),
    ("main default", "primary", None, None, 30 * 0.6),
    ("main 30", "primary", "30", None, 75 * 0.6),
    ("main 30 4 lanes", "primary", "30", "4", 75 * 0.6 - 20),     # e.g. Stresemannstrasse
    ("primary link", "primary_link", "30", None, 75 * 0.6),       # link: parent class
    ("res default", "residential", None, None, 75),                # residential default is 30
    ("zone 30", "residential", "DE:zone30", None, 75),
    ("40 between", "secondary", "40", None, 52.5 * 0.7),
    ("tertiary 50", "tertiary", "50", None, 30 * 0.85),
    ("unclassified 30", "unclassified", "30", None, 75),          # unlisted class: factor 1
    ("merged", ["residential", "primary"], "30", None, 75 * 0.6), # lowest factor
    ("living street", "living_street", None, None, 100),
    ("trunk default", "trunk", None, None, 0),
    ("4 lanes", "primary", "50", "4", 0),                          # 18 - 20, clipped at 0
    ("2 lanes", "primary", "50", "2", 30 * 0.6),                   # no penalty up to 2
    ("footway", "footway", None, None, 100),
    ("steps", "steps", None, None, 100),
    ("path", "path", "50", "4", 100),                   # pedestrian-only ignores speed and lanes
    ("bad tag", "residential", "signals", None, 75),    # unreadable -> default (30)
    ("very slow", "residential", "5", None, 100),       # flat below the curve
]


@pytest.fixture
def cases():
    return pd.DataFrame(
        [{"name": n, "highway": h, "maxspeed": m, "lanes": l} for n, h, m, l, _ in CASES]
    ).set_index("name")


def test_traffic_score_cases(cases):
    scores = traffic_score(cases, PARAMS)
    expected = pd.Series({n: s for n, *_, s in CASES}, dtype=float)
    pd.testing.assert_series_equal(scores, expected, check_names=False, atol=1e-9)
    assert scores.name == "traffic_stress"


def test_residential_30_beats_main_road_50(cases):
    scores = traffic_score(cases, PARAMS)
    assert scores["res 30"] > scores["main 50"]


def test_primary_scores_below_residential_at_same_speed(cases):
    scores = traffic_score(cases, PARAMS)
    assert scores["main 30"] < scores["res 30"]
    assert scores["main 30 4 lanes"] < scores["res default"]


@pytest.mark.parametrize("highway, expected", [
    ("primary", 0.6), ("primary_link", 0.6), ("trunk_link", 0.5), ("secondary", 0.7),
    ("tertiary", 0.85), ("residential", 1.0), ("living_street", 1.0), ("footway", 1.0),
    (["tertiary", "secondary"], 0.7), (None, 1.0),
])
def test_class_factor(highway, expected):
    assert class_factor(highway, PARAMS["traffic_stress"]["class_factor"]) == expected


def test_scores_in_range(cases):
    scores = traffic_score(cases, PARAMS)
    assert scores.between(0, 100).all()


def test_effective_speed_marks_source(cases):
    speed = effective_speed(cases, PARAMS)
    assert speed.loc["res 30", "speed_tagged"]
    assert not speed.loc["main default", "speed_tagged"]
    assert speed.loc["main default", "speed_kmh"] == 50
    assert not speed.loc["bad tag", "speed_tagged"]


def test_traffic_score_on_network_segments(area_data):
    segments = build_network(area_data).segments
    scores = traffic_score(segments, PARAMS)
    assert (scores[segments["highway"] == "footway"] == 100).all()
    assert (scores[segments["highway"] == "residential"] == 75).all()   # no maxspeed -> 30 km/h
