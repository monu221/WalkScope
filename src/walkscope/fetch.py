"""Download OpenStreetMap data for a study area, with a disk cache.

Everything here stays in EPSG:4326 (lat/lon). Projection to metres happens in
network.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import time
import types
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pandas as pd
import requests
import truststore
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from walkscope import examples
from walkscope.config import load_indicator_params

# Verify HTTPS against the operating system's certificate store instead of
# certifi's bundle. Needed where antivirus or a corporate proxy re-signs HTTPS
# traffic with its own root certificate (e.g. Avast Web Shield), which the OS
# trusts but certifi does not.
truststore.inject_into_ssl()

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
# Bump when the cached content or its shape changes, so old cache files are ignored.
# (Re-classifying needs no new download: OSMnx's HTTP cache still has the responses.)
CACHE_VERSION = 4

# All OSMnx HTTP responses (Nominatim and Overpass) are cached here.
ox.settings.use_cache = True
ox.settings.cache_folder = str(CACHE_DIR / "http")

# Way tags kept on graph edges, on top of OSMnx's defaults (name, highway,
# maxspeed, lanes, ...). The indicators read these.
EXTRA_WAY_TAGS = (
    "sidewalk",
    "sidewalk:both",
    "sidewalk:left",
    "sidewalk:right",
    "sidewalk:surface",
    "surface",
    "lit",
    "footway",
    "crossing",
)

def walk_filter() -> str:
    """Overpass filter for the walking network.

    Same as OSMnx's built-in "walk" filter, except that roads tagged
    `sidewalk=separate` are kept. OSMnx drops them (their separately drawn
    sidewalks carry the walking), but WalkScope needs the road to score traffic
    stress and to name the street. See "Separately mapped sidewalks" in
    docs/method.md.
    """
    return (
        f'["highway"]["area"!~"yes"]{ox.settings.default_access}'
        f'["highway"!~"abandoned|bus_guideway|construction|cycleway|motor|no|planned|'
        f'platform|proposed|raceway|razed|rest_area|services"]'
        f'["foot"!~"no"]["service"!~"private"]'
    )


def _join_parts(values: list[tuple]) -> tuple:
    return tuple(part for parts in values for part in parts)


def simplify_with_way_parts(raw: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Simplify an unsimplified OSMnx graph, keeping what each merged way contributed.

    OSMnx simplification merges the OSM ways between two intersections into
    one edge and keeps only the distinct tag values (e.g. maxspeed [30, 50]),
    losing which part had which value. Each simplified edge here also gets
    `way_parts`: a tuple of (highway, maxspeed, length in metres), one per
    original edge piece, so traffic stress can weight mixed segments by length.
    """
    for _, _, data in raw.edges(data=True):
        data["way_parts"] = ((data.get("highway"), data.get("maxspeed"), float(data["length"])),)
    return ox.simplify_graph(raw, edge_attr_aggs={"length": sum, "way_parts": _join_parts})


# Point features on the street itself, used by the infrastructure indicator.
STREET_POINT_TAGS: dict[str, Any] = {"highway": ["crossing"], "crossing": True, "kerb": True}


DEFAULT_AREA_HINT = ("Try a neighbourhood or district with its city, "
                     "for example “Sternschanze, Hamburg”.")


class AreaError(ValueError):
    """Raised when a place can't be turned into a usable study area.

    `str(error)` says what went wrong; `hint` says what to try next. Both are
    written for users.
    """

    def __init__(self, message: str, hint: str = DEFAULT_AREA_HINT):
        super().__init__(message)
        self.hint = hint


# --- Overpass server and retry -----------------------------------------------

log = logging.getLogger(__name__)
T = TypeVar("T")

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api"
OVERPASS_URL_ENV = "WALKSCOPE_OVERPASS_URL"

TRIES = 3
FIRST_WAIT_S = 5          # wait before the 2nd try; doubles before each further try

# Limits on waiting, so a busy or silent server ends in the busy message instead
# of hanging (docs/plan.md, "Download robustness"). OSMnx itself pauses without
# limit: before each request while the server's status endpoint says all slots
# are taken, and for 55 s before re-sending after a 429 or 504 reply.
# Set from measurements on overpass-api.de (scripts/measure_timing.py
# --no-limits, 2026-11-16): the graph request took about 70 s and the features
# request 105-181 s, about 180-250 s per download. A slow success is better
# than a quick failure, so the limits leave room for the slowest request and
# a retry; the app shows elapsed time meanwhile.
REQUEST_TIMEOUT_S = 300   # one HTTP request; also the Overpass query's own [timeout:]
DOWNLOAD_BUDGET_S = 600   # everything for one area download (graph + features): tries, pauses, timeouts
GEOCODE_BUDGET_S = 45     # the same for finding a place name (measured: about 1 s)
MIN_TRY_S = 10            # don't start a try (or an OSMnx pause) with less time than this left

