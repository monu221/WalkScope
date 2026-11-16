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
    assert seg.loc[seg["highway"] == "residential", "traffic_stress"].eq(75).all()   # default 30 km/h


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


def test_score_place_adds_transit_columns(offline):
    net = pipeline.score_place("Grid")
    seg = net.segments
    # The bus stop is at grid node 6 (column 0, row 2); streets are 100 m long,
    # so the walking distance from node n is 100 m per grid step.
    steps = seg["measure_node"].map(lambda n: (n % 3) + (2 - n // 3))
    assert seg["transit_distance_m"].tolist() == pytest.approx((steps * 100).tolist())
    assert seg["station_distance_m"].isna().all()              # no rail station in the fixture
    # 100 within 150 m, 0 at 800 m.
    assert seg["transit"].tolist() == pytest.approx(
        [min(100, (800 - d) / 650 * 100) for d in seg["transit_distance_m"]])


def test_score_place_adds_amenity_columns(offline):
    seg = pipeline.score_place("Grid").segments
    # Supermarket at grid node 4, park nearest node 0, pharmacy at node 8;
    # 100 m per grid step. No school, café or doctor -> those score 0.
    def steps(a, b):
        return abs(a % 3 - b % 3) + abs(a // 3 - b // 3)

    def lin(d):
        return max(0, min(100, (800 - d) / 600 * 100))

    expected = {"groceries": 4, "park": 0, "pharmacy": 8}
    for cat, node in expected.items():
        dist = seg["measure_node"].map(lambda n: 100 * steps(n, node))
        assert seg[f"amenity_{cat}_m"].tolist() == pytest.approx(dist.tolist())
        assert seg[f"amenity_{cat}"].tolist() == pytest.approx(dist.map(lin).tolist())
    for cat in ("school", "cafe", "doctor"):
        assert seg[f"amenity_{cat}_m"].isna().all()
        assert seg[f"amenity_{cat}"].eq(0).all()
    total = sum(seg[f"amenity_{c}"] for c in expected) / 6
    assert seg["amenity"].tolist() == pytest.approx(total.tolist())


def test_all_indicators_present_for_combined_score(offline):
    from walkscope.config import INDICATORS, load_personas
    from walkscope.scoring import score_segments

    seg = pipeline.score_place("Grid").segments
    for persona in load_personas().values():
        score = score_segments(seg, persona)
        assert score.between(0, 100).all()
    assert all(seg[i].notna().all() for i in INDICATORS)


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


def test_score_place_reports_progress_steps(offline):
    steps = []
    pipeline.score_place("Grid", progress=steps.append)
    assert steps == [pipeline.STEP_FIND, pipeline.STEP_DOWNLOAD, pipeline.STEP_SCORE]


def test_area_errors_carry_a_hint(monkeypatch):
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: box(13.0, 52.3, 13.8, 52.7))
    with pytest.raises(AreaError) as err:
        pipeline.score_place("Berlin")
    assert "neighbourhood or district" in err.value.hint
    assert AreaError("x").hint == fetch.DEFAULT_AREA_HINT


# --- Streamlit page -------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_app_cache():
    """The app keeps scored places in st.cache_resource; don't leak them between tests."""
    import streamlit as st
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _app():
    return AppTest.from_file(APP, default_timeout=60).run()


def _run_app(place):
    at = _app()
    at.sidebar.text_input(key="place_input").input(place)
    at.sidebar.button[0].click()                              # the form's Score area button
    return at.run()


def test_app_start_page():
    at = _app()
    assert not at.exception
    assert at.title[0].value == "WalkScope"
    assert "Score area" in at.info[0].value
    assert at.sidebar.button[0].label == "Score area"
    assert [b.label for b in at.sidebar.button[1:]] == ["St. Pauli", "Sternschanze", "Burtscheid (Aachen)"]
    assert at.expander[0].label == "How is this scored?"
    assert any("OpenStreetMap" in c.value for c in at.caption)


def test_app_scores_a_place(offline):
    at = _run_app("Grid")
    assert not at.exception
    assert not at.error
    assert at.subheader[0].value == "Grid"
    assert at.metric[1].value == "7"                          # streets scored
    assert at.metric[2].value == "89"                         # mean: 4 footways 100, 3 residential 75
    table = at.dataframe[0].value
    assert list(table.columns) == ["Street", "Road type", "Traffic stress", "Speed used", "Source"]
    assert table["Street"].tolist() == ["Row 0 Street", "Row 1 Street", "Row 2 Street"]
    assert table["Traffic stress"].tolist() == [75, 75, 75]
    assert table["Speed used"].tolist() == ["30 km/h"] * 3           # residential default
    assert table["Source"].tolist() == ["assumed"] * 3               # no maxspeed in the fixture
    assert at.expander[0].label == "How is this scored?"


def test_app_example_button_scores_that_place(offline, monkeypatch, area_data):
    asked = []
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: asked.append(name) or area_data.area)
    at = _app()
    at.sidebar.button(key="example_Sternschanze").click()
    at.run()
    assert not at.exception
    assert asked == ["Sternschanze, Hamburg"]
    assert at.sidebar.text_input(key="place_input").value == "Sternschanze, Hamburg"
    assert at.subheader[0].value == "Sternschanze, Hamburg"


def test_app_second_visit_uses_memory(offline, monkeypatch):
    at = _run_app("Grid")
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params: pytest.fail("downloaded again"))
    at.run()
    assert not at.exception and at.metric[1].value == "7"


