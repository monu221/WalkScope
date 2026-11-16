"""Download the example areas again and save them to data/examples (shipped with the app).

Run from the project root:

    .venv/Scripts/python scripts/refresh_examples.py                # all examples, fresh download
    .venv/Scripts/python scripts/refresh_examples.py iserbrook      # one example (file stem)
    .venv/Scripts/python scripts/refresh_examples.py --from-cache   # reuse data/cache where present

Run it when OpenStreetMap has changed enough to matter, and whenever
tests/test_examples.py says the bundle no longer matches the configuration
(after changing the buffer, the downloaded tags or fetch.CACHE_VERSION).
Commit the changed files in data/examples together.

Fresh downloads use generous limits (300 s per request, no overall budget) and
skip OSMnx's HTTP cache and data/cache, so they wait out a slow public server instead of
giving up. Geocoded examples store the outline found today; that outline is
then used for the example without calling Nominatim.

The data is © OpenStreetMap contributors and licensed under the ODbL; the
attribution is in data/examples/README.md and must stay with the files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
import time
import warnings

import osmnx as ox

from walkscope import examples, fetch
from walkscope.config import load_indicator_params

REQUEST_TIMEOUT_S = 300


def remove_limits() -> None:
    fetch.REQUEST_TIMEOUT_S = REQUEST_TIMEOUT_S
    fetch.DOWNLOAD_BUDGET_S = fetch.GEOCODE_BUDGET_S = 24 * 3600
    ox.settings.requests_timeout = REQUEST_TIMEOUT_S
    ox.settings.overpass_settings = f"[out:json][timeout:{REQUEST_TIMEOUT_S}]{{maxsize}}"


def refresh(stem: str, spec, params: dict, from_cache: bool) -> None:
    start = time.perf_counter()
    if isinstance(spec, str):
        name, area = spec, fetch.area_from_place(spec)
    else:
        name, area = spec
    key = fetch.cache_key(area, params)
    cached = fetch.CACHE_DIR / f"area_{key}.pkl"
    if from_cache and cached.exists():
        data = fetch.fetch_area(area, params)
        downloaded = dt.date.fromtimestamp(cached.stat().st_mtime).isoformat()
        source = "data/cache"
    else:
        with tempfile.TemporaryDirectory() as tmp:     # never answered from an older data/cache file
            data = fetch.fetch_area(area, params, cache_dir=tmp)
        downloaded = dt.date.today().isoformat()
        source = "Overpass"
    path = examples.save_bundle(stem, name, area, key, data, downloaded)
    print(f"{stem}: {source}, {path.stat().st_size / 1e6:.1f} MB, downloaded {downloaded}, "
          f"{time.perf_counter() - start:.0f} s", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stems", nargs="*", help=f"examples to refresh (default all): {', '.join(examples.BUNDLED)}")
    parser.add_argument("--from-cache", action="store_true",
                        help="use data/cache (and OSMnx's HTTP cache) where present instead of downloading")
    args = parser.parse_args(argv)
    unknown = set(args.stems) - set(examples.BUNDLED)
    if unknown:
        parser.error(f"unknown example(s): {', '.join(sorted(unknown))}")

    warnings.filterwarnings("ignore")
    examples.use_bundle = False
    if not args.from_cache:
        ox.settings.use_cache = False
        remove_limits()
    params = load_indicator_params()
    failed = []
    for stem in args.stems or examples.BUNDLED:
        try:
            refresh(stem, examples.BUNDLED[stem], params, args.from_cache)
        except Exception as e:  # report and go on with the next example
            failed.append(stem)
            print(f"{stem}: FAILED, {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
