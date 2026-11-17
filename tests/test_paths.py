"""Finding config/ and data/ after a regular install, and a cache directory that can be written."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from walkscope import paths

REPO = Path(__file__).resolve().parents[1]


def test_the_project_is_found_in_a_source_checkout(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    assert paths.project_dir() == REPO
    assert paths.is_project_dir(REPO)


def test_walkscope_home_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    assert paths.project_dir() == tmp_path.resolve()


def test_cache_in_the_project_when_writable(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.CACHE_ENV, raising=False)
    assert paths.cache_dir(tmp_path) == tmp_path / "data" / "cache"
    assert (tmp_path / "data" / "cache").is_dir()


def test_cache_falls_back_to_the_temp_directory(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.CACHE_ENV, raising=False)
    monkeypatch.setattr(paths.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "data").write_text("a file, so data/cache can't be created")
    assert paths.cache_dir(tmp_path) == tmp_path / "tmp" / paths.TEMP_CACHE_NAME


def test_cache_dir_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.CACHE_ENV, str(tmp_path / "elsewhere"))
    assert paths.cache_dir(tmp_path) == tmp_path / "elsewhere"


def test_regular_install_finds_config_and_examples(tmp_path):
    """`pip install .` (as on Streamlit Community Cloud) copies the package out of the repository:
    with WALKSCOPE_HOME set, as the app does, the config and the bundled examples are still found."""
    # What a regular install puts in site-packages: a copy of the package, without config/ and data/.
    target = tmp_path / "site-packages"
    shutil.copytree(REPO / "src" / "walkscope", target / "walkscope",
                    ignore=shutil.ignore_patterns("__pycache__"))
    code = ("import walkscope.paths as p, walkscope.examples as e; "
            "assert p.__file__.startswith(r'%s'), p.__file__; "
            "print(p.CONFIG_DIR, len(e.index()))" % target)
    # PYTHONPATH puts the installed copy ahead of the editable install of the test environment.
    env = {**os.environ, "PYTHONPATH": str(target), "WALKSCOPE_HOME": str(REPO),
           "WALKSCOPE_CACHE_DIR": str(tmp_path / "cache")}
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    config_dir, n_examples = out.stdout.split()
    assert Path(config_dir) == REPO / "config"
    assert int(n_examples) == 3
