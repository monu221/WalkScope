"""Where WalkScope finds its config and example data, and where it caches downloads.

The project directory holds `config/` and `data/examples/`. In a source
checkout (`pip install -e .`) it is the repository around this package. With a
regular install (`pip install .`, as on Streamlit Community Cloud) the package
sits in site-packages, so the project directory comes from WALKSCOPE_HOME (the
app sets it to its own repository) or else the working directory.

Downloads are cached in `data/cache/` if it can be written to, else in the
system temp directory. The cache only saves time: on a server whose disk is
temporary it may be empty after a restart, and the area is downloaded again.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

HOME_ENV = "WALKSCOPE_HOME"            # project directory (holds config/ and data/)
CACHE_ENV = "WALKSCOPE_CACHE_DIR"      # cache directory, if not the default
TEMP_CACHE_NAME = "walkscope-cache"


def is_project_dir(path: Path) -> bool:
    return (path / "config" / "indicators.yaml").is_file()


def project_dir() -> Path:
    """WALKSCOPE_HOME if set, else the source checkout around this package, else the working directory."""
    if home := os.environ.get(HOME_ENV):
        return Path(home).resolve()
    for candidate in (Path(__file__).resolve().parents[2], Path.cwd()):
        if is_project_dir(candidate):
            return candidate
    raise FileNotFoundError(
        f"WalkScope's config/ folder was not found next to the package or in {Path.cwd()}. "
        f"Run from the repository, or set {HOME_ENV} to it.")


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write-test-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def cache_dir(project: Path | None = None) -> Path:
    """WALKSCOPE_CACHE_DIR if set, else <project>/data/cache if writable, else <temp>/walkscope-cache.

    Creates the directory. If none can be written, returns the last choice anyway;
    saving to the cache then fails quietly and every area is downloaded.
    """
    if env := os.environ.get(CACHE_ENV):
        candidates = [Path(env)]
    else:
        candidates = [(project or project_dir()) / "data" / "cache",
                      Path(tempfile.gettempdir()) / TEMP_CACHE_NAME]
    for directory in candidates:
        if _writable(directory):
            return directory
        log.warning("Cache directory %s is not writable", directory)
    return candidates[-1]


PROJECT_DIR = project_dir()
CONFIG_DIR = PROJECT_DIR / "config"
EXAMPLES_DIR = PROJECT_DIR / "data" / "examples"
