import math

import pytest

from walkscope.tags import as_list, is_pedestrian_only, is_routing_only, road_classes

PED = ["footway", "pedestrian", "path", "steps"]
RULES = {
    "footway": ["sidewalk", "crossing", "traffic_island"],
    "service": ["parking_aisle", "driveway"],
    "access": ["private"],
    "highway": ["corridor"],
}


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "footway", "footway": "sidewalk"}, True),
    ({"highway": "footway", "footway": "crossing"}, True),
    ({"highway": "footway", "footway": ["sidewalk", "crossing"]}, True),
    ({"highway": "footway"}, False),                                  # a footpath of its own
    ({"highway": "footway", "footway": ["sidewalk", "access_aisle"]}, False),
    ({"highway": "path", "footway": "sidewalk"}, True),
    ({"highway": "residential", "footway": "sidewalk"}, False),       # footway rule: pedestrian-only only
    ({"highway": ["footway", "residential"], "footway": "sidewalk"}, False),
    ({"highway": "service", "service": "parking_aisle"}, True),
    ({"highway": "service", "service": "driveway"}, True),
    ({"highway": "service", "service": "alley"}, False),
    ({"highway": "service"}, False),                                  # plain service road is scored
    ({"highway": "service", "service": ["driveway", "alley"]}, False),  # partly an alley
    ({"highway": "residential", "access": "private"}, True),
    ({"highway": "residential", "access": "destination"}, False),
    ({"highway": "corridor"}, True),
    ({"highway": ["steps", "corridor"]}, False),                      # partly outdoor steps
    ({"highway": "residential"}, False),
])
def test_is_routing_only(tags, expected):
    assert is_routing_only(tags, PED, RULES) is expected


@pytest.mark.parametrize("value, expected", [
    (None, []),
    (math.nan, []),
    ("residential", ["residential"]),
    ("30;50", ["30", "50"]),
    (["30", "50 ; 70"], ["30", "50", "70"]),
    (30, ["30"]),
    ([None, "a", ""], ["a"]),
])
def test_as_list(value, expected):
    assert as_list(value) == expected


def test_road_classes_strip_link():
    assert road_classes(["primary_link", "residential"]) == ["primary", "residential"]


@pytest.mark.parametrize("highway, expected", [
    ("footway", True),
    ("steps", True),
    ("path", True),
    (["footway", "pedestrian"], True),
    (["footway", "residential"], False),   # partly a road counts as a road
    ("living_street", False),
    (None, False),
])
def test_is_pedestrian_only(highway, expected):
    assert is_pedestrian_only(highway, PED) is expected
