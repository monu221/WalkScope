"""Shared fixtures: a tiny hand-made OSM-style area, no network access needed."""

import geopandas as gpd
import networkx as nx
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon, box

from walkscope.fetch import AreaData

# 3 x 3 grid of nodes in Berlin, roughly 100 m apart.
LON0, LAT0 = 13.400, 52.520
DLON, DLAT = 0.00148, 0.0009


def grid_xy(col: float, row: float) -> tuple[float, float]:
    return LON0 + col * DLON, LAT0 + row * DLAT


@pytest.fixture
def grid_graph() -> nx.MultiDiGraph:
    """OSMnx-style walking graph: every street stored in both directions."""
    g = nx.MultiDiGraph(crs="EPSG:4326")
    for row in range(3):
        for col in range(3):
            x, y = grid_xy(col, row)
            g.add_node(row * 3 + col, x=x, y=y, street_count=2 + (0 < row < 2) + (0 < col < 2))

    osmid = 1000
    def street(a, b, **tags):
        nonlocal osmid
        osmid += 1
        for u, v in ((a, b), (b, a)):
            g.add_edge(u, v, key=0, osmid=osmid, length=100.0, **tags)

    for row in range(3):
        for col in range(2):
            street(row * 3 + col, row * 3 + col + 1, highway="residential", name=f"Row {row} Street")
    for col in range(3):
        for row in range(2):
            street(row * 3 + col, (row + 1) * 3 + col, highway="footway")
    return g


@pytest.fixture
def grid_area() -> Polygon:
    """Covers columns 0 to 1.2: 3 horizontal + 4 vertical segment midpoints fall inside."""
    x0, y0 = grid_xy(-0.5, -0.5)
    x1, y1 = grid_xy(1.2, 2.5)
    return box(x0, y0, x1, y1)


@pytest.fixture
def raw_features() -> gpd.GeoDataFrame:
    """What ox.features_from_polygon might return for the grid area."""
    park = Polygon([grid_xy(0.1, 0.1), grid_xy(0.4, 0.1), grid_xy(0.4, 0.4), grid_xy(0.1, 0.4)])
    rows = [
        {"element": "node", "id": 1, "shop": "supermarket", "geometry": Point(grid_xy(1, 1))},
        {"element": "way", "id": 2, "leisure": "park", "geometry": park},
        {"element": "node", "id": 3, "amenity": "pharmacy", "geometry": Point(grid_xy(2, 2))},
        {"element": "node", "id": 4, "highway": "bus_stop", "public_transport": "platform",
         "geometry": Point(grid_xy(0, 2))},
        {"element": "node", "id": 5, "highway": "crossing", "crossing": "marked",
         "geometry": Point(grid_xy(0.5, 0))},
        {"element": "node", "id": 6, "kerb": "lowered", "geometry": Point(grid_xy(0.5, 0.05))},
        {"element": "way", "id": 7, "highway": "footway", "footway": "crossing", "crossing": "marked",
         "geometry": LineString([grid_xy(1.5, 0), grid_xy(1.5, 0.1)])},
        {"element": "node", "id": 8, "amenity": "bench", "geometry": Point(grid_xy(1, 0))},
    ]
    df = pd.DataFrame(rows).set_index(["element", "id"])
    return gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326")


@pytest.fixture
def area_data(grid_graph, grid_area, raw_features) -> AreaData:
    from walkscope.config import load_indicator_params
    from walkscope.fetch import buffer_area, classify_features

    pois, stops, street_points = classify_features(raw_features, load_indicator_params())
    return AreaData(grid_area, buffer_area(grid_area, 800), grid_graph, pois, stops, street_points)
