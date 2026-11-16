import folium
import pytest
from shapely.geometry import Polygon, box, mapping

from walkscope.drawing import DRAWN_PREFIX, draw_map, drawn_area_name, polygon_from_drawings


def feature(geom):
    return {"type": "Feature", "properties": {}, "geometry": mapping(geom)}


def test_polygon_from_drawings_takes_the_last_polygon():
    a, b = box(9.9, 53.5, 9.91, 53.51), box(10.0, 53.6, 10.01, 53.61)
    line = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
    assert polygon_from_drawings([feature(a), feature(b), line]).equals(b)
    assert polygon_from_drawings([]) is None
    assert polygon_from_drawings(None) is None
    assert polygon_from_drawings([line]) is None


def test_self_intersecting_outline_is_repaired():
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2)])
    poly = polygon_from_drawings([feature(bowtie)])
    assert poly.is_valid and poly.geom_type == "Polygon" and poly.area == pytest.approx(1)


def test_drawn_area_name_is_stable_and_distinct():
    a = box(9.9, 53.5, 9.91, 53.51)
    rotated = Polygon(list(a.exterior.coords)[2:] + list(a.exterior.coords)[1:3])   # same shape, other start
    name = drawn_area_name(a, 0.74)
    assert name.startswith(f"{DRAWN_PREFIX}, 0.7 km² (")
    assert drawn_area_name(rotated, 0.74) == name
    assert drawn_area_name(box(9.9, 53.5, 9.92, 53.51), 1.5) != name


def test_draw_map_has_polygon_and_rectangle_tools_only():
    m = draw_map()
    draw = next(c for c in m._children.values() if type(c).__name__ == "Draw")
    opts = draw.draw_options
    assert opts["polygon"] and opts["rectangle"]
    assert not any(opts[k] for k in ("polyline", "circle", "marker", "circlemarker"))


def test_draw_map_opens_on_the_current_area():
    area = box(9.95, 53.55, 9.98, 53.57)
    m = draw_map(area)
    lat, lon = m.location
    assert lat == pytest.approx(53.56) and lon == pytest.approx(9.965)
    assert any(isinstance(c, folium.GeoJson) for c in m._children.values())     # dashed outline
    assert not any(isinstance(c, folium.GeoJson) for c in draw_map()._children.values())
