"""Measure download time and calculation time for the timing examples (build step 11).

Run from the project root:

    .venv/Scripts/python scripts/measure_timing.py               # 1 run per area, app limits
    .venv/Scripts/python scripts/measure_timing.py --runs 3
    .venv/Scripts/python scripts/measure_timing.py --no-limits   # see what the server really needs

Each run downloads fresh: OSMnx's HTTP cache is off and area files go to a
temporary folder, so data/cache is neither used nor changed, and the
bundled example data (data/examples) is not used either. Areas:
Sternschanze (place name, geocoded) and the Aachen city-centre square (drawn
area, no geocoding).

Columns: geocode (Nominatim), graph and features (the two Overpass requests,
including OSMnx's own pauses), download (geocode excluded; graph + features +
our retries), calculation (building the network and all five indicators, no
network access), retries (failed tries that were retried).

With the app's limits (60 s per request, 150 s per download) a slow server
ends in "busy"; --no-limits uses 180 s per request and no budget, to measure
how long the server actually takes when it is slow.
"""

from __future__ import annotations

import argparse
import logging
import tempfile
import time
import warnings
from dataclasses import dataclass

import osmnx as ox

from walkscope import examples, fetch, pipeline
from walkscope.config import load_indicator_params
from walkscope.examples import AACHEN_CENTRE, STERNSCHANZE

AREAS = {"Sternschanze": STERNSCHANZE, "Aachen centre": AACHEN_CENTRE}


@dataclass
class Row:
    area: str
    run: int
    km2: float = float("nan")
    segments: int | None = None
    geocode_s: float = float("nan")
    graph_s: float = float("nan")
    features_s: float = float("nan")
    download_s: float = float("nan")
    calculation_s: float = float("nan")
    retries: int = 0
    result: str = "ok"


class _RetryCounter(logging.Handler):
    """Counts the warnings fetch.with_retry logs for each failed try."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if " try " in record.getMessage():
            self.count += 1


def _timed(name: str, store: dict[str, float]):
    """Wrap an OSMnx download function so the time spent in it adds up in store[name]."""
    original = getattr(ox, name)

    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            store[name] = store.get(name, 0.0) + time.perf_counter() - start
    return wrapper


def remove_limits() -> None:
    """OSMnx's old request timeout (180 s) and no waiting budget."""
    fetch.REQUEST_TIMEOUT_S = 180
    fetch.DOWNLOAD_BUDGET_S = fetch.GEOCODE_BUDGET_S = 24 * 3600
    ox.settings.requests_timeout = 180
    ox.settings.overpass_settings = "[out:json][timeout:180]{maxsize}"


def measure(label: str, spec, run: int, params: dict) -> Row:
    row = Row(label, run)
    calls: dict[str, float] = {}
    counter = _RetryCounter()
    logging.getLogger(fetch.__name__).addHandler(counter)
    originals = {n: getattr(ox, n) for n in ("graph_from_polygon", "features_from_polygon")}
    for name in originals:
        setattr(ox, name, _timed(name, calls))
    stage = "geocode_s"
    try:
        start = time.perf_counter()
        area = pipeline.area_from_place(spec) if isinstance(spec, str) else spec
        row.geocode_s = time.perf_counter() - start if isinstance(spec, str) else 0.0
        row.km2 = pipeline.area_km2(area)

        stage, start = "download_s", time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            data = fetch.fetch_area(area, params, cache_dir=tmp)
        row.download_s = time.perf_counter() - start

        fetch_area = pipeline.fetch_area
        pipeline.fetch_area = lambda a, p: data
        try:
            start = time.perf_counter()
            net = pipeline.score_area(area, params)
            row.calculation_s = time.perf_counter() - start
        finally:
            pipeline.fetch_area = fetch_area
        row.segments = int(net.segments["scored"].sum())
    except fetch.ServerBusyError:
        setattr(row, stage, time.perf_counter() - start)     # how long until the busy message
        row.result = "busy" if stage == "download_s" else "busy (geocoding)"
    except Exception as e:  # report and go on with the next run
        row.result = f"{type(e).__name__}: {str(e)[:60]}"
    finally:
        for name, fn in originals.items():
            setattr(ox, name, fn)
        logging.getLogger(fetch.__name__).removeHandler(counter)
    row.graph_s = calls.get("graph_from_polygon", float("nan"))
    row.features_s = calls.get("features_from_polygon", float("nan"))
    row.retries = counter.count
    return row


COLUMNS = [("Area", "area", "{}"), ("Run", "run", "{}"), ("km²", "km2", "{:.2f}"),
           ("Segments", "segments", "{}"), ("Geocode s", "geocode_s", "{:.1f}"), ("Graph s", "graph_s", "{:.1f}"),
           ("Features s", "features_s", "{:.1f}"), ("Download s", "download_s", "{:.1f}"),
           ("Calculation s", "calculation_s", "{:.1f}"), ("Retries", "retries", "{}"), ("Result", "result", "{}")]


def format_table(rows: list[Row]) -> str:
    """Rows as a Markdown table (readable in a terminal, pasteable into docs)."""
    def cell(row: Row, attr: str, fmt: str) -> str:
        value = getattr(row, attr)
        if value is None or (isinstance(value, float) and value != value):
            return "–"
        return fmt.format(value)

    body = [[cell(r, attr, fmt) for _, attr, fmt in COLUMNS] for r in rows]
    heads = [h for h, _, _ in COLUMNS]
    widths = [max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(heads)]
    line = lambda cells: "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"   # noqa: E731
    return "\n".join([line(heads), "|" + "|".join("-" * (w + 2) for w in widths) + "|", *map(line, body)])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=1, help="runs per area (default 1)")
    parser.add_argument("--no-limits", action="store_true",
                        help="180 s per request and no waiting budget, to see what the server needs")
    args = parser.parse_args(argv)

    warnings.filterwarnings("ignore")
    ox.settings.use_cache = False
    examples.use_bundle = False
    if args.no_limits:
        remove_limits()
    params = load_indicator_params()
    print(f"Limits: {fetch.REQUEST_TIMEOUT_S} s per request, "
          + ("no budget" if args.no_limits else f"{fetch.DOWNLOAD_BUDGET_S} s per download")
          + f"; server {fetch.overpass_url()}; {time.strftime('%Y-%m-%d %H:%M')}", flush=True)

    rows = []
    for label, spec in AREAS.items():
        for run in range(1, args.runs + 1):
            row = measure(label, spec, run, params)
            rows.append(row)
            print(f"  {label} run {run}: {row.result}, download {row.download_s:.1f} s, "
                  f"calculation {row.calculation_s:.1f} s", flush=True)
    print()
    print(format_table(rows))


if __name__ == "__main__":
    main()
