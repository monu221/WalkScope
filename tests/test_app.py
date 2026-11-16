"""End-to-end: pipeline, map and the Streamlit page, on the offline grid fixture."""

import re
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
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params, **kw: area_data)


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
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params, **kw: area_data.__class__(
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
    assert [b.label for b in at.sidebar.button[1:]] == [
        "Sternschanze (inner city)", "Iserbrook (suburban)", "Aachen city centre"]
    assert at.expander[0].label == "How is this scored?"
    assert any("OpenStreetMap" in c.value for c in at.caption)


def test_app_scores_a_place(offline):
    at = _run_app("Grid")
    assert not at.exception
    assert not at.error
    assert at.subheader[0].value == "Grid · General adult"
    # 7 scored segments of 100 m: 3 residential roads and 4 footways.
    assert at.metric[1].value == "0.7 km"
    assert at.metric[2].label == "Walkability score, roads"   # the combined score is the default view
    assert at.metric[3].value == "57%"                        # footways: 4 of 7 segments by length
    # Data completeness: no sidewalk, lit or maxspeed tags in the fixture.
    labels = [m.label for m in at.metric[4:]]
    assert labels == ["Overall", "Sidewalk known", "Kerb data nearby", "Lighting tagged", "Speed limit tagged"]
    assert at.metric[5].value == "0%" and at.metric[7].value == "0%" and at.metric[8].value == "0%"
    table = at.dataframe[0].value
    assert list(table.columns) == ["Street", "Score", "Weakest indicator", "Road type"]
    assert sorted(table["Street"]) == ["Row 0 Street", "Row 1 Street", "Row 2 Street"]
    assert table["Weakest indicator"].tolist() == ["Connectivity 0"] * 3   # the grid has no intersections
    assert at.expander[0].label == "What if… add a crossing or an amenity"
    assert at.expander[-1].label == "How is this scored?"


def test_app_example_button_scores_that_place(offline, monkeypatch, area_data):
    asked = []
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: asked.append(name) or area_data.area)
    at = _app()
    at.sidebar.button(key="example_Sternschanze").click()
    at.run()
    assert not at.exception
    assert asked == ["Sternschanze, Hamburg"]
    assert at.sidebar.text_input(key="place_input").value == "Sternschanze, Hamburg"
    assert at.subheader[0].value == "Sternschanze, Hamburg · General adult"


def test_app_second_visit_uses_memory(offline, monkeypatch):
    at = _run_app("Grid")
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params, **kw: pytest.fail("downloaded again"))
    at.run()
    assert not at.exception and at.metric[1].value == "0.7 km"


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
    assert labels == ["Walkability score", "Traffic stress", "Pedestrian infrastructure", "Connectivity",
                      "Transit access", "Amenity access"]

    expected = {
        # label: (extra table columns, first column's values, scores)
        "Traffic stress": (["Speed used", "Source"], ["30 km/h"] * 3, [75, 75, 75]),   # residential default
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
        assert list(table.columns) == ["Street", "Score", *extra, "Road type"]
        if first_values is not None:
            assert table[extra[0]].tolist() == first_values
        if scores is not None:
            assert table["Score"].tolist() == scores
        assert at.metric[2].label == f"{label}, roads"
    assert table["Furthest category"].iloc[0] == "none within 800 m: school, café, doctor"



def test_app_persona_switch(offline):
    at = _run_app("Grid")
    persona = at.sidebar.selectbox(key="persona")
    assert persona.options == ["General adult", "Wheelchair user", "Child walking to school", "Night-time"]
    assert "Weights: amenity 30" in _persona_caption(at)
    general = at.metric[2].value

    at.sidebar.selectbox(key="persona").set_value("wheelchair").run()
    assert not at.exception
    assert at.subheader[0].value == "Grid · Wheelchair user"
    caption = _persona_caption(at)
    assert "Infrastructure: base 50 %, kerb 25 %, surface 25 %" in caption
    assert "Impassable: steps" in caption
    assert at.metric[2].value != general

    # Infrastructure for wheelchair users: 50 % base + 25 % kerb + 25 % surface (unknown: 50).
    # Row 0 Street: base 70, lowered kerb nearby (100); the other rows: base 30, no kerb data (50).
    at.sidebar.selectbox(key="view").set_value(2).run()
    assert at.dataframe[0].value["Score"].tolist() == [40, 40, 72]

    # Back to the general adult: the cached network was not changed by the wheelchair run.
    at.sidebar.selectbox(key="persona").set_value("general_adult").run()
    assert at.dataframe[0].value["Score"].tolist() == [30, 30, 70]



def test_app_colour_scale_defaults_to_relative(offline):
    at = _run_app("Grid")
    scale = at.sidebar.radio(key="colour_scale")
    assert scale.value == "relative"
    assert scale.options == ["Relative to this area", "Absolute (0–100)"]
    scale.set_value("absolute").run()
    assert not at.exception


def test_app_cautions_when_persona_data_is_sparse(offline):
    # The grid has no sidewalk, surface or lighting tags and one kerb point.
    at = _run_app("Grid")
    assert len(at.warning) == 1
    text = at.warning[0].value
    assert "Less reliable for general adult" in text and "sidewalk tags cover only 0%" in text

    at.sidebar.selectbox(key="persona").set_value("wheelchair").run()
    text = at.warning[0].value
    assert "wheelchair user" in text
    assert "surface tags" in text and "kerb data" in text and "sidewalk tags" in text

    at.sidebar.selectbox(key="view").set_value(1).run()         # traffic stress: persona data not used
    assert len(at.warning) == 0


def test_app_street_design_toggle(offline):
    at = _run_app("Grid")
    focus = at.button_group(key="table_focus")
    assert focus.value == "all"
    focus.set_value("design").run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table.columns) == ["Street", "Score", "Weakest indicator", "Road type"]
    # Street design = (15 x traffic 75 + 20 x infrastructure) / 35; Row 0 has infrastructure 70.
    assert table["Score"].tolist() == [round((15 * 75 + 20 * 30) / 35)] * 2 + [round((15 * 75 + 20 * 70) / 35)]
    assert table["Weakest indicator"].tolist() == ["Pedestrian infrastructure 30"] * 2 + ["Pedestrian infrastructure 70"]

    at.sidebar.selectbox(key="view").set_value(1).run()         # toggle only on the walkability view
    assert len(at.button_group) == 0



