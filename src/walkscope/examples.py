"""Example study areas, and their OpenStreetMap data shipped with the app.

The examples are shared by the app, scripts/measure_timing.py and
scripts/refresh_examples.py. Their downloaded data lives in data/examples/
(one gzipped pickle of fetch.AreaData per example, listed in index.json), so
the example buttons open without calling Nominatim or Overpass. That data is
© OpenStreetMap contributors under the ODbL; see data/examples/README.md.

The bundle is used only when it matches what a fresh download would ask for:
fetch.fetch_area looks it up by the same cache key as its disk cache, so a
change to the tags, the buffer or CACHE_VERSION makes the app download again
(and tests/test_examples.py fails until the bundle is refreshed).
"""

from __future__ import annotations

import gzip
import json
import logging
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely import wkt
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from walkscope import paths

log = logging.getLogger(__name__)

# Aachen's districts are larger than 5 km² or don't resolve to an area, so the
# Aachen example is a drawn area: a 1.5 km² square (about 1.22 x 1.22 km)
# centred between the Dom (50.7747 N, 6.0839 E) and the Markt. Lat/lon.
AACHEN_CENTRE = box(6.0752, 50.76999, 6.0926, 50.78101)
AACHEN_CENTRE_NAME = "Aachen city centre (Dom and Markt, 1.5 km²)"

STERNSCHANZE = "Sternschanze, Hamburg"
ISERBROOK = "Iserbrook, Hamburg"

# Bundled examples: file stem -> place name (geocoded) or (display name, drawn area).
BUNDLED: dict[str, str | tuple[str, BaseGeometry]] = {
    "sternschanze": STERNSCHANZE,
    "iserbrook": ISERBROOK,
    "aachen_centre": (AACHEN_CENTRE_NAME, AACHEN_CENTRE),
}

# False makes every lookup miss, so places are geocoded and downloaded fresh
# (scripts/measure_timing.py and scripts/refresh_examples.py).
use_bundle = True

# data/examples in the project directory (see paths.py); found after a regular install too.
EXAMPLES_DIR = paths.EXAMPLES_DIR
INDEX_FILE = "index.json"


def _normalise(name: str) -> str:
    return " ".join(name.casefold().split())


@lru_cache(maxsize=4)
def _index(directory: Path) -> dict[str, dict[str, Any]]:
    """index.json: file stem -> {name, file, key, area_wkt, downloaded}. Empty if missing or unreadable."""
    try:
        return json.loads((directory / INDEX_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("No usable example index in %s: %s", directory, e)
        return {}


def index(directory: Path | None = None) -> dict[str, dict[str, Any]]:
    return _index(Path(directory or EXAMPLES_DIR))


def bundled_area(name: str, directory: Path | None = None) -> BaseGeometry | None:
    """The stored outline of a bundled example, by its place or display name (case and spacing ignored)."""
    if not use_bundle:
        return None
    wanted = _normalise(name)
    for entry in index(directory).values():
        if _normalise(entry["name"]) == wanted:
            return wkt.loads(entry["area_wkt"])
    return None


def bundled_entry(key: str, directory: Path | None = None) -> dict[str, Any] | None:
    """The index entry whose data was downloaded with this cache key, if any."""
    return next((e for e in index(directory).values() if e["key"] == key), None)


def bundled_data(key: str, directory: Path | None = None):
    """fetch.AreaData for this cache key from the bundle, or None.

    None also when the file can't be read (e.g. pickled with library versions
    this install can't load); the caller then downloads as usual.
    """
    entry = bundled_entry(key, directory) if use_bundle else None
    if entry is None:
        return None
    path = Path(directory or EXAMPLES_DIR) / entry["file"]
    try:
        with gzip.open(path, "rb") as f:
            return pickle.load(f)   # our own file, written by scripts/refresh_examples.py
    except Exception as e:  # noqa: BLE001 - any failure means "download instead"
        log.warning("Could not read bundled example %s, downloading instead: %s", path, e)
        return None


def downloaded_on(name: str, directory: Path | None = None) -> str | None:
    """Download date (YYYY-MM-DD) of a bundled example, by its place or display name."""
    wanted = _normalise(name)
    return next((e["downloaded"] for e in index(directory).values() if _normalise(e["name"]) == wanted), None)


def save_bundle(stem: str, name: str, area: BaseGeometry, key: str, data: Any, downloaded: str,
                directory: Path | None = None) -> Path:
    """Write one example's data and its index entry (used by scripts/refresh_examples.py)."""
    directory = Path(directory or EXAMPLES_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    file = f"{stem}.pkl.gz"
    tmp = directory / (file + ".tmp")
    # mtime=0 so an unchanged download gives a byte-identical file.
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(directory / file)

    entries = dict(_index(directory))
    entries[stem] = {"name": name, "file": file, "key": key, "area_wkt": area.wkt, "downloaded": downloaded}
    (directory / INDEX_FILE).write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _index.cache_clear()
    return directory / file
