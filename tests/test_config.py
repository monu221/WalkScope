import copy

import pytest
import yaml

from walkscope.config import (
    INDICATORS,
    ConfigError,
    load_indicator_params,
    load_personas,
)


# --- personas.yaml -----------------------------------------------------------

def test_shipped_personas_load():
    personas = load_personas()
    assert list(personas) == ["general_adult", "wheelchair", "child", "night"]
    for p in personas.values():
        assert set(p.weights) == set(INDICATORS)
        assert sum(p.weights.values()) == pytest.approx(100)
        assert sum(p.infrastructure_mix.values()) == pytest.approx(100)


def test_shipped_persona_values_match_method():
    personas = load_personas()
    assert personas["general_adult"].weights == {
        "amenity": 30, "connectivity": 20, "infrastructure": 20, "traffic_stress": 15, "transit": 15,
    }
    assert personas["wheelchair"].infrastructure_mix == {"base": 50, "kerb": 25, "surface": 25}
    assert personas["night"].infrastructure_mix == {"base": 60, "lit": 40}
    assert personas["child"].weights["traffic_stress"] == 35
    assert personas["wheelchair"].impassable_highways == ("steps",)
    assert personas["general_adult"].impassable_highways == ()


def test_impassable_highways_must_be_list_of_strings(tmp_path):
    p = _valid_persona()
    p["impassable_highways"] = "steps"
    with pytest.raises(ConfigError, match="impassable_highways"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def _write(tmp_path, data):
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _valid_persona():
    return {
        "label": "Test",
        "weights": {"amenity": 20, "connectivity": 20, "infrastructure": 20, "traffic_stress": 20, "transit": 20},
        "infrastructure_mix": {"base": 100},
    }


def test_weights_not_summing_to_100_rejected(tmp_path):
    p = _valid_persona()
    p["weights"]["amenity"] = 25
    with pytest.raises(ConfigError, match="sum to 100"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def test_missing_indicator_rejected(tmp_path):
    p = _valid_persona()
    del p["weights"]["transit"]
    p["weights"]["amenity"] = 40
    with pytest.raises(ConfigError, match="missing keys"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def test_unknown_indicator_rejected(tmp_path):
    p = _valid_persona()
    p["weights"]["noise"] = 0
    with pytest.raises(ConfigError, match="unknown keys"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def test_negative_weight_rejected(tmp_path):
    p = _valid_persona()
    p["weights"]["amenity"] = -20
    p["weights"]["transit"] = 60
    with pytest.raises(ConfigError, match="non-negative"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def test_infrastructure_mix_needs_base_and_sum_100(tmp_path):
    p = _valid_persona()
    p["infrastructure_mix"] = {"lit": 100}
    with pytest.raises(ConfigError, match="missing keys"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))

    p["infrastructure_mix"] = {"base": 60, "lit": 30}
    with pytest.raises(ConfigError, match="sum to 100"):
        load_personas(_write(tmp_path, {"personas": {"x": p}}))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_personas(tmp_path / "nope.yaml")


# --- indicators.yaml ---------------------------------------------------------

def test_shipped_indicator_params_match_method():
    params = load_indicator_params()
    assert params["area"]["buffer_m"] == 800
    assert params["walking"]["max_distance_m"] == 800
    assert params["connectivity"]["radius_m"] == 400
    assert (params["connectivity"]["density_zero"], params["connectivity"]["density_full"]) == (30, 150)
    assert (params["transit"]["full_score_m"], params["transit"]["zero_score_m"]) == (300, 800)
    assert params["infrastructure"]["base_mix"] == {"sidewalk": 60, "crossing": 40}
    assert params["infrastructure"]["missing_score"] == 50
    assert params["infrastructure"]["crossing"]["search_radius_m"] == 50
    assert set(params["amenity"]["categories"]) == {"groceries", "school", "pharmacy", "park", "cafe", "doctor"}
    assert set(params["pedestrian_only_highways"]) == {"footway", "pedestrian", "path", "steps"}


def test_yaml_no_values_stay_strings():
    # Unquoted `no` would become False in YAML; the config quotes it.
    params = load_indicator_params()
    assert params["infrastructure"]["sidewalk"]["no"] == 0
    assert params["infrastructure"]["lit"]["values"]["yes"] == 100


def test_residential_30_beats_main_road_50():
    curve = load_indicator_params()["traffic_stress"]["speed_curve"]
    score = dict((s, v) for s, v in curve)
    assert score[30] > score[50]


@pytest.fixture
def shipped_params():
    return copy.deepcopy(load_indicator_params())


def test_inverted_transit_thresholds_rejected(tmp_path, shipped_params):
    shipped_params["transit"]["full_score_m"] = 900
    with pytest.raises(ConfigError, match="transit"):
        load_indicator_params(_write(tmp_path, shipped_params))


def test_inverted_connectivity_thresholds_rejected(tmp_path, shipped_params):
    shipped_params["connectivity"]["density_zero"] = 200
    with pytest.raises(ConfigError, match="connectivity"):
        load_indicator_params(_write(tmp_path, shipped_params))


def test_unsorted_speed_curve_rejected(tmp_path, shipped_params):
    shipped_params["traffic_stress"]["speed_curve"] = [[50, 30], [30, 75]]
    with pytest.raises(ConfigError, match="speed_curve"):
        load_indicator_params(_write(tmp_path, shipped_params))


def test_missing_section_rejected(tmp_path, shipped_params):
    del shipped_params["transit"]
    with pytest.raises(ConfigError, match="missing sections"):
        load_indicator_params(_write(tmp_path, shipped_params))
