import numpy as np
import pandas as pd
import pytest

from walkscope.config import load_personas
from walkscope.scoring import display_name, score_segments, weakest

PERSONAS = load_personas()


@pytest.fixture
def segs():
    return pd.DataFrame({
        "name": ["A Street", "A Street", None, ["B Road", "C Lane"], "D Steps"],
        "highway": ["residential", "residential", "footway", "primary", ["footway", "steps"]],
        "scored": [True, True, True, True, True],
        "amenity": [100, 50, 0, 80, 60],
        "connectivity": [100, 50, 0, 80, 60],
        "infrastructure": [100, 50, 0, 80, 60],
        "traffic_stress": [100, 50, 0, 80, 60],
        "transit": [100, 50, 0, 80, 60],
    })


def test_equal_indicators_give_that_score(segs):
    score = score_segments(segs, PERSONAS["general_adult"])
    assert score.tolist() == [100, 50, 0, 80, 60]


def test_weights_apply():
    row = pd.DataFrame({"amenity": [100], "connectivity": [0], "infrastructure": [0],
                        "traffic_stress": [0], "transit": [0], "highway": ["residential"]})
    assert score_segments(row, PERSONAS["general_adult"]).iloc[0] == 30
    assert score_segments(row, PERSONAS["wheelchair"]).iloc[0] == 20


def test_steps_are_zero_for_wheelchair_only(segs):
    assert score_segments(segs, PERSONAS["wheelchair"]).iloc[4] == 0     # footway|steps
    assert score_segments(segs, PERSONAS["general_adult"]).iloc[4] == 60


def test_missing_indicator_is_an_error(segs):
    with pytest.raises(KeyError, match="transit"):
        score_segments(segs.drop(columns="transit"), PERSONAS["general_adult"])


def test_display_name():
    assert display_name("A") == "A"
    assert display_name(["A", "B", "A"]) == "A / B"
    assert display_name(None) is None


def test_weakest_named_only_one_row_per_street(segs):
    table = weakest(segs, "traffic_stress", n=10)
    assert table["street"].tolist() == ["A Street", "D Steps", "B Road / C Lane"]   # unnamed left out
    assert table.loc[0, "traffic_stress"] == 50                                    # A Street's worst segment
    assert table.loc[1, "type"] == "footway / steps"


def test_weakest_ignores_unscored(segs):
    segs.loc[1, "scored"] = False
    assert weakest(segs, "traffic_stress").loc[0, "traffic_stress"] == 60


def test_weakest_returns_segment_id_and_extra_columns(segs):
    segs["speed_kmh"] = [50, 30, None, 50, None]
    table = weakest(segs, "traffic_stress", extra=["speed_kmh"])
    assert list(table.columns) == ["street", "type", "traffic_stress", "speed_kmh", "segment_id"]
    assert table["segment_id"].tolist() == [1, 4, 3]
    assert table["speed_kmh"].tolist()[0] == 30


# --- area summaries ---------------------------------------------------------------

from walkscope.scoring import area_summary, length_weighted_mean  # noqa: E402

PEDESTRIAN = ["footway", "pedestrian", "path", "steps"]


def test_length_weighted_mean():
    assert length_weighted_mean(pd.Series([100, 0]), pd.Series([30.0, 10.0])) == 75
    assert np.isnan(length_weighted_mean(pd.Series([], dtype=float), pd.Series([], dtype=float)))


def test_area_summary_separates_roads_and_paths_and_weights_by_length():
    seg = pd.DataFrame({
        "highway": ["residential", "primary", "footway", "path", "residential"],
        "length": [300.0, 100.0, 50.0, 150.0, 999.0],
        "scored": [True, True, True, True, False],          # last one is outside the area
        "traffic_stress": [80.0, 20.0, 100.0, 100.0, 0.0],
    })
    summary = area_summary(seg, "traffic_stress", PEDESTRIAN)
    assert summary.road_mean == pytest.approx((80 * 300 + 20 * 100) / 400)   # 65, not the segment mean 75
    assert summary.road_km == pytest.approx(0.4)
    assert summary.pedestrian_mean == 100
    assert summary.pedestrian_km == pytest.approx(0.2)
    assert summary.pedestrian_share == pytest.approx(1 / 3)


def test_area_summary_without_roads():
    seg = pd.DataFrame({"highway": ["footway"], "length": [10.0], "scored": [True], "amenity": [40.0]})
    summary = area_summary(seg, "amenity", PEDESTRIAN)
    assert np.isnan(summary.road_mean)
    assert summary.pedestrian_share == 1
