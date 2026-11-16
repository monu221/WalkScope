"""From a place name to a scored network: the one entry point the app calls."""

from __future__ import annotations

from typing import Any

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from walkscope.config import load_indicator_params
from walkscope.fetch import AreaError, area_from_place, fetch_area
from walkscope.indicators.traffic import effective_speed, traffic_score
from walkscope.network import Network, build_network


def area_km2(area: BaseGeometry) -> float:
    """Area of a lat/lon geometry in km²."""
    projected = gpd.GeoSeries([area], crs="EPSG:4326")
    return float(projected.to_crs(projected.estimate_utm_crs()).area.iloc[0] / 1e6)


def score_area(area: BaseGeometry, params: dict[str, Any] | None = None) -> Network:
    """Download, build and score one study area (EPSG:4326 polygon).

    Adds indicator columns to `network.segments`. So far: `traffic_stress`,
    `speed_kmh`, `speed_tagged`.
    Raises AreaError (too large, no streets) or fetch.ServerBusyError.
    """
    params = params or load_indicator_params()
    size = area_km2(area)
    limit = params["area"]["max_km2"]
    if size > limit:
        raise AreaError(
            f"This area is {size:.0f} km²; WalkScope handles up to {limit:g} km². "
            "Try a neighbourhood or district instead of a whole town."
        )

    net = build_network(fetch_area(area, params), params)
    seg = net.segments
    if not seg["scored"].any():
        raise AreaError(
            "No streets to score inside this area; it may be a single square or building. "
            "Try a neighbourhood or district."
        )
    seg[["speed_kmh", "speed_tagged"]] = effective_speed(seg, params)
    seg["traffic_stress"] = traffic_score(seg, params)
    return net


def score_place(name: str, params: dict[str, Any] | None = None) -> Network:
    """Geocode a place name and score it. See score_area."""
    return score_area(area_from_place(name), params)
