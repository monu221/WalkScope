"""Time variants of the features request for the Aachen example; check the scores stay identical.

Run from the project root (makes 5 requests to overpass-api.de; best when the
server is quiet, e.g. early morning CET; see docs/plan.md, "Lighter features
request"):

    .venv/Scripts/python scripts/compare_feature_queries.py [OUT_DIR]

Each raw response is saved to OUT_DIR (default: a new temporary folder).
Variants:

A: OSMnx's query as sent today (one node/way/relation statement per tag value, each with the 69-vertex polygon)
B: A with a bounding box instead of the polygon (OSMnx clips to the polygon afterwards)
C: one statement per key (regex over values); crossing/kerb/highway=crossing only as nodes; polygon
D: C with a bounding box
A2: A again at the end, to see how much the server's load moved
"""
import re
import sys
import tempfile
import time
import warnings
from pathlib import Path

import osmnx as ox
import pandas as pd
import requests

from walkscope import examples, fetch, pipeline   # fetch also makes HTTPS use the OS certificate store
from walkscope.config import load_indicator_params
from walkscope.examples import AACHEN_CENTRE

warnings.filterwarnings("ignore")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="walkscope-queries-"))
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://overpass-api.de/api/interpreter"
# Send OSMnx's headers: overpass-api.de answers HTTP 406 to the default python-requests User-Agent.
HEADERS = {"User-Agent": ox.settings.http_user_agent, "Referer": ox.settings.http_referer,
           "Accept-Language": ox.settings.http_accept_language}
params = load_indicator_params()
tags = fetch.feature_tags(params)
buffered = fetch.buffer_area(AACHEN_CENTRE, params["area"]["buffer_m"])
coord = ox._overpass._make_overpass_polygon_coord_strs(buffered)
assert len(coord) == 1; coord = coord[0]
w, s, e, n = buffered.bounds
bbox = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
ox.settings.overpass_settings = "[out:json][timeout:300]{maxsize}"
A = ox._overpass._create_overpass_features_query(coord, tags)
B = A.replace(f"(poly:{coord!r})", f"({bbox})")

used = {}   # key -> values used as amenity or stop (any element type)
for d in list(params["amenity"]["categories"].values()) + [params["transit"]["stop_tags"]]:
    for k, v in d.items():
        used.setdefault(k, [])
        used[k] += [x for x in v if x not in used[k]]
def compact(spatial):
    parts = [f'nwr["{k}"~"^({"|".join(map(re.escape, v))})$"]{spatial};' for k, v in used.items()]
    for k, v in fetch.STREET_POINT_TAGS.items():
        if v is True:
            parts.append(f'node["{k}"]{spatial};')
        else:
            extra = [x for x in v if x not in used.get(k, [])]
            if extra:
                parts.append(f'node["{k}"~"^({"|".join(extra)})$"]{spatial};')
    return "[out:json][timeout:300];(" + "".join(parts) + ");(._;>;);out;"
C = compact(f'(poly:"{coord}")')
D = compact(f"({bbox})")
variants = {"A": A, "B": B, "C": C, "D": D, "A2": A}
print("polygon vertices", len(buffered.exterior.coords), {k: (len(q), q.count("poly:") + q.count(bbox)) for k, q in variants.items()}, flush=True)
print(C[:600], flush=True)

def wait_for_slot():
    for _ in range(60):
        st = requests.get("https://overpass-api.de/api/status", headers=HEADERS, timeout=30).text
        if "slots available now" in st or "Slot available after" not in st:
            return
        m = re.findall(r"in (\d+) seconds", st)
        time.sleep(min(int(m[0]) if m else 10, 60) + 1)

results = {}
for name, q in variants.items():
    wait_for_slot()
    t0 = time.perf_counter()
    r = requests.post(URL, data={"data": q}, headers=HEADERS, timeout=400)
    dt = time.perf_counter() - t0
    ok = r.status_code == 200
    data = r.json() if ok else None
    (OUT / f"{name}.json").write_bytes(r.content)
    nel = len(data["elements"]) if ok else None
    print(f"{name}: HTTP {r.status_code}, {dt:.1f} s, {len(r.content)/1e6:.2f} MB, {nel} elements", flush=True)
    if not ok:
        print(f"  {r.text[:300]}", flush=True)
    results[name] = data
    time.sleep(5)

def classify(data):
    gdf = ox.features._create_gdf([data], buffered, tags)
    return fetch.classify_features(gdf, params)

def norm(df, cols):
    d = pd.DataFrame({c: df[c].astype(str) for c in cols})
    d["wkt"] = df.geometry.apply(lambda g: g.wkt)
    return sorted(map(tuple, d.to_numpy().tolist()))

print(f"Responses saved in {OUT}", flush=True)
base_bundle = examples.bundled_data(fetch.cache_key(AACHEN_CENTRE, params))
scored = {}
for name, data in results.items():
    if data is None: continue
    pois, stops, sp = classify(data)
    sig = (norm(pois, ["category"]), norm(stops, ["rail_station"]), norm(sp, ["highway", "crossing", "kerb"]))
    ad = fetch.AreaData(base_bundle.area, base_bundle.buffered, base_bundle.graph, pois, stops, sp)
    orig = pipeline.fetch_area
    pipeline.fetch_area = lambda a, p, **kw: ad
    try:
        net = pipeline.score_area(AACHEN_CENTRE, params)
    finally:
        pipeline.fetch_area = orig
    seg = net.segments[net.segments["scored"]]
    cols = ["amenity", "transit", "infrastructure", "infra_crossing", "infra_kerb", "traffic_stress", "connectivity"]
    scored[name] = (sig, seg[cols].round(9))
    print(f"{name}: {len(pois)} pois, {len(stops)} stops, {len(sp)} street points", flush=True)
ref_sig, ref = scored["A"]
for name, (sig, sc) in scored.items():
    print(f"{name} vs A: inputs identical={sig == ref_sig}, scores identical={sc.equals(ref)}", flush=True)
