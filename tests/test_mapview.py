import folium
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from walkscope.mapview import BASEMAP, SCORE_COLORS, legend_html, make_map, view_for_bounds


@pytest.fixture
def segs():
    """Three short streets in Hamburg, in UTM metres."""
    lines = [LineString([(565000 + i * 200, 5933000), (565000 + i * 200 + 150, 5933000)]) for i in range(3)]
    return gpd.GeoDataFrame({
        "name": ["A Street", None, "B Road"],
        "highway": ["residential", "footway", ["primary", "secondary"]],
        "scored": [True, True, True],
        "traffic_stress": [30.4, 100.0, 9.6],
    }, geometry=lines, crs="EPSG:25832", index=pd.Index([5, 6, 7], name="segment_id"))


def geojson_layers(m):
    return [c for c in m._children.values() if isinstance(c, folium.GeoJson)]


def test_view_for_bounds():
    # About 2 km x 1.5 km in Hamburg: about 700 x 510 px at zoom 15 (fits 1100 x 600
    # with 10 % padding), twice that at zoom 16 (doesn't).
    centre, zoom = view_for_bounds((9.955, 53.555, 9.985, 53.568), 1100, 600)
    assert centre == pytest.approx([53.5615, 9.97])
    assert zoom == 15
    assert view_for_bounds((9.955, 53.555, 9.985, 53.568), 500, 400)[1] == 14
    assert view_for_bounds((9.97, 53.56, 9.9701, 53.5601), 1100, 600)[1] == 18   # clamped
    assert view_for_bounds((-180, -80, 180, 80), 1100, 600)[1] == 1


