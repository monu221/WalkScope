import geopandas as gpd
import pytest
from shapely.geometry import Point

from walkscope.network import TAG_COLUMNS, build_network, nearest_nodes


@pytest.fixture
def net(area_data):
    return build_network(area_data)


def test_everything_is_in_one_metric_crs(net):
    assert gpd.GeoSeries([], crs=net.crs).crs.is_projected
    for gdf in (net.nodes, net.segments, net.pois, net.stops, net.street_points):
        assert gdf.crs == net.crs
    assert net.graph.graph["crs"] == net.crs or str(net.graph.graph["crs"]) == net.crs


def test_each_street_appears_once(net):
    # 12 streets in the grid, each stored in both directions in the graph.
    assert net.graph.number_of_edges() == 24
    assert len(net.segments) == 12
    assert net.segments.index.name == "segment_id"
    assert list(net.segments.index) == list(range(12))


def test_segment_lengths_and_geometry_in_metres(net):
    assert net.segments["length"].tolist() == [100.0] * 12
    assert net.segments.geometry.length.between(90, 110).all()


def test_in_area_uses_segment_midpoint(net):
    # Area covers grid columns 0 to 1.2: midpoints of 3 horizontal + 4 vertical segments.
    assert net.segments["in_area"].sum() == 7
    outside = net.segments[~net.segments["in_area"]]
    assert (outside.geometry.interpolate(0.5, normalized=True).x > net.area.bounds[2]).all()


def test_plain_footways_are_scored(net):
    # The fixture's footways have no `footway=` tag: paths of their own, so scored.
    assert (net.segments["scored"] == net.segments["in_area"]).all()
    assert not net.segments["routing_only"].any()


def test_sidewalk_ways_are_routing_only(area_data):
    # Tag the in-area footway between nodes 0 and 3 as a road's sidewalk.
    for u, v in ((0, 3), (3, 0)):
        area_data.graph.edges[u, v, 0]["footway"] = "sidewalk"
    net = build_network(area_data)
    seg = net.segments
    sidewalk = seg[seg["footway"] == "sidewalk"]
    assert len(sidewalk) == 1
    assert sidewalk["in_area"].iloc[0] and sidewalk["routing_only"].iloc[0]
    assert not sidewalk["scored"].iloc[0]
    assert seg["scored"].sum() == 6                  # 7 in area minus the sidewalk
    assert net.graph.has_edge(0, 3)                  # still in the graph for walking distances


def test_driveways_and_corridors_are_routing_only(area_data):
    for u, v in ((0, 1), (1, 0)):                    # in-area residential street
        area_data.graph.edges[u, v, 0].update(highway="service", service="driveway")
    for u, v in ((1, 4), (4, 1)):                    # in-area footway
        area_data.graph.edges[u, v, 0]["highway"] = "corridor"
    seg = build_network(area_data).segments
    assert seg.loc[seg["service"] == "driveway", "routing_only"].all()
    assert seg.loc[seg["highway"] == "corridor", "routing_only"].all()
    assert seg["scored"].sum() == 5


def test_measure_node_is_nearest_to_midpoint(net):
    segs = net.segments
    assert all(m in (u, v) for m, u, v in zip(segs["measure_node"], segs["u"], segs["v"]))


def test_all_tag_columns_present(net):
    for col in TAG_COLUMNS:
        assert col in net.segments.columns
    assert net.segments["sidewalk"].isna().all()    # not in the fixture, still a column
    assert set(net.segments["highway"]) == {"residential", "footway"}


def test_nodes_keep_street_count(net):
    assert net.nodes.loc[4, "street_count"] == 4    # centre of the grid
    assert net.nodes.loc[0, "street_count"] == 2    # corner


def test_nearest_nodes():
    nodes = gpd.GeoDataFrame({"street_count": [3, 3]}, geometry=[Point(0, 0), Point(100, 0)],
                             index=[11, 22], crs=32633)
    points = gpd.GeoSeries([Point(90, 5), Point(10, -5), Point(49, 0)], index=[7, 8, 9], crs=32633)
    assert nearest_nodes(nodes, points).to_dict() == {7: 22, 8: 11, 9: 11}
