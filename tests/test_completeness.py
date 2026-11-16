import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from walkscope.completeness import FLAG_COLUMNS, completeness, data_flags
from walkscope.config import load_indicator_params

PARAMS = load_indicator_params()
PEDESTRIAN = PARAMS["pedestrian_only_highways"]
CRS = "EPSG:25833"


@pytest.fixture
def segs():
    """Five 100 m segments along y = 0, 200, 400, 600, 800 (the last one 300 m long)."""
    rows = [
        # highway, sidewalk, sidewalk:right, lit, length
        ("residential", "both", None, "yes", 100),
        ("residential", None, "separate", "no", 100),      # side tag: one side known
        ("residential", "yes", None, None, 100),           # sidewalk=yes: not a known value
        ("footway", None, None, "yes", 100),               # no sidewalk needed
        ("primary", None, None, "limited", 300),           # nothing known
    ]
    return gpd.GeoDataFrame(
        {"highway": [r[0] for r in rows], "sidewalk": [r[1] for r in rows],
         "sidewalk:right": [r[2] for r in rows], "lit": [r[3] for r in rows],
         "length": [float(r[4]) for r in rows], "scored": True},
        geometry=[LineString([(0, 200 * i), (r[4], 200 * i)]) for i, r in enumerate(rows)], crs=CRS,
    )


@pytest.fixture
def kerbs():
    # Known kerbs 10 m from segment 0 and 49 m from segment 4; none near the others.
    return gpd.GeoDataFrame({"kerb_score": [100.0, 0.0]}, geometry=[Point(50, 10), Point(150, 849)], crs=CRS)


def test_data_flags(segs, kerbs):
    flags = data_flags(segs, kerbs, PARAMS)
    assert list(flags.columns) == list(FLAG_COLUMNS)
    assert flags["data_sidewalk"].tolist() == [True, True, False, False, False]
    assert flags["data_kerb"].tolist() == [True, False, False, False, True]
    assert flags["data_lit"].tolist() == [True, True, False, True, False]


def test_completeness_is_length_weighted_and_sidewalk_counts_roads_only(segs, kerbs):
    segs[list(FLAG_COLUMNS)] = data_flags(segs, kerbs, PARAMS)
    c = completeness(segs, PEDESTRIAN)
    assert c.sidewalk == pytest.approx(200 / 600)          # roads: 100+100+100+300 m, known on 200 m
    assert c.kerb == pytest.approx(400 / 700)              # all segments, 700 m
    assert c.lit == pytest.approx(300 / 700)
    assert c.mean == pytest.approx((200 / 600 + 400 / 700 + 300 / 700) / 3)


def test_completeness_ignores_unscored(segs, kerbs):
    segs[list(FLAG_COLUMNS)] = data_flags(segs, kerbs, PARAMS)
    segs.loc[4, "scored"] = False
    assert completeness(segs, PEDESTRIAN).sidewalk == pytest.approx(200 / 300)


def test_completeness_without_roads(segs, kerbs):
    segs[list(FLAG_COLUMNS)] = data_flags(segs, kerbs, PARAMS)
    c = completeness(segs[segs["highway"] == "footway"], PEDESTRIAN)
    assert np.isnan(c.sidewalk)
    assert c.mean == pytest.approx((0 + 1) / 2)            # kerb 0, lit 1; sidewalk left out



def test_surface_flag_uses_the_first_tag_present(kerbs):
    from walkscope.completeness import data_flags
    segs = gpd.GeoDataFrame({
        "highway": ["residential"] * 3, "lit": [None] * 3, "sidewalk": [None] * 3,
        "sidewalk:surface": [None, "mud-ish", None], "surface": ["asphalt", "asphalt", None],
        "length": [100.0] * 3, "scored": True,
    }, geometry=[LineString([(0, 1000 * i), (100, 1000 * i)]) for i in range(3)], crs=CRS)
    # Like the surface sub-score: an unknown sidewalk:surface value wins over a known surface.
    assert data_flags(segs, kerbs, PARAMS)["data_surface"].tolist() == [True, False, False]


def test_persona_gaps():
    from walkscope.completeness import Completeness, persona_gaps
    from walkscope.config import load_personas
    personas = load_personas()
    data = Completeness(sidewalk=0.8, kerb=0.25, lit=0.4, surface=0.6)
    assert persona_gaps(data, personas["general_adult"], 0.5) == []
    assert persona_gaps(data, personas["wheelchair"], 0.5) == [("kerb data", 0.25)]
    assert persona_gaps(data, personas["night"], 0.5) == [("lighting tags", 0.4)]
    assert persona_gaps(data, personas["wheelchair"], 0.7) == [("kerb data", 0.25), ("surface tags", 0.6)]
    low = Completeness(sidewalk=0.3, kerb=0.9, lit=0.9, surface=float("nan"))
    assert persona_gaps(low, personas["child"], 0.5) == [("sidewalk tags", 0.3)]


def test_caution_threshold_in_config():
    assert PARAMS["completeness"]["caution_below"] == 0.5