def test_app_shows_busy_message(monkeypatch):
    def busy(name):
        raise ServerBusyError(["x"])
    monkeypatch.setattr(pipeline, "area_from_place", busy)
    at = _run_app("Somewhere busy")
    assert fetch.BUSY_MESSAGE in at.error[0].value
    assert "press **Score area** again" in at.error[0].value


def test_app_shows_area_error_with_hint(monkeypatch):
    def bad(name):
        raise AreaError("'Bus stop' was found as a single point, not an area.", hint="Try a district.")
    monkeypatch.setattr(pipeline, "area_from_place", bad)
    at = _run_app("Bus stop")
    assert "not an area" in at.error[0].value
    assert "Try a district." in at.error[0].value


def test_app_shows_unexpected_error_kindly(monkeypatch):
    def broken(name):
        raise RuntimeError("boom")
    monkeypatch.setattr(pipeline, "area_from_place", broken)
    at = _run_app("Anywhere")
    assert not at.exception
    assert "Something went wrong" in at.error[0].value
    assert at.expander[0].label == "Technical details"


def test_app_map_selector_shows_every_indicator(offline):
    at = _run_app("Grid")
    selector = at.sidebar.selectbox(key="view")
    labels = selector.options                                  # AppTest lists the formatted labels
    assert labels == ["Traffic stress", "Pedestrian infrastructure", "Connectivity",
                      "Transit access", "Amenity access"]

    expected = {
        # label: (extra table columns, first column's values, scores)
        "Pedestrian infrastructure": (["Sidewalk", "Crossing within 50 m"],
                                      ["not tagged"] * 3, [30, 30, 70]),
        "Connectivity": (["Intersections within 400 m"], ["0 per km²"] * 3, [0, 0, 0]),
        "Transit access": (["Nearest stop", "Nearest rail station"], None, None),
        "Amenity access": (["Furthest category"], ["none within 800 m: school, café, doctor"] * 3, None),
    }
    for i, label in enumerate(labels[1:], start=1):
        at.sidebar.selectbox(key="view").set_value(i).run()
        assert not at.exception, label
        table = at.dataframe[0].value
        extra, first_values, scores = expected[label]
        assert list(table.columns) == ["Street", "Road type", label, *extra]
        if first_values is not None:
            assert table[extra[0]].tolist() == first_values
        if scores is not None:
            assert table[label].tolist() == scores
        assert at.metric[2].label == f"Mean {label.lower()}"
    assert table["Furthest category"].iloc[0] == "none within 800 m: school, café, doctor"
