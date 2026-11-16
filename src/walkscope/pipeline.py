"""From a place name to a scored network: the one entry point the app calls."""

from __future__ import annotations

from typing import Any, Callable

import geopandas as gpd
from shapely.geometry.base import BaseGeometry

from walkscope.config import load_indicator_params, load_personas
from walkscope.fetch import AreaError, area_from_place, fetch_area
from walkscope.indicators.amenity import amenities_reached, amenity_score
from walkscope.indicators.connectivity import connectivity_score, intersection_density
from walkscope.indicators.infrastructure import (
    infrastructure_score,
    infrastructure_subscores,
    split_street_points,
)
from walkscope.indicators.traffic import effective_speed, traffic_score
from walkscope.indicators.transit import distance_score, stop_distance
from walkscope.network import Network, build_network

# Persona whose infrastructure mix fills the `infrastructure` column until the
# persona switch (build step 7) recomputes it from the stored sub-scores.
DEFAULT_PERSONA = "general_adult"

# Progress steps reported to the `progress` callback, in order.
STEP_FIND = "Finding the place"
STEP_DOWNLOAD = "Downloading map data"
STEP_SCORE = "Scoring streets"

Progress = Callable[[str], None]


def _no_progress(step: str) -> None:
    pass


def area_km2(area: BaseGeometry) -> float:
    """Area of a lat/lon geometry in km²."""
    projected = gpd.GeoSeries([area], crs="EPSG:4326")
    return float(projected.to_crs(projected.estimate_utm_crs()).area.iloc[0] / 1e6)


def score_area(area: BaseGeometry, params: dict[str, Any] | None = None,
               progress: Progress = _no_progress) -> Network:
    """Download, build and score one study area (EPSG:4326 polygon).

    Adds indicator columns to `network.segments`. So far: `traffic_stress`,
    `speed_kmh`, `speed_tagged`; `infrastructure` (general-adult mix) and its
    sub-scores `infra_sidewalk`, `infra_crossing`, `infra_kerb`,
    `infra_surface`, `infra_lit`, `infra_base`; `connectivity` and
    `intersection_density` (per km²); `transit` and `transit_distance_m`
    (NaN if no stop within reach); `amenity` and one boolean column per
    category, `amenity_<category>`.
    `progress` is called with STEP_DOWNLOAD and STEP_SCORE as each starts.
    Raises AreaError (too large, no streets) or fetch.ServerBusyError.
    """
    params = params or load_indicator_params()
    size = area_km2(area)
    limit = params["area"]["max_km2"]
    if size > limit:
        raise AreaError(
            f"This area is {size:.0f} km²; WalkScope handles up to {limit:g} km².",
            hint="Try a neighbourhood or district instead of a whole town.",
        )

    progress(STEP_DOWNLOAD)
    data = fetch_area(area, params)
    progress(STEP_SCORE)
    net = build_network(data, params)
    seg = net.segments
    if not seg["scored"].any():
        raise AreaError(
            "No streets to score inside this area; it may be a single square or building.",
            hint="Try the surrounding neighbourhood or district.",
        )
    seg[["speed_kmh", "speed_tagged"]] = effective_speed(seg, params)
    seg["traffic_stress"] = traffic_score(seg, params)

    crossings, kerbs = split_street_points(net.street_points, params)
    subscores = infrastructure_subscores(seg, crossings, kerbs, params)
    seg[subscores.columns] = subscores
    seg["infrastructure"] = infrastructure_score(subscores, load_personas()[DEFAULT_PERSONA])

    seg["intersection_density"] = intersection_density(net.nodes, seg, params)
    seg["connectivity"] = connectivity_score(net.nodes, seg, params)

    seg["transit_distance_m"] = stop_distance(net.graph, net.nodes, seg, net.stops, params)
    seg["transit"] = distance_score(seg["transit_distance_m"], params)

    reached = amenities_reached(net.graph, net.nodes, seg, net.pois, params)
    seg[reached.columns] = reached
    seg["amenity"] = amenity_score(reached)
    return net


def score_place(name: str, params: dict[str, Any] | None = None,
                progress: Progress = _no_progress) -> Network:
    """Geocode a place name and score it. See score_area.

    `progress` is called with STEP_FIND, STEP_DOWNLOAD and STEP_SCORE as each starts.
    """
    progress(STEP_FIND)
    return score_area(area_from_place(name), params, progress)
