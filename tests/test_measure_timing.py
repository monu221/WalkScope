"""scripts/measure_timing.py, offline: the grid fixture stands in for the downloads."""

import importlib.util
import sys
from pathlib import Path

import osmnx as ox
import pytest

from walkscope import fetch, pipeline

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_timing.py"


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("measure_timing", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "measure_timing", module)     # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


def test_measure_separates_download_and_calculation(script, monkeypatch, area_data):
    def fake_fetch(area, params, cache_dir=None):
        ox.graph_from_polygon()                     # timed as the graph request
        return area_data
    monkeypatch.setattr(ox, "graph_from_polygon", lambda *a, **k: None)
    monkeypatch.setattr(fetch, "fetch_area", fake_fetch)
    monkeypatch.setattr(pipeline, "area_from_place", lambda name: area_data.area)

    row = script.measure("Grid", "Grid, Berlin", 1, fetch.load_indicator_params())
    assert row.result == "ok" and row.segments == 7
    assert row.download_s >= 0 and row.calculation_s > 0 and row.graph_s >= 0
    assert row.features_s != row.features_s        # not called: NaN
    assert pipeline.fetch_area is not fake_fetch    # restored
    assert ox.graph_from_polygon.__name__ == "<lambda>"


def test_busy_server_is_a_result_not_a_crash(script, monkeypatch, area_data):
    def busy(area, params, cache_dir=None):
        raise fetch.ServerBusyError(["x"])
    monkeypatch.setattr(fetch, "fetch_area", busy)
    row = script.measure("Aachen centre", area_data.area, 1, fetch.load_indicator_params())
    assert row.result == "busy" and row.geocode_s == 0 and row.segments is None


def test_table(script):
    rows = [script.Row("Sternschanze", 1, 0.55, 379, 1.4, 108.4, 181.2, 295.8, 3.7, 1, "ok"),
            script.Row("Aachen centre", 1, result="busy", download_s=150.0)]
    table = script.format_table(rows).splitlines()
    assert table[0].startswith("| Area ") and "Calculation s" in table[0]
    assert "| 295.8 " in table[2] and "| 3.7 " in table[2]
    assert "| busy " in table[3] and "| – " in table[3]
    assert len({len(line) for line in table}) == 1  # aligned


def test_help_runs(script, capsys):
    with pytest.raises(SystemExit):
        script.main(["--help"])
    assert "--no-limits" in capsys.readouterr().out
