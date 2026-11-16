"""The example areas' data shipped in data/examples, used without any network access."""

import gzip
import json

import pytest

from walkscope import examples, fetch, pipeline
from walkscope.config import load_indicator_params
from walkscope.examples import AACHEN_CENTRE, AACHEN_CENTRE_NAME, BUNDLED, ISERBROOK, STERNSCHANZE

REFRESH = "run scripts/refresh_examples.py and commit data/examples"


@pytest.fixture
def params():
    return load_indicator_params()


@pytest.fixture
def no_network(monkeypatch):
    def offline(*args, **kwargs):
        pytest.fail("called the map servers")
    monkeypatch.setattr(fetch, "_download", offline)
    monkeypatch.setattr(fetch.ox, "geocode_to_gdf", offline)


def _outline(spec):
    return examples.bundled_area(spec) if isinstance(spec, str) else spec[1]


@pytest.mark.parametrize("stem", list(BUNDLED))
def test_bundle_matches_the_current_configuration(stem, params):
    """Fails after a change to the buffer, the downloaded tags or CACHE_VERSION: refresh the bundle."""
    entry = examples.index().get(stem)
    assert entry is not None, f"{stem} is not in data/examples/index.json; {REFRESH}"
    assert (examples.EXAMPLES_DIR / entry["file"]).exists()
    outline = _outline(BUNDLED[stem])
    assert outline is not None
    assert fetch.cache_key(outline, params) == entry["key"], f"bundled {stem} is out of date; {REFRESH}"


@pytest.mark.parametrize("place", [STERNSCHANZE, ISERBROOK])
def test_bundled_place_is_scored_without_network(place, no_network, params):
    net = pipeline.score_place(place, params)
    assert net.segments["scored"].sum() > 100
    assert net.segments.loc[net.segments["scored"], "amenity"].between(0, 100).all()


def test_bundled_drawn_area_is_scored_without_network(no_network, params):
    net = pipeline.score_area(AACHEN_CENTRE, params)
    assert net.segments["scored"].sum() > 100


def test_place_names_match_ignoring_case_and_spacing():
    assert examples.bundled_area("  sternschanze,   HAMBURG ") is not None
    assert examples.bundled_area("Sternschanze") is None
    assert examples.downloaded_on(AACHEN_CENTRE_NAME) == examples.index()["aachen_centre"]["downloaded"]
    assert examples.downloaded_on("Kreuzberg, Berlin") is None


def test_switching_the_bundle_off_misses_every_lookup(monkeypatch, params):
    monkeypatch.setattr(examples, "use_bundle", False)
    assert examples.bundled_area(STERNSCHANZE) is None
    assert examples.bundled_data(fetch.cache_key(AACHEN_CENTRE, params)) is None


def test_save_and_load_round_trip(tmp_path, area_data):
    examples.save_bundle("grid", "Grid, Berlin", area_data.area, "abc123", area_data, "2026-01-02",
                         directory=tmp_path)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["grid"]["downloaded"] == "2026-01-02"
    assert examples.bundled_area("grid, berlin", tmp_path).equals(area_data.area)
    loaded = examples.bundled_data("abc123", tmp_path)
    assert len(loaded.graph) == len(area_data.graph)
    assert examples.bundled_data("other", tmp_path) is None


def test_unreadable_bundle_falls_back_to_download(tmp_path, area_data, caplog):
    examples.save_bundle("grid", "Grid", area_data.area, "abc123", area_data, "2026-01-02", directory=tmp_path)
    with gzip.open(tmp_path / "grid.pkl.gz", "wb") as f:
        f.write(b"not a pickle")
    assert examples.bundled_data("abc123", tmp_path) is None
    assert "downloading instead" in caplog.text


def test_fetch_area_prefers_the_bundle(monkeypatch, tmp_path, no_network, params):
    data = fetch.fetch_area(AACHEN_CENTRE, params, cache_dir=tmp_path)
    assert data.area.equals(AACHEN_CENTRE)
    assert not list(tmp_path.iterdir())          # nothing downloaded, nothing written to the disk cache


def test_attribution_ships_with_the_data():
    text = (examples.EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    assert "© OpenStreetMap contributors" in text
    assert "opendatacommons.org/licenses/odbl" in text
