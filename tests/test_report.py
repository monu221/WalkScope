"""Report on the offline grid fixture (3 residential rows of 2 x 100 m, 4 footways in the area)."""

import io
from datetime import date

import pytest
from PIL import Image
from shapely.geometry import box

from tests.conftest import grid_xy
from walkscope import pipeline
from walkscope.config import load_indicator_params, load_personas
from walkscope.report import (OSM_CREDIT, build_report, map_image, pdf_text, rounded_change, to_markdown,
                              to_pdf)
from walkscope.scenario import CROSSING, Edit

PARAMS = load_indicator_params()
PERSONAS = load_personas()
TODAY = date(2026, 11, 16)


@pytest.fixture
def net(monkeypatch, area_data):
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: area_data.area)
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params: area_data)
    return pipeline.score_place("Grid")


def test_report_contents(net):
    r = build_report(net, "Grid", PERSONAS["general_adult"], params=PARAMS, today=TODAY)
    assert r.place == "Grid" and r.created == "2026-11-16"
    assert r.network_km == pytest.approx(0.7)
    assert r.pedestrian_share == pytest.approx(4 / 7)
    assert list(r.indicators.columns) == ["Indicator", "Road average", "Weight (%)"]
    assert r.indicators.set_index("Indicator").loc["Traffic stress", "Road average"] == 75
    assert r.indicators["Weight (%)"].sum() == 100
    assert list(r.weakest.columns) == ["Street", "Score", "Weakest indicator", "Road type"]
    assert set(r.weakest["Street"]) == {"Row 0 Street", "Row 1 Street", "Row 2 Street"}
    assert r.weakest_design["Weakest indicator"].str.startswith("Pedestrian infrastructure").all()
    assert r.completeness.sidewalk == 0                       # no sidewalk tags in the grid
    assert "Less reliable for general adult" in r.caution
    assert r.scenario is None
    assert r.map_png[:8] == b"\x89PNG\r\n\x1a\n"


def test_markdown(net):
    md = to_markdown(build_report(net, "Grid", PERSONAS["wheelchair"], params=PARAMS, today=TODAY))
    assert md.startswith("# WalkScope report: Grid\n")
    for heading in ("## Summary", "## Map", "## Indicators", "## Weakest streets", "## Data completeness",
                    "## Method and sources"):
        assert heading in md
    assert "## What-if scenario" not in md
    assert "**Persona:** Wheelchair user" in md
    assert "**Walkability score, roads:" in md
    assert "![Walkability score for wheelchair user." in md and "data:image/png;base64," in md
    assert "| Street | Score | Weakest indicator | Road type |" in md
    assert "Kerb data within 50 m" in md and "⚠ **Less reliable for wheelchair user:**" in md
    assert OSM_CREDIT in md


def test_scenario_before_and_after(net):
    edits = [Edit(CROSSING, *grid_xy(0.3, 1))]
    r = build_report(net, "Grid", PERSONAS["general_adult"], edits=edits, params=PARAMS, today=TODAY)
    s = r.scenario
    assert s.items == ["Marked crossing"]
    assert s.after > s.before and s.improved_segments == 1
    assert s.streets["Street"].tolist() == ["Row 1 Street"]
    md = to_markdown(r)
    assert "## What-if scenario" in md and "- Marked crossing" in md
    assert "| Row 1 Street |" in md and "| +" in md            # signed change
    assert md.count("data:image/png;base64,") == 2             # before and after maps


def test_pdf(net):
    edits = [Edit(CROSSING, *grid_xy(0.3, 1))]
    pdf = to_pdf(build_report(net, "Grünstraße, Köln", PERSONAS["night"], edits=edits, params=PARAMS,
                              today=TODAY))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000


def test_pdf_text_handles_characters_outside_latin1():
    assert pdf_text("30–50 km/h, ≤ 43, Straße ² © ⚠ x") == "30-50 km/h, <= 43, Straße ² © Caution: x"
    assert pdf_text("北京") == "??"


def test_rounded_change():
    assert rounded_change(77.98, 78.248) == "+0.2"            # 78.0 -> 78.2 as shown
    assert rounded_change(43.33, 56.67) == "+13.4"
    assert rounded_change(float("nan"), 1) == "–"


def test_map_image_is_to_scale_and_includes_markers(net):
    seg = net.segments.assign(score=50.0)

    def ratio(png):
        """Map part's width / height (image minus 30 px padding each side and the 90 px legend)."""
        img = Image.open(io.BytesIO(png))
        assert img.width <= 800 and img.height <= 1400
        return (img.width - 60) / (img.height - 90 - 60)

    minx, miny, maxx, maxy = net.area.bounds
    png = map_image(seg, "score", net.area, 0, 100, width_px=800)
    assert png.startswith(bytes([0x89]) + b"PNG")
    assert ratio(png) == pytest.approx((maxx - minx) / (maxy - miny), rel=0.02)

    far = box(maxx + 300, miny, maxx + 301, miny + 1).centroid      # 300 m east of the area
    wider = map_image(seg, "score", net.area, 0, 100, markers=[(far, "amenity")], width_px=800)
    # The frame reaches 30 m past the marker on every side: east to maxx + 330.5, and down to
    # miny - 29.5 (the marker is 0.5 m above the bottom edge).
    assert ratio(wider) == pytest.approx((maxx + 330.5 - minx) / (maxy - (miny - 29.5)), rel=0.02)
