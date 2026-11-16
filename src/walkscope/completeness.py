"""Data completeness: how much of an area's network has the OSM data the scores rely on.

Rules (docs/method.md, "Data completeness"):
- sidewalk: the sidewalk is known from `sidewalk` or the side tags (roads only;
  pedestrian-only segments need no sidewalk);
- kerb: a kerb node with a known `kerb` value within the kerb search radius (50 m);
- lit: a known `lit` value.
Each is the share of scored network length, like every area figure
(docs/method.md, "Area summaries"); the headline is their mean. Where data is
missing, the indicators use the stated assumptions, so a low score next to low
completeness may be an artefact of missing data rather than a bad street.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import pandas as pd

from walkscope.indicators.infrastructure import points_near, sidewalk_values, worst_value
from walkscope.scoring import length_weighted_mean, pedestrian_mask
from walkscope.tags import as_list

FLAG_COLUMNS = ("data_sidewalk", "data_kerb", "data_lit")


def data_flags(segments: gpd.GeoDataFrame, kerbs: gpd.GeoDataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Per segment: is the sidewalk known, is a known kerb nearby, is lighting known (bool columns)."""
    cfg = params["infrastructure"]
    side_cols = [c for c in ("sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right")
                 if c in segments.columns]
    sidewalk = [
        not np.isnan(worst_value(sidewalk_values(dict(zip(side_cols, row)), cfg["sidewalk_sides"]),
                                 cfg["sidewalk"]))
        for row in zip(*(segments[c] for c in side_cols))
    ]
    seg_pos, _ = points_near(segments, kerbs, cfg["kerb"]["search_radius_m"])
    kerb = np.zeros(len(segments), dtype=bool)
    kerb[seg_pos] = True
    lit = segments["lit"].map(lambda v: not np.isnan(worst_value(as_list(v), cfg["lit"]["values"])))
    return pd.DataFrame({"data_sidewalk": sidewalk, "data_kerb": kerb, "data_lit": lit.to_numpy(dtype=bool)},
                        index=segments.index)


@dataclass(frozen=True)
class Completeness:
    """Shares (0-1) of scored network length with the data."""
    sidewalk: float    # of road length
    kerb: float
    lit: float

    @property
    def mean(self) -> float:
        """Headline figure: the mean of the three (NaN parts left out)."""
        parts = [p for p in (self.sidewalk, self.kerb, self.lit) if not np.isnan(p)]
        return float(np.mean(parts)) if parts else float("nan")


def completeness(segments: pd.DataFrame, pedestrian_only: Iterable[str]) -> Completeness:
    """Completeness of the scored segments; needs the FLAG_COLUMNS (see data_flags)."""
    seg = segments[segments["scored"]]
    roads = seg[~pedestrian_mask(seg, pedestrian_only)]
    return Completeness(
        sidewalk=length_weighted_mean(roads["data_sidewalk"], roads["length"]),
        kerb=length_weighted_mean(seg["data_kerb"], seg["length"]),
        lit=length_weighted_mean(seg["data_lit"], seg["length"]),
    )
