"""Folium map of segment scores."""

from __future__ import annotations

import branca.colormap as cm
import folium
import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from walkscope.scoring import display_name
from walkscope.tags import as_list

# Diverging red -> blue (low -> high), readable with red-green colour blindness.
# The middle is a deep yellow so it still shows on the light OSM basemap.
SCORE_COLORS = ["#b2182b", "#ef8a62", "#e0b84a", "#67a9cf", "#2166ac"]


def score_colormap(label: str) -> cm.LinearColormap:
    return cm.LinearColormap(SCORE_COLORS, vmin=0, vmax=100, caption=f"{label} (0 = worst, 100 = best)")


def make_map(segments: gpd.GeoDataFrame, column: str, label: str,
             area: BaseGeometry | None = None) -> folium.Map:
    """Map of scored segments coloured by `column`, with the study-area outline."""
    seg = segments[segments["scored"]]
    data = gpd.GeoDataFrame(
        {
            "street": seg["name"].map(display_name).fillna("(unnamed)"),
            "type": seg["highway"].map(lambda h: " / ".join(as_list(h))),
            "score": seg[column].round().astype(int),
        },
        geometry=seg.geometry,
        crs=seg.crs,
    ).to_crs(4326)

    cmap = score_colormap(label)
    minx, miny, maxx, maxy = data.total_bounds
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    m.fit_bounds([[miny, minx], [maxy, maxx]])

    if area is not None:
        outline = gpd.GeoSeries([area], crs=seg.crs).to_crs(4326)
        folium.GeoJson(
            outline.__geo_interface__,
            name="Study area",
            style_function=lambda _: {"fill": False, "color": "#444", "weight": 1.5, "dashArray": "5 5"},
        ).add_to(m)

    folium.GeoJson(
        data.__geo_interface__,
        name=label,
        style_function=lambda f: {"color": cmap(f["properties"]["score"]), "weight": 4, "opacity": 0.9},
        highlight_function=lambda _: {"weight": 8},
        tooltip=folium.GeoJsonTooltip(fields=["street", "type", "score"], aliases=["Street", "Type", label]),
    ).add_to(m)
    cmap.add_to(m)
    return m
