"""End-to-end: pipeline, map and the Streamlit page, on the offline grid fixture."""

from pathlib import Path

import folium
import pytest
from shapely.geometry import box
from streamlit.testing.v1 import AppTest

from walkscope import fetch, pipeline
from walkscope.fetch import AreaError, ServerBusyError
from walkscope.mapview import make_map

APP = str(Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py")


@pytest.fixture
def offline(monkeypatch, area_data):
    """Make score_place() run on the grid fixture instead of downloading."""
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: area_data.area)
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params: area_data)


def test_score_place_adds_traffic_columns(offline):
    net = pipeline.score_place("Grid")
    seg = net.segments
    assert {"traffic_stress", "speed_kmh", "speed_tagged"} <= set(seg.columns)
    assert seg.loc[seg["highway"] == "footway", "traffic_stress"].eq(100).all()
    assert seg.loc[seg["highway"] == "residential", "traffic_stress"].eq(30).all()


def test_score_place_adds_infrastructure_columns(offline):
    seg = pipeline.score_place("Grid").segments
    footway = seg["highway"] == "footway"
    assert seg.loc[footway, "infrastructure"].eq(100).all()
    # Row 0 Street (nodes 0-1) has a marked crossing at its midpoint and a lowered
    # kerb next to it; the other rows have neither. No sidewalk tags -> 50.
    row0 = seg[(seg["u"].isin([0, 1])) & (seg["v"].isin([0, 1]))].iloc[0]
    assert row0["infra_crossing"] == 100 and row0["infra_kerb"] == 100
    assert row0["infrastructure"] == 0.6 * 50 + 0.4 * 100
    other_rows = seg[~footway & (seg.index != row0.name)]
    assert other_rows["infra_crossing"].eq(0).all()
    assert other_rows["infrastructure"].eq(30).all()


def test_score_place_adds_connectivity_columns(offline):
    seg = pipeline.score_place("Grid").segments
    assert seg["intersection_density"].eq(0).all()      # grid has no road with 3 roads meeting
    assert seg["connectivity"].between(0, 100).all()


def test_too_large_area_is_refused(monkeypatch):
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: box(13.0, 52.3, 13.8, 52.7))  # ~Berlin
    with pytest.raises(AreaError, match="up to 5 km²"):
        pipeline.score_place("Berlin")


def test_area_without_streets_is_refused(monkeypatch, area_data):
    tiny = box(13.3, 52.3, 13.3001, 52.3001)                 # nowhere near the grid
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: tiny)
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params: area_data.__class__(
        tiny, area_data.buffered, area_data.graph, area_data.pois, area_data.stops, area_data.street_points))
    with pytest.raises(AreaError, match="No streets to score"):
        pipeline.score_place("Markt")


def test_area_km2():
    # 0.01° x 0.01° at the equator is about 1.11 km x 1.11 km
    assert pipeline.area_km2(box(0, 0, 0.01, 0.01)) == pytest.approx(1.23, rel=0.01)


def test_make_map_draws_only_scored_segments(offline):
    net = pipeline.score_place("Grid")
    m = make_map(net.segments, "traffic_stress", "Traffic stress", net.area)
    assert isinstance(m, folium.Map)
    layers = [c for c in m._children.values() if isinstance(c, folium.GeoJson)]
    segments_layer = layers[-1]
    assert len(segments_layer.data["features"]) == net.segments["scored"].sum() == 7
    html = m.get_root().render()
    assert "Traffic stress" in html


def _run_app(place):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input[0].input(place)
    at.button[0].click()
    return at.run()


def test_app_start_page():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "WalkScope"
    assert "Score area" in at.info[0].value


def test_app_scores_a_place(offline):
    at = _run_app("Grid")
    assert not at.exception
    assert not at.error
    assert at.metric[1].value == "7"                          # streets scored
    assert at.metric[2].value == "70"                         # mean: 4 footways 100, 3 residential 30
    table = at.dataframe[0].value
    assert table["Street"].tolist()[0].startswith("Row ")


def test_app_shows_busy_message(monkeypatch):
    def busy(name):
        raise ServerBusyError(["x"])
    monkeypatch.setattr(pipeline, "area_from_place", busy)
    at = _run_app("Somewhere busy")
    assert at.error[0].value == fetch.BUSY_MESSAGE


def test_app_shows_area_error(monkeypatch):
    def bad(name):
        raise AreaError("'Bus stop' resolved to a Point, not an area")
    monkeypatch.setattr(pipeline, "area_from_place", bad)
    at = _run_app("Bus stop")
    assert "not an area" in at.error[0].value
