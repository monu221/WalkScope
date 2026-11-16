# Example data

OpenStreetMap data for the three example areas, shipped with WalkScope so the
example buttons open in seconds without calling the public map servers.

**© OpenStreetMap contributors.** This data is available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/);
see <https://www.openstreetmap.org/copyright>. It is *not* covered by
WalkScope's MIT licence, which applies to the code only. If you redistribute
these files or data derived from them, keep this notice with them and share
the result under the ODbL.

| File | Area | Downloaded |
| --- | --- | --- |
| `sternschanze.pkl.gz` | Sternschanze, Hamburg (boundary from Nominatim) | see `index.json` |
| `iserbrook.pkl.gz` | Iserbrook, Hamburg (boundary from Nominatim) | see `index.json` |
| `aachen_centre.pkl.gz` | Aachen city centre, a drawn 1.5 km² square | see `index.json` |

Each file is a gzipped Python pickle of `walkscope.fetch.AreaData`: the
study area, the area plus its 1,000 m download buffer, the walking network
(OSMnx graph) and the amenity points, transit stops, and crossing and kerb
nodes, extracted from OpenStreetMap through the Overpass API with the filters
in `src/walkscope/fetch.py` and `config/indicators.yaml`. `index.json` lists
each file's place name, outline, download date and the cache key it matches.

These are derived extracts, not the full OpenStreetMap database. To rebuild
them from current OpenStreetMap data, run:

    .venv/Scripts/python scripts/refresh_examples.py

Load the files only with code you trust; pickles can run code when loaded.