# Fixed in the query so it doesn't change when a late try gets a shorter client
# timeout (the query text is also OSMnx's HTTP cache key).
ox.settings.requests_timeout = REQUEST_TIMEOUT_S
ox.settings.overpass_settings = f"[out:json][timeout:{REQUEST_TIMEOUT_S}]{{maxsize}}"

BUSY_MESSAGE = "The map data server is busy. Please try again in a minute."

# Errors worth retrying: HTTP error status (502/504/429 when a server is at
# capacity), timeouts, dropped connections, truncated responses.
RETRYABLE_ERRORS = (ox._errors.ResponseStatusCodeError, requests.exceptions.RequestException)

_sleep = time.sleep       # replaced in tests
_clock = time.monotonic   # replaced in tests


class Deadline:
    """A point in time after which no more waiting is allowed."""

    def __init__(self, seconds: float):
        self.end = _clock() + seconds

    def remaining(self) -> float:
        return self.end - _clock()


class OutOfTime(Exception):
    """An OSMnx pause would run past the deadline."""


@contextmanager
def _limit_waits(deadline: Deadline) -> Iterator[None]:
    """Inside the block, OSMnx's own pauses raise OutOfTime instead of running past the deadline,
    and each HTTP request may take at most the time left (and at most REQUEST_TIMEOUT_S)."""
    def sleep(seconds: float) -> None:
        if seconds > deadline.remaining() - MIN_TRY_S:
            raise OutOfTime(f"OSMnx wanted to pause {seconds:.0f} s with {deadline.remaining():.0f} s left")
        _sleep(seconds)

    modules = (ox._overpass, ox._nominatim)
    old_time = [m.time for m in modules]
    old_timeout = ox.settings.requests_timeout
    for m in modules:
        m.time = types.SimpleNamespace(sleep=sleep)      # they only call time.sleep
    ox.settings.requests_timeout = max(1.0, min(REQUEST_TIMEOUT_S, deadline.remaining()))
    try:
        yield
    finally:
        for m, t in zip(modules, old_time):
            m.time = t
        ox.settings.requests_timeout = old_timeout


class ServerBusyError(RuntimeError):
    """Every retry failed. `str(error)` is the message to show users."""

    def __init__(self, attempts: list[str]):
        super().__init__(BUSY_MESSAGE)
        self.attempts = attempts   # one line per failed attempt, for logs


def overpass_url() -> str:
    """Overpass server: WALKSCOPE_OVERPASS_URL if set, else overpass-api.de."""
    return os.environ.get(OVERPASS_URL_ENV, "").strip().rstrip("/") or DEFAULT_OVERPASS_URL


@contextmanager
def _use_overpass_server(url: str | None) -> Iterator[None]:
    """Point OSMnx at one Overpass server for the duration of the block (None = leave as is)."""
    if url is None:
        yield
        return
    old = (ox.settings.overpass_url, ox.settings.overpass_rate_limit)
    ox.settings.overpass_url = url
    # Only overpass-api.de offers the /status endpoint OSMnx uses for rate limiting.
    ox.settings.overpass_rate_limit = url == DEFAULT_OVERPASS_URL
    try:
        yield
    finally:
        ox.settings.overpass_url, ox.settings.overpass_rate_limit = old


def with_retry(
    fn: Callable[[], T],
    server: str | None,
    tries: int = TRIES,
    first_wait_s: float = FIRST_WAIT_S,
    deadline: Deadline | None = None,
    on_retry: Callable[[int], None] | None = None,
) -> T:
    """Call `fn` against one server, retrying with exponential backoff, until `deadline`.

    `server` is an Overpass URL, or None to leave OSMnx's settings alone (for
    Nominatim). `deadline` defaults to DOWNLOAD_BUDGET_S from now; no try or
    wait (ours or OSMnx's) runs past it. Raises ServerBusyError if every try
    fails with a retryable error or time runs out. Other errors (e.g. "no data
    in this area") are raised immediately. `on_retry(n)` is called before
    try n (2, 3, ...), e.g. to tell the user the server is busy.
    """
    deadline = deadline or Deadline(DOWNLOAD_BUDGET_S)
    attempts: list[str] = []
    wait = first_wait_s
    name = server or "default server"
    for i in range(tries):
        if deadline.remaining() < MIN_TRY_S:
            attempts.append(f"{name}: out of time before try {i + 1}")
            break
        try:
            with _use_overpass_server(server), _limit_waits(deadline):
                return fn()
        except OutOfTime as e:
            attempts.append(f"{name} try {i + 1}: {e}")
            log.warning(attempts[-1])
            break
        except RETRYABLE_ERRORS as e:
            line = f"{name} try {i + 1}: {type(e).__name__}: {str(e)[:200]}"
            attempts.append(line)
            log.warning(line)
            if i < tries - 1:
                if wait > deadline.remaining() - MIN_TRY_S:
                    attempts.append(f"{name}: out of time for try {i + 2}")
                    break
                if on_retry:
                    on_retry(i + 2)
                _sleep(wait)
                wait *= 2
    raise ServerBusyError(attempts)


