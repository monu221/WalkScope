"""Tests against the real Nominatim and Overpass APIs.

Skipped by default. Run with:  pytest -m live
"""

import pytest
from shapely.geometry import box

from walkscope.fetch import area_from_place, fetch_area
from walkscope.network import build_network

pytestmark = pytest.mark.live


def test_geocode_real_place():
    area = area_from_place("Kreuzberg, Berlin")
    assert area.geom_type in ("Polygon", "MultiPolygon")


def test_fetch_and_build_small_area(tmp_path):
    # About 480 m x 450 m in Kreuzberg; 800 m buffer makes the download about 2 x 2 km.
    area = box(13.390, 52.488, 13.397, 52.492)
    net = build_network(fetch_area(area, cache_dir=tmp_path))

    inside = net.segments[net.segments["in_area"]]
    assert len(inside) > 50
    assert inside["name"].notna().any()
    assert len(net.segments) > len(inside)          # buffer streets are kept for routing
    assert set(net.pois["category"]) >= {"groceries", "cafe"}
    assert len(net.stops) > 0
    assert len(net.street_points) > 0
    assert net.segments["sidewalk"].notna().any() or net.segments["sidewalk:both"].notna().any()