def test_map_opens_on_the_segments(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress")
    lat, lon = m.location
    assert 53.5 < lat < 53.6 and 9.9 < lon < 10.1
    assert m.options["zoom"] >= 14
    assert "fitBounds" in m.get_root().render()


def test_basemap_is_grey_osm_without_key(segs):
    html = make_map(segs, "traffic_stress", "Traffic stress").get_root().render()
    assert BASEMAP == "OpenStreetMap"
    assert "tile.openstreetmap.org" in html
    assert ".leaflet-tile-pane{filter:grayscale(100%)" in html
    assert "apikey" not in html.lower()


def test_tooltip_and_popup(segs):
    details = {"Speed limit": pd.Series(["30 km/h, tagged", None, "50 km/h, assumed"], index=segs.index)}
    m = make_map(segs, "traffic_stress", "Traffic stress", details=details)
    layer = geojson_layers(m)[-1]
    tooltip = next(c for c in layer._children.values() if isinstance(c, folium.GeoJsonTooltip))
    popup = next(c for c in layer._children.values() if isinstance(c, folium.GeoJsonPopup))
    assert tooltip.fields == ["street", "score"]
    assert popup.fields == ["street", "type", "score", "Speed limit"]
    props = [f["properties"] for f in layer.data["features"]]
    assert [p["street"] for p in props] == ["A Street", "(unnamed)", "B Road"]
    assert [p["score"] for p in props] == [30, 100, 10]
    assert [p["type"] for p in props] == ["residential", "footway", "primary / secondary"]
    assert [p["Speed limit"] for p in props] == ["30 km/h, tagged", "–", "50 km/h, assumed"]


def test_no_legend_on_the_map(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress")
    assert not any("colormap" in type(c).__name__.lower() for c in m._children.values())


def test_highlight_outlines_selected_segments_under_the_scores(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress", highlight=[7])
    layers = geojson_layers(m)
    assert [l.layer_name for l in layers] == ["Selected street", "Traffic stress"]
    assert [f["properties"]["segment_id"] for f in layers[0].data["features"]] == [7]
    assert len(geojson_layers(make_map(segs, "traffic_stress", "Traffic stress"))) == 1


@pytest.mark.parametrize("highlight", [pd.Index([7]), pd.Index([7], name="segment_id"), [7], (7,), {7}])
def test_highlight_accepts_a_pandas_index(segs, highlight):
    # The app passes seg.index[...] (a pandas Index); `highlight or ()` raised
    # "The truth value of a Index is ambiguous".
    layers = geojson_layers(make_map(segs, "traffic_stress", "Traffic stress", highlight=highlight))
    assert [l.layer_name for l in layers] == ["Selected street", "Traffic stress"]
    assert [f["properties"]["segment_id"] for f in layers[0].data["features"]] == [7]


@pytest.mark.parametrize("highlight", [None, [], pd.Index([], dtype="int64")])
def test_no_or_empty_highlight_draws_no_outline(segs, highlight):
    layers = geojson_layers(make_map(segs, "traffic_stress", "Traffic stress", highlight=highlight))
    assert [l.layer_name for l in layers] == ["Traffic stress"]


def test_legend_html():
    html = legend_html("Traffic stress")
    assert "Traffic stress: 0 = worst, 100 = best" in html
    assert "background:#fff" in html
    assert all(c in html for c in SCORE_COLORS)
    assert ">0<" in html and ">100<" in html



def test_colour_range():
    from walkscope.mapview import colour_range
    values = pd.Series(range(40, 101))                        # 40..100
    assert colour_range(values, relative=False) == (0, 100)
    lo, hi = colour_range(values, relative=True)
    assert (lo, hi) == (43, 97)                               # 5th / 95th percentile (exact here)
    assert colour_range(values + 0.5, relative=True) == (43, 98)              # rounded outwards
    assert colour_range(pd.Series([70.0] * 20), relative=True) == (65, 75)    # widened to 10 points
    assert colour_range(pd.Series([100.0] * 5), relative=True) == (90, 100)   # kept inside 0-100
    assert colour_range(pd.Series([], dtype=float), relative=True) == (0, 100)


def test_relative_colours_and_legend(segs):
    from walkscope.mapview import score_colormap
    m = make_map(segs, "traffic_stress", "Traffic stress", vmin=30, vmax=100)
    layer = geojson_layers(m)[-1]
    colours = [layer.style_function(f)["color"] for f in layer.data["features"]]
    cmap = score_colormap("x", 30, 100)
    assert colours[0] == cmap(30) and colours[2] == cmap(30)  # below the range: the low end colour
    html = legend_html("Walkability score", 43, 98, relative=True)
    assert "relative to this area" in html
    assert "≤ 43" in html and "≥ 98" in html and ">70<" in html
    assert "Same scale for every area" in legend_html("Walkability score")


def test_map_is_framed_on_the_study_area(segs):
    from shapely.geometry import box
    area = box(565000, 5932000, 567000, 5934000)              # 2 km square around the streets
    m = make_map(segs, "traffic_stress", "Traffic stress", area=area)
    html = m.get_root().render()
    bounds = gpd.GeoSeries([area], crs=segs.crs).to_crs(4326).total_bounds
    assert f"{bounds[1]}" in html and f"{bounds[3]}" in html  # fitBounds uses the area, not the streets



def test_markers_for_added_items(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress",
                 markers=[(53.55, 9.97, "crossing", "Added: Marked crossing"),
                          (53.56, 9.98, "school", "Added: Amenity: school"),
                          (53.57, 9.99, "library", "Added: Amenity: library")])
    markers = [c for c in m._children.values() if isinstance(c, folium.Marker)]
    assert [c.location for c in markers] == [[53.55, 9.97], [53.56, 9.98], [53.57, 9.99]]
    icons = [next(i for i in c._children.values() if isinstance(i, folium.DivIcon)) for c in markers]
    assert ["🚸" in i.options["html"] for i in icons] == [True, False, False]
    assert "🏫" in icons[1].options["html"]
    assert "📍" in icons[2].options["html"]                     # unknown type: a plain pin
    assert icons[0].options["icon_anchor"] == (15, 15)         # centred on the clicked point
    assert "Added: Amenity: school" in m.get_root().render()


def _leaflet_script(m):
    """The map script streamlit-folium runs in its iframe (header and body HTML are set with innerHTML)."""
    from streamlit_folium import _get_map_string
    m.get_root().render()
    return _get_map_string(m)


def test_add_mode_shows_a_crosshair_and_keeps_dragging(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress", adding=True)
    header = m.get_root().header.render()
    assert ".leaflet-container.leaflet-grab, .leaflet-container .leaflet-interactive{cursor:crosshair;}" in header
    assert ".leaflet-dragging .leaflet-container.leaflet-grab" in header       # grabbing hand while dragging
    assert m.options.get("dragging", True) is not False
    layer = geojson_layers(m)[-1]
    assert not any(isinstance(c, folium.GeoJsonPopup) for c in layer._children.values())   # a click only places
    assert any(isinstance(c, folium.GeoJsonTooltip) for c in layer._children.values())
    assert 'page.dispatchEvent(new KeyboardEvent(type, {key: "Escape"' in _leaflet_script(m)


def test_explore_mode_has_the_normal_pointer_and_popups(segs):
    m = make_map(segs, "traffic_stress", "Traffic stress")
    assert "crosshair" not in m.get_root().header.render()
    assert "KeyboardEvent" not in _leaflet_script(make_map(segs, "traffic_stress", "Traffic stress"))
    layer = geojson_layers(m)[-1]
    assert any(isinstance(c, folium.GeoJsonPopup) for c in layer._children.values())