@dataclass
class AreaData:
    """Raw OSM data for one study area, all in EPSG:4326."""

    area: BaseGeometry            # the study area itself (only this is scored)
    buffered: BaseGeometry        # area + buffer (data was downloaded for this)
    graph: nx.MultiDiGraph        # OSMnx walking network
    pois: gpd.GeoDataFrame        # one row per (feature, amenity category), column `category`
    stops: gpd.GeoDataFrame       # transit stops, column `rail_station` (bool)
    street_points: gpd.GeoDataFrame  # crossing and kerb nodes, columns `highway`, `crossing`, `kerb`


# --- Study area ---------------------------------------------------------------

def area_from_place(name: str) -> BaseGeometry:
    """Geocode a place name to its boundary polygon via Nominatim.

    A bundled example (examples.BUNDLED) uses its stored outline, without
    calling Nominatim. Network failures are retried and end in ServerBusyError; a place that
    can't be found or isn't an area raises AreaError.
    """
    stored = examples.bundled_area(name)
    if stored is not None:
        return stored
    try:
        gdf = with_retry(lambda: ox.geocode_to_gdf(name), server=None, deadline=Deadline(GEOCODE_BUDGET_S))
    except ServerBusyError:
        raise
    except Exception as e:  # OSMnx raises several types for "not found" / "not a polygon"
        log.warning("Geocoding %r failed: %s", name, e)
        raise AreaError(f"Could not find a place called “{name}”.") from e
    geom = gdf.geometry.iloc[0]
    if not isinstance(geom, (Polygon, MultiPolygon)):
        raise AreaError(
            f"“{name}” was found as a single {geom.geom_type.lower()}, not an area.",
            hint="Try a more specific name: a neighbourhood or district with its city.",
        )
    return geom


def buffer_area(area: BaseGeometry, buffer_m: float) -> BaseGeometry:
    """Buffer a lat/lon geometry by a distance in metres."""
    projected, crs = ox.projection.project_geometry(area)
    buffered, _ = ox.projection.project_geometry(projected.buffer(buffer_m), crs=crs, to_latlong=True)
    return buffered


# --- Download -----------------------------------------------------------------

def feature_tags(params: dict[str, Any]) -> dict[str, Any]:
    """Merge amenity, transit and street-point tags into one Overpass tag filter."""
    merged: dict[str, Any] = {}

    def add(key: str, values: Any) -> None:
        if values is True or merged.get(key) is True:
            merged[key] = True
            return
        current = merged.setdefault(key, [])
        for v in values:
            if v not in current:
                current.append(v)

    for category_tags in params["amenity"]["categories"].values():
        for key, values in category_tags.items():
            add(key, values)
    for key, values in params["transit"]["stop_tags"].items():
        add(key, values)
    for key, values in STREET_POINT_TAGS.items():
        add(key, values)
    return merged


# Progress steps for fetch_area's `progress` callback (a busy server adds a note).
STEP_STREETS = "Downloading streets"
STEP_FEATURES = "Downloading shops, stops and crossings"


