import copy

import geopandas as gpd
import osmnx as ox
import pytest
import requests
from shapely.geometry import Point

from walkscope import fetch
from walkscope.config import load_indicator_params
from walkscope.fetch import (
    BUSY_MESSAGE,
    DEFAULT_OVERPASS_URL,
    AreaError,
    ServerBusyError,
    area_from_place,
    buffer_area,
    classify_features,
    feature_tags,
    overpass_url,
    with_retry,
)


@pytest.fixture
def sleeps(monkeypatch):
    """Record backoff waits instead of sleeping."""
    waits = []
    monkeypatch.setattr(fetch, "_sleep", waits.append)
    return waits


def busy():
    return ox._errors.ResponseStatusCodeError("'overpass-api.de' responded: 502 Bad Gateway")


# --- server and retry ---------------------------------------------------------

def test_default_server(monkeypatch):
    monkeypatch.delenv("WALKSCOPE_OVERPASS_URL", raising=False)
    assert overpass_url() == DEFAULT_OVERPASS_URL == "https://overpass-api.de/api"


def test_server_from_environment(monkeypatch):
    monkeypatch.setenv("WALKSCOPE_OVERPASS_URL", " http://localhost:12345/api/ ")
    assert overpass_url() == "http://localhost:12345/api"
    monkeypatch.setenv("WALKSCOPE_OVERPASS_URL", "")
    assert overpass_url() == DEFAULT_OVERPASS_URL


def test_retry_succeeds_after_failures_with_backoff(sleeps):
    calls = []
    before = ox.settings.overpass_url

    def fn():
        calls.append((ox.settings.overpass_url, ox.settings.overpass_rate_limit))
        if len(calls) < 3:
            raise busy() if len(calls) == 1 else requests.exceptions.ConnectionError("reset")
        return "data"

    assert with_retry(fn, "https://a/api") == "data"
    assert calls == [("https://a/api", False)] * 3     # rate-limit check only on overpass-api.de
    assert sleeps == [5, 10]
    assert ox.settings.overpass_url == before          # setting restored


def test_rate_limit_check_on_default_server(sleeps):
    seen = []
    with_retry(lambda: seen.append(ox.settings.overpass_rate_limit), DEFAULT_OVERPASS_URL)
    assert seen == [True]


def test_all_attempts_fail_gives_busy_error(sleeps):
    def fn():
        raise busy()

    with pytest.raises(ServerBusyError) as err:
        with_retry(fn, "https://a/api")
    assert str(err.value) == BUSY_MESSAGE == "The map data server is busy. Please try again in a minute."
    assert len(err.value.attempts) == 3
    assert sleeps == [5, 10]


def test_other_errors_are_not_retried(sleeps):
    calls = []

    def fn():
        calls.append(1)
        raise KeyError("bug")

    with pytest.raises(KeyError):
        with_retry(fn, "https://a/api")
    assert calls == [1] and sleeps == []


def test_download_reports_empty_area(monkeypatch, sleeps):
    def no_streets(*a, **k):
        raise ox._errors.InsufficientResponseError("No data elements in server response")
    monkeypatch.setattr(fetch.ox, "graph_from_polygon", no_streets)
    with pytest.raises(AreaError, match="No walkable streets"):
        fetch._download(None, {})


def test_geocoding_network_failure_gives_busy_error(monkeypatch, sleeps):
    def down(name):
        raise requests.exceptions.ConnectionError("unreachable")
    monkeypatch.setattr(fetch.ox, "geocode_to_gdf", down)
    with pytest.raises(ServerBusyError):
        area_from_place("Aachen")
    assert len(sleeps) == 2                             # Nominatim: one server, 3 tries


@pytest.fixture
def params():
    return copy.deepcopy(load_indicator_params())


# --- feature_tags -------------------------------------------------------------

