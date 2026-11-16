"""Folium map of segment scores, and the colour legend shown below it."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

import branca.colormap as cm
import folium
from branca.element import Element
import geopandas as gpd
import pandas as pd
from shapely.geometry.base import BaseGeometry

from walkscope.scoring import display_name, road_type

# Diverging red -> blue (low -> high), readable with red-green colour blindness.
# The middle is a deep yellow so it still shows on a light basemap.
SCORE_COLORS = ["#b2182b", "#ef8a62", "#e0b84a", "#67a9cf", "#2166ac"]

# Standard OpenStreetMap tiles (no API key), shown in light grey so the
# coloured streets stand out. Only the tile layer is filtered, not the streets.
# CARTO's grey "Positron" tiles were the first choice, but xyzservices now
# lists them as needing an API key.
BASEMAP = "OpenStreetMap"
GREY_TILES_CSS = (
    "<style>.leaflet-tile-pane{filter:grayscale(100%) brightness(1.06) contrast(0.82);}</style>"
)

HIGHLIGHT_COLOR = "#111111"


def score_colormap(label: str) -> cm.LinearColormap:
    return cm.LinearColormap(SCORE_COLORS, vmin=0, vmax=100, caption=f"{label} (0 = worst, 100 = best)")


def view_for_bounds(bounds: tuple[float, float, float, float], width_px: int,
                    height_px: int, padding: float = 0.9) -> tuple[list[float], int]:
    """Centre [lat, lon] and the largest whole zoom level that shows lon/lat bounds in a map of this size.

    Used as the map's initial view so it opens on the area even before (or
    without) Leaflet's fitBounds running.
    """
    minx, miny, maxx, maxy = bounds

    def merc_y(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    frac_x = max((maxx - minx) / 360, 1e-9)
    frac_y = max((merc_y(maxy) - merc_y(miny)) / (2 * math.pi), 1e-9)
    zoom = math.floor(math.log2(min(width_px * padding / (256 * frac_x),
                                    height_px * padding / (256 * frac_y))))
    return [(miny + maxy) / 2, (minx + maxx) / 2], max(1, min(18, zoom))


def make_map(
    segments: gpd.GeoDataFrame,
    column: str,
    label: str,
    area: BaseGeometry | None = None,
    details: Mapping[str, pd.Series] | None = None,
    highlight: Iterable[int] | None = None,
    width_px: int = 1100,
    height_px: int = 600,
) -> folium.Map:
    """Map of scored segments coloured by `column`, with the study-area outline.

    Hovering a street shows its name and score; clicking it opens a popup with
    name, road type, score and the `details` (label -> per-segment text,
    indexed like `segments`). Segments whose id is in `highlight` get a dark
    outline. The legend is not drawn on the map; show `legend_html` below it.
    """
    seg = segments[segments["scored"]]
    details = dict(details or {})
    data = gpd.GeoDataFrame(
        {
            "segment_id": seg.index,
            "street": seg["name"].map(display_name).fillna("(unnamed)").to_numpy(),
            "type": seg["highway"].map(road_type).to_numpy(),
            "score": seg[column].round().astype(int).to_numpy(),
            **{name: values.reindex(seg.index).fillna("–").astype(str).to_numpy()
               for name, values in details.items()},
        },
        geometry=seg.geometry.to_numpy(),
        crs=seg.crs,
    ).to_crs(4326)

    minx, miny, maxx, maxy = data.total_bounds
    centre, zoom = view_for_bounds((minx, miny, maxx, maxy), width_px, height_px)
    m = folium.Map(location=centre, zoom_start=zoom, tiles=BASEMAP, control_scale=True)
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    m.get_root().header.add_child(Element(GREY_TILES_CSS))

    if area is not None:
        outline = gpd.GeoSeries([area], crs=seg.crs).to_crs(4326)
        folium.GeoJson(
            outline.__geo_interface__,
            name="Study area",
            style_function=lambda _: {"fill": False, "color": "#555", "weight": 1.5, "dashArray": "5 5"},
            interactive=False,
        ).add_to(m)

    selected = set(highlight or ())
    if selected:
        # Drawn under the score layer, wider, so it shows as a dark outline around the street.
        folium.GeoJson(
            data[data["segment_id"].isin(selected)][["segment_id", "geometry"]].__geo_interface__,
            name="Selected street",
            style_function=lambda _: {"color": HIGHLIGHT_COLOR, "weight": 11, "opacity": 0.9},
            interactive=False,
        ).add_to(m)

    cmap = score_colormap(label)
    detail_fields = list(details)
    folium.GeoJson(
        data.__geo_interface__,
        name=label,
        style_function=lambda f: {"color": cmap(f["properties"]["score"]), "weight": 4, "opacity": 0.95},
        highlight_function=lambda _: {"weight": 8},
        tooltip=folium.GeoJsonTooltip(fields=["street", "score"], aliases=["Street", label]),
        popup=folium.GeoJsonPopup(
            fields=["street", "type", "score", *detail_fields],
            aliases=["Street", "Road type", label, *detail_fields],
        ),
    ).add_to(m)
    return m


def legend_html(label: str) -> str:
    """Colour scale for the map as a small HTML block (white box, gradient bar, 0-100 ticks)."""
    gradient = ", ".join(SCORE_COLORS)
    ticks = "".join(f"<span>{t}</span>" for t in (0, 25, 50, 75, 100))
    return (
        '<div style="background:#fff;border:1px solid #ddd;border-radius:4px;'
        'padding:8px 12px;max-width:420px;font-size:0.85rem;color:#333;">'
        f'<div style="margin-bottom:4px;">{label}: 0 = worst, 100 = best</div>'
        f'<div style="height:10px;border-radius:2px;background:linear-gradient(to right, {gradient});"></div>'
        f'<div style="display:flex;justify-content:space-between;margin-top:2px;color:#666;">{ticks}</div>'
        "</div>"
    )
