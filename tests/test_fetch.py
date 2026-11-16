import copy

import geopandas as gpd
import pytest
from shapely.geometry import Point

from walkscope import fetch
from walkscope.config import load_indicator_params
from walkscope.fetch import AreaError, area_from_place, buffer_area, classify_features, feature_tags


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