def _download(buffered: BaseGeometry, tags: dict[str, Any],
              progress: Callable[[str], None] | None = None) -> tuple[nx.MultiDiGraph, gpd.GeoDataFrame]:
    """Fetch the walking graph and the features. The only function that hits Overpass.

    Each of the two requests is retried with backoff, within one shared
    DOWNLOAD_BUDGET_S; ServerBusyError if all tries fail or time runs out.
    `progress` is called with STEP_STREETS and STEP_FEATURES as each starts,
    and with a note when a request is retried.
    """
    progress = progress or (lambda step: None)
    server = overpass_url()
    deadline = Deadline(DOWNLOAD_BUDGET_S)

    def get_graph() -> nx.MultiDiGraph:
        defaults = list(ox.settings.useful_tags_way)
        ox.settings.useful_tags_way = defaults + [t for t in EXTRA_WAY_TAGS if t not in defaults]
        try:
            raw = ox.graph_from_polygon(
                buffered, custom_filter=walk_filter(), simplify=False, retain_all=False
            )
        finally:
            ox.settings.useful_tags_way = defaults
        return simplify_with_way_parts(raw)

    def get_features() -> gpd.GeoDataFrame:
        try:
            return ox.features_from_polygon(buffered, tags)
        except ox._errors.InsufficientResponseError:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    def retrying(step: str) -> Callable[[int], None]:
        return lambda n: progress(f"{step} (map server busy, try {n} of {TRIES})")

    progress(STEP_STREETS)
    try:
        graph = with_retry(get_graph, server, deadline=deadline, on_retry=retrying(STEP_STREETS))
    except ox._errors.InsufficientResponseError as e:
        raise AreaError("No walkable streets found in this area.",
                        hint="Check that the place is a town or neighbourhood, not a lake or forest.") from e
    progress(STEP_FEATURES)
    features = with_retry(get_features, server, deadline=deadline, on_retry=retrying(STEP_FEATURES))
    return graph, features


def _matches(features: gpd.GeoDataFrame, tags: dict[str, Any]) -> pd.Series:
    """Boolean mask of features that carry any of the given tag values."""
    mask = pd.Series(False, index=features.index)
    for key, values in tags.items():
        if key not in features.columns:
            continue
        col = features[key]
        mask |= col.notna() if values is True else col.isin(values)
    return mask


def classify_features(
    features: gpd.GeoDataFrame, params: dict[str, Any]
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Split the downloaded features into amenity POIs, transit stops and street points.

    Areas (parks, school grounds, station buildings) are reduced to a point
    inside them, so every output has point geometry.
    """
    crs = features.crs or "EPSG:4326"
    empty = gpd.GeoDataFrame(geometry=[], crs=crs)
    if features.empty:
        return (
            empty.assign(category=pd.Series(dtype=str)),
            empty.assign(rail_station=pd.Series(dtype=bool)),
            empty.assign(highway=None, crossing=None, kerb=None),
        )

    points = features.copy()
    points["geometry"] = points.geometry.representative_point()

    poi_parts = []
    for category, tags in params["amenity"]["categories"].items():
        part = points.loc[_matches(points, tags), ["geometry"]].copy()
        part["category"] = category
        poi_parts.append(part)
    pois = gpd.GeoDataFrame(pd.concat(poi_parts), geometry="geometry", crs=crs)

    is_stop = _matches(points, params["transit"]["stop_tags"])
    stops = points.loc[is_stop, ["geometry"]].copy()
    stops["rail_station"] = _matches(points, params["transit"]["rail_station_tags"])[is_stop].to_numpy()

    # Crossings and kerbs are only meaningful as nodes on the street, not as ways/areas.
    is_node = features.geom_type == "Point"
    street = features.loc[is_node & _matches(features, STREET_POINT_TAGS)].copy()
    for col in ("highway", "crossing", "kerb"):
        if col not in street.columns:
            street[col] = None
    street_points = gpd.GeoDataFrame(street[["highway", "crossing", "kerb", "geometry"]], crs=crs)

    return pois, gpd.GeoDataFrame(stops, crs=crs), street_points


# --- Cache --------------------------------------------------------------------

def _cache_key(area: BaseGeometry, buffer_m: float, tags: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "version": CACHE_VERSION,
            "area": area.wkt,
            "buffer_m": buffer_m,
            "tags": tags,
            "way_tags": EXTRA_WAY_TAGS,
            "walk_filter": walk_filter(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cache_key(area: BaseGeometry, params: dict[str, Any]) -> str:
    """Key of the downloaded data for this area and parameters (disk cache and bundled examples)."""
    return _cache_key(area, params["area"]["buffer_m"], feature_tags(params))


def fetch_area(
    area: BaseGeometry,
    params: dict[str, Any] | None = None,
    cache_dir: str | Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> AreaData:
    """Return OSM data for the area plus its buffer.

    From the bundled examples or the disk cache if already downloaded, else
    from Overpass (then saved to the disk cache). `progress` gets the download
    steps (see _download).
    """
    params = params or load_indicator_params()
    cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
    buffer_m = params["area"]["buffer_m"]
    tags = feature_tags(params)
    key = _cache_key(area, buffer_m, tags)

    bundled = examples.bundled_data(key)
    if bundled is not None:
        return bundled

    path = cache_dir / f"area_{key}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)  # our own file, written below

    buffered = buffer_area(area, buffer_m)
    graph, features = _download(buffered, tags, progress)
    pois, stops, street_points = classify_features(features, params)
    data = AreaData(area, buffered, graph, pois, stops, street_points)

    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(data, f)
    tmp.replace(path)
    return data