def test_feature_tags_merges_all_sources(params):
    tags = feature_tags(params)
    assert {"school", "pharmacy", "cafe", "doctors", "clinic"} <= set(tags["amenity"])
    assert "supermarket" in tags["shop"]
    assert {"park", "playground"} <= set(tags["leisure"])
    assert {"bus_stop", "crossing"} <= set(tags["highway"])   # transit + street points share a key
    assert {"station", "halt", "tram_stop"} <= set(tags["railway"])
    assert tags["crossing"] is True
    assert tags["kerb"] is True


def test_feature_tags_true_wins_over_list(params):
    params["amenity"]["categories"]["school"] = {"kerb": ["lowered"]}
    assert feature_tags(params)["kerb"] is True


def test_walk_filter_keeps_roads_with_separate_sidewalks():
    f = fetch.walk_filter()
    assert "separate" not in f                   # OSMnx's walk filter drops these roads
    assert '["foot"!~"no"]' in f
    assert "cycleway" in f and "motor" in f      # still excluded


# --- classify_features --------------------------------------------------------

def test_classify_splits_pois_stops_and_street_points(raw_features, params):
    pois, stops, street = classify_features(raw_features, params)

    assert sorted(pois["category"]) == ["groceries", "park", "pharmacy"]
    assert (pois.geom_type == "Point").all()
    park = pois[pois["category"] == "park"].geometry.iloc[0]
    assert raw_features.loc[("way", 2)].geometry.contains(park)   # point lies inside the park

    assert list(stops.index) == [("node", 4)]

    # Crossing node and kerb node kept; the crossing *way* is not a street point.
    assert sorted(street.index) == [("node", 5), ("node", 6)]
    assert street.loc[("node", 5), "crossing"] == "marked"
    assert street.loc[("node", 6), "kerb"] == "lowered"


def test_classify_handles_no_features(params):
    pois, stops, street = classify_features(gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), params)
    assert pois.empty and stops.empty and street.empty
    assert "category" in pois.columns
    assert {"highway", "crossing", "kerb"} <= set(street.columns)


# --- area_from_place / buffer_area -------------------------------------------

def test_area_from_place_rejects_points(monkeypatch):
    monkeypatch.setattr(fetch.ox, "geocode_to_gdf",
                        lambda name: gpd.GeoDataFrame(geometry=[Point(13.4, 52.5)], crs="EPSG:4326"))
    with pytest.raises(AreaError, match="not an area"):
        area_from_place("Some Bus Stop")


def test_area_from_place_wraps_lookup_errors(monkeypatch):
    def fail(name):
        raise ValueError("Nominatim could not geocode query")
    monkeypatch.setattr(fetch.ox, "geocode_to_gdf", fail)
    with pytest.raises(AreaError, match="Could not find"):
        area_from_place("Nowhere")


def test_buffer_area_adds_distance_in_metres(grid_area):
    buffered = buffer_area(grid_area, 800)
    assert buffered.contains(grid_area)
    projected = gpd.GeoSeries([grid_area, buffered], crs="EPSG:4326").to_crs(32633)
    gap = projected.iloc[0].exterior.distance(projected.iloc[1].exterior)
    assert gap == pytest.approx(800, rel=0.01)


# --- fetch_area cache ---------------------------------------------------------

def test_fetch_area_downloads_once_then_uses_cache(monkeypatch, tmp_path, grid_graph, grid_area,
                                                   raw_features, params):
    calls = []

    def fake_download(buffered, tags):
        calls.append(buffered)
        return grid_graph, raw_features

    monkeypatch.setattr(fetch, "_download", fake_download)

    first = fetch.fetch_area(grid_area, params, cache_dir=tmp_path)
    second = fetch.fetch_area(grid_area, params, cache_dir=tmp_path)

    assert len(calls) == 1
    assert calls[0].contains(grid_area)            # downloaded the buffered area
    assert second.graph.number_of_edges() == first.graph.number_of_edges()
    assert sorted(second.pois["category"]) == ["groceries", "park", "pharmacy"]

    params["area"]["buffer_m"] = 400               # different settings -> new download
    fetch.fetch_area(grid_area, params, cache_dir=tmp_path)
    assert len(calls) == 2
