import copy
import time

import geopandas as gpd
import networkx as nx
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
    assert stops["rail_station"].tolist() == [False]

    # Crossing node and kerb node kept; the crossing *way* is not a street point.
    assert sorted(street.index) == [("node", 5), ("node", 6)]
    assert street.loc[("node", 5), "crossing"] == "marked"
    assert street.loc[("node", 6), "kerb"] == "lowered"


def test_classify_marks_rail_stations(raw_features, params):
    import pandas as pd
    from shapely.geometry import Point
    extra = gpd.GeoDataFrame(
        pd.DataFrame([
            {"element": "node", "id": 20, "railway": "station", "station": "subway",
             "public_transport": "station", "geometry": Point(13.401, 52.521)},
            {"element": "node", "id": 21, "railway": "halt", "geometry": Point(13.402, 52.521)},
            {"element": "node", "id": 22, "railway": "tram_stop", "geometry": Point(13.403, 52.521)},
            {"element": "node", "id": 23, "public_transport": "station", "bus": "yes",
             "geometry": Point(13.404, 52.521)},
        ]).set_index(["element", "id"]), geometry="geometry", crs="EPSG:4326")
    _, stops, _ = classify_features(pd.concat([raw_features, extra]), params)
    rail = stops["rail_station"]
    assert rail[("node", 20)] and rail[("node", 21)]            # U-Bahn station, halt
    assert not rail[("node", 22)] and not rail[("node", 23)]    # tram stop, bus station
    assert not rail[("node", 4)]                                 # bus stop


def test_classify_handles_no_features(params):
    pois, stops, street = classify_features(gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"), params)
    assert pois.empty and stops.empty and street.empty
    assert "rail_station" in stops.columns
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


def test_simplify_with_way_parts_keeps_each_ways_speed_and_length():
    import networkx as nx
    # e - a - b - c - d - f: one street between two dead ends, made of OSM
    # ways tagged 30 and 50. All inner nodes are interstitial, so it becomes one edge.
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for n, x in zip("abcd", range(4)):
        g.add_node(n, x=13.4 + x * 0.001, y=52.5, street_count=2)
    g.add_node("e", x=13.4, y=52.501, street_count=1)
    g.add_node("f", x=13.403, y=52.501, street_count=1)

    def way(u, v, osmid, maxspeed, length):
        for a, b in ((u, v), (v, u)):
            g.add_edge(a, b, osmid=osmid, highway="residential", maxspeed=maxspeed, length=length,
                       oneway=False, reversed=a != u)

    way("a", "b", 1, "30", 60.0)
    way("b", "c", 1, "30", 40.0)
    way("c", "d", 2, "50", 25.0)
    way("a", "e", 3, "30", 50.0)
    way("d", "f", 4, "30", 50.0)
    simple = fetch.simplify_with_way_parts(g)
    assert simple.number_of_edges() == 2                            # e-f, both directions
    data = next(iter(simple.get_edge_data("e", "f").values()))
    assert data["length"] == 225.0
    assert sorted(data["maxspeed"]) == ["30", "50"]                 # OSMnx keeps only distinct values
    assert sorted(data["way_parts"]) == [("residential", "30", 40.0), ("residential", "30", 50.0),
                                         ("residential", "30", 50.0), ("residential", "30", 60.0),
                                         ("residential", "50", 25.0)]


# --- waiting limits (build step 11) --------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """Fake time: sleeping (ours or OSMnx's) advances it; `now["t"] += x` simulates slow requests."""
    now = {"t": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        now["t"] += s
    monkeypatch.setattr(fetch, "_clock", lambda: now["t"])
    monkeypatch.setattr(fetch, "_sleep", sleep)
    now["slept"] = slept
    return now


def test_request_timeout_and_query_timeout_are_60_s():
    assert fetch.REQUEST_TIMEOUT_S == 60
    assert ox.settings.requests_timeout == 60
    assert "[timeout:60]" in ox.settings.overpass_settings


def test_silent_server_gives_busy_within_the_budget(clock):
    """Each try hangs for the full request timeout: the old settings needed 3 x 180 + 15 = 555 s."""
    timeouts = []

    def hang():
        timeouts.append(ox.settings.requests_timeout)
        clock["t"] += ox.settings.requests_timeout
        raise requests.exceptions.ReadTimeout("read timed out")

    with pytest.raises(ServerBusyError) as err:
        with_retry(hang, "https://a/api")
    assert clock["t"] <= fetch.DOWNLOAD_BUDGET_S
    # 60 s, wait 5, 60 s, wait 10, and the last try gets the 15 s left: busy at exactly 150 s.
    assert timeouts == [60, 60, 15] and clock["t"] == 150
    assert len(err.value.attempts) == 3


def test_last_try_gets_only_the_time_left(clock):
    timeouts = []

    def slow_then_fail():
        timeouts.append(ox.settings.requests_timeout)
        clock["t"] += 40
        raise requests.exceptions.ConnectionError("reset")

    with pytest.raises(ServerBusyError):
        with_retry(slow_then_fail, "https://a/api", deadline=fetch.Deadline(100))
    assert timeouts == [60, 55]                     # 100 - 40 - 5 = 55 s left for try 2
    assert ox.settings.requests_timeout == 60       # restored


def test_osmnx_pause_past_the_deadline_ends_in_busy(clock):
    """OSMnx sleeps 55 s and re-sends after a 504, without limit; the deadline stops that."""
    def osmnx_loop():
        while True:
            ox._overpass.time.sleep(55)

    with pytest.raises(ServerBusyError) as err:
        with_retry(osmnx_loop, "https://a/api", deadline=fetch.Deadline(150))
    assert clock["slept"] == [55, 55]               # a third pause would end past 150 - 10
    assert "OSMnx wanted to pause" in err.value.attempts[-1]
    assert ox._overpass.time is time                # restored


def test_fast_failures_still_get_three_tries(clock):
    def refuse():
        raise busy()                                # a 502 comes back at once

    with pytest.raises(ServerBusyError) as err:
        with_retry(refuse, "https://a/api")
    assert len(err.value.attempts) == 3 and clock["slept"] == [5, 10]


def test_graph_and_features_share_one_budget(monkeypatch, clock):
    def slow_graph(*a, **k):
        clock["t"] += 145                           # succeeds, but uses nearly all of the budget
        raise ox._errors.InsufficientResponseError("none")
    calls = []

    def features(*a, **k):
        calls.append(1)
        return None
    monkeypatch.setattr(fetch.ox, "graph_from_polygon", slow_graph)
    monkeypatch.setattr(fetch.ox, "features_from_polygon", features)
    with pytest.raises(AreaError):                  # the graph call's own error still surfaces
        fetch._download(None, {})

    monkeypatch.setattr(fetch.ox, "graph_from_polygon", lambda *a, **k: (clock.__setitem__("t", clock["t"] + 145)
                                                                       or nx.MultiDiGraph()))
    monkeypatch.setattr(fetch, "simplify_with_way_parts", lambda g: g)
    with pytest.raises(ServerBusyError):            # 5 s left: no time for the features request
        fetch._download(None, {})
    assert calls == []