# --- What-if (build step 8) -------------------------------------------------------
# Map clicks come from the streamlit-folium component, which AppTest can't drive;
# the tests put edits into session state as a click would.

def _metric(at, label):
    return next(m for m in at.metric if m.label == label)


def test_app_whatif_crossing_before_and_after(offline):
    from tests.conftest import grid_xy
    from walkscope.scenario import CROSSING, Edit

    at = _run_app("Grid")
    assert at.radio(key="whatif_mode").value == "off"
    assert not any(m.label == "After" for m in at.metric)       # nothing added yet

    at.sidebar.selectbox(key="view").set_value(2).run()          # pedestrian infrastructure
    at.session_state["edits|Grid"] = [Edit(CROSSING, *grid_xy(0.3, 1))]
    at.run()
    assert not at.exception
    # Roads: 3 x 100 m at 30, 30, 70 -> 43.3; the crossing lifts Row 1 Street from 30 to 70 -> 56.7.
    assert _metric(at, "Pedestrian infrastructure, roads: before").value == "43.3"
    after = _metric(at, "After")
    assert after.value == "56.7" and after.delta == "+13.4"            # difference of the shown values
    assert _metric(at, "Segments improved").value == "1"
    changes = at.dataframe[0].value
    assert changes.to_dict("records") == [{"Street": "Row 1 Street", "Before": 30, "After": 70, "Change": 40}]
    assert at.subheader[1].value == "Weakest streets (after)"
    assert at.dataframe[1].value["Score"].tolist() == [30, 70, 70]

    at.button_group(key="whatif_show").set_value("before").run()
    assert at.dataframe[1].value["Score"].tolist() == [30, 30, 70]

    next(b for b in at.button if b.label == "Undo last").click().run()
    assert at.session_state["edits|Grid"] == []
    assert not any(m.label == "After" for m in at.metric)


def test_app_whatif_amenity_category(offline):
    from tests.conftest import grid_xy
    from walkscope.scenario import AMENITY, Edit

    at = _run_app("Grid")
    at.radio(key="whatif_mode").set_value("amenity").run()
    assert at.selectbox(key="whatif_category").options[:2] == ["Groceries", "School"]
    at.session_state["edits|Grid"] = [Edit(AMENITY, *grid_xy(0, 0), category="school")]
    at.run()
    assert not at.exception
    assert float(_metric(at, "After").delta) > 0
    assert int(_metric(at, "Segments improved").value) == 7        # every scored segment is within 800 m
    assert "Amenity: school" in " ".join(c.value for c in at.caption)


def test_app_whatif_shows_placement_errors(offline):
    at = _run_app("Grid")
    at.session_state["whatif_error"] = "A crossing needs a road within 50 m. Click on or next to a road."
    at.run()
    assert "needs a road" in at.error[0].value


# --- Drawing an area (build step 9) -----------------------------------------------
# The draw map is a streamlit-folium component, which AppTest can't draw on; the
# tests put a drawn outline into session state as the Score button would.

def test_app_draw_mode_shows_the_draw_map():
    at = _app()
    at.sidebar.radio(key="area_mode").set_value("draw").run()
    assert not at.exception
    assert at.subheader[0].value == "Draw a study area"
    assert at.info[0].value == "Nothing drawn yet."
    assert "Score drawn area" in at.sidebar.caption[0].value
    assert len(at.sidebar.text_input) == 0                     # no place box while drawing


