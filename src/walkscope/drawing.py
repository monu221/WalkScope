"""Drawing a study area on the map (build step 9).

The app shows `draw_map`; streamlit-folium returns the drawn shapes as GeoJSON
features (`all_drawings`). `polygon_from_drawings` turns the last polygon or
rectangle into a lat/lon polygon that pipeline.score_area scores like a
geocoded place (same 5 km² limit).
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

import folium
import geopandas as gpd
from branca.element import Element
from folium.plugins import Draw
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry

from walkscope.mapview import BASEMAP, GREY_TILES_CSS, view_for_bounds

DRAWN_PREFIX = "Drawn area"
# Opening view when there is no area to show yet: central Hamburg.
DEFAULT_CENTRE = (53.556, 9.975)
DEFAULT_ZOOM = 13
DRAW_STYLE = {"color": "#2166ac", "weight": 2, "fillOpacity": 0.08}


def polygon_from_drawings(drawings: Iterable[dict[str, Any]] | None) -> Polygon | None:
    """The last drawn polygon (or rectangle) as a valid lat/lon polygon, or None.

    Other shapes are ignored. A self-intersecting outline is repaired and the
    largest part kept.
    """
    for feature in reversed(list(drawings or [])):
        geom = (feature or {}).get("geometry") or {}
        if geom.get("type") != "Polygon":
            continue
        poly = shape(geom)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda p: p.area)
        if isinstance(poly, Polygon) and not poly.is_empty:
            return poly
    return None


def drawn_area_name(polygon: BaseGeometry, km2: float) -> str:
    """Stable, readable name for a drawn area, e.g. 'Drawn area, 1.2 km² (3f9a2c)'.

    The short hash of the rounded outline keeps different drawings apart in
    the app's cache of scored places; the same outline gets the same name.
    """
    digest = hashlib.sha1(polygon.normalize().wkt.encode()).hexdigest()[:6]
    return f"{DRAWN_PREFIX}, {km2:.1f} km² ({digest})"


def draw_map(outline: BaseGeometry | None = None, width_px: int = 1100, height_px: int = 600) -> folium.Map:
    """Map with polygon and rectangle drawing tools.

    Opens on `outline` (lat/lon; the current study area, drawn dashed for
    reference) or on DEFAULT_CENTRE.
    """
    if outline is not None:
        minx, miny, maxx, maxy = outline.bounds
        centre, zoom = view_for_bounds((minx, miny, maxx, maxy), width_px, height_px, padding=0.7)
    else:
        centre, zoom = list(DEFAULT_CENTRE), DEFAULT_ZOOM
    m = folium.Map(location=centre, zoom_start=zoom, tiles=BASEMAP, control_scale=True)
    m.get_root().header.add_child(Element(GREY_TILES_CSS))
    if outline is not None:
        folium.GeoJson(
            gpd.GeoSeries([outline], crs=4326).__geo_interface__,
            name="Current study area",
            style_function=lambda _: {"fill": False, "color": "#555", "weight": 1.5, "dashArray": "5 5"},
            interactive=False,
        ).add_to(m)
    Draw(
        export=False,
        show_geometry_on_click=False,
        draw_options={
            "polygon": {"allowIntersection": False, "shapeOptions": DRAW_STYLE},
            "rectangle": {"shapeOptions": DRAW_STYLE},
            "polyline": False, "circle": False, "marker": False, "circlemarker": False,
        },
        edit_options={"edit": True, "remove": True},
    ).add_to(m)
    return m
