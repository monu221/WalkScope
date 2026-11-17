"""What-if scenarios on the offline grid fixture (see conftest.py: 3 x 3 nodes, 100 m apart)."""

import pytest

from tests.conftest import grid_xy
from walkscope import pipeline
from walkscope.config import load_indicator_params
from walkscope.config import load_personas
from walkscope.scenario import (AMENITY, CROSSING, Edit, ScenarioError, apply_edits, check_edit, edit_outcome,
                                item_noun)

PARAMS = load_indicator_params()


@pytest.fixture
def net(monkeypatch, area_data):
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: area_data.area)
    monkeypatch.setattr(pipeline, "fetch_area", lambda area, params, **kw: area_data)
    return pipeline.score_place("Grid")


def segment(seg, u, v):
    """Id of the segment between grid nodes u and v."""
    hit = seg[(seg["u"].isin([u, v])) & (seg["v"].isin([u, v]))]
    return hit.index[0]


def test_crossing_rescores_only_nearby_roads(net):
    before = net.segments
    after, changed = apply_edits(net, [Edit(CROSSING, *grid_xy(0.3, 1))], PARAMS)
    row1 = segment(before, 3, 4)                               # Row 1 Street, columns 0-1
    assert list(changed) == [row1]
    assert before.loc[row1, "infra_crossing"] == 0
    assert after.loc[row1, "infra_crossing"] == 100
    assert after.loc[row1, "infra_base"] == 0.6 * 50 + 0.4 * 100    # sidewalk unknown (50)
    assert after.loc[row1, "infrastructure"] == 70
    others = before.index.difference(changed)
    assert after.loc[others, "infrastructure"].equals(before.loc[others, "infrastructure"])
    assert before.loc[row1, "infra_crossing"] == 0             # the scored network is not changed


def test_crossing_next_to_an_existing_marked_crossing_changes_nothing(net):
    _, changed = apply_edits(net, [Edit(CROSSING, *grid_xy(0.5, 0.02))], PARAMS)
    assert len(changed) == 0                                   # Row 0 already has a marked crossing


def test_crossing_away_from_roads_is_refused(net):
    with pytest.raises(ScenarioError, match="needs a road within 50 m"):
        apply_edits(net, [Edit(CROSSING, *grid_xy(5, 5))], PARAMS)


def test_amenity_shortens_walks_within_800_m(net):
    before = net.segments
    assert before["amenity_school_m"].isna().all()             # no school in the fixture
    after, changed = apply_edits(net, [Edit(AMENITY, *grid_xy(0, 0), category="school")], PARAMS)
    steps = after["measure_node"].map(lambda n: n % 3 + n // 3)  # grid steps from node 0
    assert after["amenity_school_m"].tolist() == pytest.approx((steps * 100).tolist())
    assert set(changed) == set(before.index)                   # every segment is within 800 m
    expected = [max(0, min(100, (800 - d) / 600 * 100)) for d in after["amenity_school_m"]]
    assert after["amenity_school"].tolist() == pytest.approx(expected)
    assert (after["amenity"] - before["amenity"]).tolist() == pytest.approx([e / 6 for e in expected])


def test_amenity_only_counts_when_closer(net):
    # A second supermarket next to the existing one (node 4) changes nothing.
    _, changed = apply_edits(net, [Edit(AMENITY, *grid_xy(1, 1.01), category="groceries")], PARAMS)
    assert len(changed) == 0


def test_amenity_errors(net):
    with pytest.raises(ScenarioError, match="within 100 m of a street"):
        apply_edits(net, [Edit(AMENITY, *grid_xy(5, 5), category="school")], PARAMS)
    with pytest.raises(ScenarioError, match="Unknown amenity category"):
        apply_edits(net, [Edit(AMENITY, *grid_xy(0, 0), category="casino")], PARAMS)


def test_edits_apply_in_order_and_check_edit(net):
    edits = [Edit(CROSSING, *grid_xy(0.3, 1)), Edit(AMENITY, *grid_xy(0, 0), category="school")]
    after, changed = apply_edits(net, edits, PARAMS)
    assert after.loc[segment(after, 3, 4), "infra_crossing"] == 100
    assert after["amenity_school_m"].notna().all()
    check_edit(net, edits, Edit(CROSSING, *grid_xy(0.3, 2)), PARAMS)        # fine
    with pytest.raises(ScenarioError):
        check_edit(net, edits, Edit(CROSSING, *grid_xy(5, 5)), PARAMS)


def test_edit_labels():
    assert Edit(CROSSING, 0, 0).label() == "Marked crossing"
    assert Edit(AMENITY, 0, 0, "school").label() == "Amenity: school"


# --- What one placement did (the message after a click on the map) -------------------

GENERAL = load_personas()["general_adult"]


def test_item_noun():
    assert item_noun(CROSSING) == "marked crossing"
    assert item_noun(AMENITY, "groceries") == "grocery shop"
    assert item_noun(AMENITY, "doctor") == "doctor's practice"
    assert item_noun(AMENITY, "library") == "library"             # unknown category: its own name


def test_outcome_counts_improved_segments_and_road_average(net):
    out = edit_outcome(net, [], Edit(CROSSING, *grid_xy(0.3, 1)), GENERAL, "infrastructure",
                       "Pedestrian infrastructure", PARAMS)
    # Roads: 3 x 100 m at 30, 30, 70 -> 43.3; the crossing lifts Row 1 Street from 30 to 70 -> 56.7.
    assert out.improved == 1 and out.streets == 1
    assert (round(out.before_mean, 1), round(out.after_mean, 1)) == (43.3, 56.7)
    assert out.message == ("Added a marked crossing: 1 street improved. "
                           "Road average pedestrian infrastructure: 43.3 to 56.7.")


def test_outcome_is_relative_to_the_items_already_added(net):
    first = Edit(AMENITY, *grid_xy(0, 0), category="school")
    out = edit_outcome(net, [first], Edit(AMENITY, *grid_xy(0, 0), category="school"), GENERAL, "score",
                       "Walkability score", PARAMS)
    assert out.improved == 0 and out.before_mean == out.after_mean
    assert out.message == ("Added a school, but nothing improved: no street within 800 m on foot is closer "
                           "to this point than to an existing school.")


def test_outcome_explains_a_crossing_where_one_is_already_marked(net):
    out = edit_outcome(net, [], Edit(CROSSING, *grid_xy(0.5, 0.02)), GENERAL, "score", "Walkability score",
                       PARAMS)
    assert out.improved == 0
    assert out.message.endswith("nothing improved: every road within 50 m of this point already counts "
                                "a marked crossing.")


def test_outcome_explains_a_map_that_does_not_count_the_item(net):
    out = edit_outcome(net, [], Edit(CROSSING, *grid_xy(0.3, 1)), GENERAL, "traffic_stress", "Traffic stress",
                       PARAMS)
    assert out.improved == 0
    assert "raised pedestrian infrastructure on 1 segment, but the map shows traffic stress" in out.message
    assert 'Switch "Map shows" to Walkability score or Pedestrian infrastructure' in out.message


def test_outcome_refuses_like_check_edit(net):
    with pytest.raises(ScenarioError, match="needs a road within 50 m"):
        edit_outcome(net, [], Edit(CROSSING, *grid_xy(5, 5)), GENERAL, "score", "Walkability score", PARAMS)