def test_app_scores_a_drawn_area(offline, monkeypatch, area_data):
    from walkscope.drawing import drawn_area_name

    monkeypatch.setattr(pipeline, "area_from_place", lambda name: pytest.fail("geocoded a drawn area"))
    name = drawn_area_name(area_data.area, pipeline.area_km2(area_data.area))
    at = _app()
    at.session_state["drawn_areas"] = {name: area_data.area.wkt}
    at.session_state["selected_place"] = name
    at.run()
    assert not at.exception
    assert at.subheader[0].value == f"{name} · General adult"
    assert at.metric[1].value == "0.7 km"                       # same grid as the place-name tests


def test_app_example_button_leaves_draw_mode(offline):
    at = _app()
    at.sidebar.radio(key="area_mode").set_value("draw").run()
    at.sidebar.button(key="example_Sternschanze").click().run()
    assert at.sidebar.radio(key="area_mode").value == "place"



def test_app_aachen_example_is_a_drawn_area(monkeypatch, area_data):
    """The Aachen button scores its preset outline directly, without geocoding."""
    scored = []
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: pytest.fail("geocoded the Aachen preset"))

    def fake_fetch(area, params, **kw):
        scored.append(area)
        return area_data.__class__(area_data.area, area_data.buffered, area_data.graph, area_data.pois,
                                   area_data.stops, area_data.street_points)
    monkeypatch.setattr(pipeline, "fetch_area", fake_fetch)
    at = _app()
    at.sidebar.button(key="example_Aachen").click().run()
    assert not at.exception
    assert at.subheader[0].value == "Aachen city centre (Dom and Markt, 1.5 km²) · General adult"
    assert pipeline.area_km2(scored[0]) == pytest.approx(1.5, abs=0.01)
    minx, miny, maxx, maxy = scored[0].bounds
    assert miny < 50.7747 < maxy and minx < 6.0839 < maxx          # the Dom is inside


@pytest.mark.parametrize("button", ["example_Sternschanze", "example_Iserbrook", "example_Aachen"])
def test_app_examples_open_from_shipped_data(monkeypatch, button):
    """The example buttons use data/examples: no geocoding, no download."""
    def offline(*args, **kwargs):
        pytest.fail("called the map servers")
    monkeypatch.setattr(fetch, "_download", offline)
    monkeypatch.setattr(fetch.ox, "geocode_to_gdf", offline)
    at = _app()
    at.sidebar.button(key=button).click().run(timeout=60)
    assert not at.exception and not at.error
    note = next(c.value for c in at.caption if c.value.startswith("Example: map data downloaded on"))
    assert "© [OpenStreetMap]" in note and "ODbL" in note


def test_app_shows_steps_and_waiting_note_while_scoring(monkeypatch, area_data):
    """A slow download: the status shows each step with its time, and the note about the public server.

    The download ends busy, so the status stays on the page to be inspected (on success it is cleared).
    """
    import time as _time

    def slow_fetch(area, params, progress=None, **kw):
        progress(fetch.STEP_STREETS)
        _time.sleep(1.5)
        progress(fetch.STEP_FEATURES)
        _time.sleep(1.2)
        raise ServerBusyError(["x"])
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: area_data.area)
    monkeypatch.setattr(pipeline, "fetch_area", slow_fetch)
    at = _run_app("Grid")
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert re.search(rf"- {fetch.STEP_STREETS} \(0:0[12]\)", text)
    assert f"- {fetch.STEP_FEATURES}…" in text
    assert re.search(r"\*\*Elapsed: 0:0[2-5]\*\*", text)
    assert any("public OpenStreetMap server" in c.value and "up to 10" in c.value for c in at.caption)
    assert at.status[0].label.startswith("Could not score Grid (0:0")
    assert fetch.BUSY_MESSAGE in at.error[0].value


def _persona_caption(at):
    return next(c.value for c in at.sidebar.caption if c.value.startswith("Weights:"))


# --- Report (build step 10) -------------------------------------------------------

def test_app_report_downloads(offline):
    at = _run_app("Grid")
    assert not at.exception
    buttons = at.get("download_button")
    assert len(buttons) == 2
    assert "For general adult" in next(c.value for c in at.sidebar.caption if c.value.startswith("For "))


def _helpers():
    """The app module. It runs main() on import, so Streamlit's page calls are stubbed out."""
    import importlib.util
    import sys
    from unittest import mock
    spec = importlib.util.spec_from_file_location("walkscope_app", APP)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("streamlit.set_page_config"), mock.patch.dict(sys.modules, {"walkscope_app": module}):
        spec.loader.exec_module(module)
    return module


def test_text_helpers():
    """Kept in one test at the end: importing the app outside AppTest leaves Streamlit state behind."""
    module = _helpers()
    assert [module.elapsed_text(s) for s in (0, 7.9, 155, 600)] == ["0:00", "0:07", "2:35", "10:00"]
    assert module.file_stem("Sternschanze, Hamburg") == "walkscope-sternschanze-hamburg"
    assert module.file_stem("Aachen city centre (Dom and Markt, 1.5 km²)") == \
        "walkscope-aachen-city-centre-dom-and-markt-1-5-km2"
    assert module.file_stem("Düsseldorf-Altstadt") == "walkscope-dusseldorf-altstadt"
