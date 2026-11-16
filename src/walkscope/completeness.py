"""Data completeness: how much of an area's network has the OSM data the scores rely on.

Rules (docs/method.md, "Data completeness"):
- sidewalk: the sidewalk is known from `sidewalk` or the side tags (roads only;
  pedestrian-only segments need no sidewalk);
- kerb: a kerb node with a known `kerb` value within the kerb search radius (50 m);
- lit: a known `lit` value;
- surface: a known `sidewalk:surface` or `surface` value (only in persona cautions).
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

from walkscope.config import Persona
from walkscope.indicators.infrastructure import points_near, sidewalk_values, worst_value
from walkscope.scoring import length_weighted_mean, pedestrian_mask
from walkscope.tags import as_list

FLAG_COLUMNS = ("data_sidewalk", "data_kerb", "data_lit", "data_surface")

# Data each part of a persona's infrastructure mix relies on ("base" = sidewalk tags).
MIX_DATA = {"base": "sidewalk", "kerb": "kerb", "lit": "lit", "surface": "surface"}
DATA_LABELS = {
    "sidewalk": "sidewalk tags",
    "kerb": "kerb data",
    "lit": "lighting tags",
    "surface": "surface tags",
}


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
    surface_values = cfg["surface"]["values"]
    surface_cols = [segments[c] for c in cfg["surface"]["tags"] if c in segments.columns]


    def surface_known(row: tuple) -> bool:      # like surface_subscore: the first tag present counts
        for value in row:
            values = as_list(value)
            if values:
                return not np.isnan(worst_value(values, surface_values))
        return False

    surface = [surface_known(row) for row in zip(*surface_cols)] if surface_cols else [False] * len(segments)
    return pd.DataFrame({"data_sidewalk": sidewalk, "data_kerb": kerb, "data_lit": lit.to_numpy(dtype=bool),
                         "data_surface": np.asarray(surface, dtype=bool)},
                        index=segments.index)


@dataclass(frozen=True)
class Completeness:
    """Shares (0-1) of scored network length with the data."""
    sidewalk: float    # of road length
    kerb: float
    lit: float
    surface: float = float("nan")    # not part of the headline mean; used for persona cautions

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
        surface=(length_weighted_mean(seg["data_surface"], seg["length"])
                 if "data_surface" in seg.columns else float("nan")),
    )


def persona_gaps(data: Completeness, persona: Persona, threshold: float) -> list[tuple[str, float]]:
    """Data the persona's scores rely on that covers less than `threshold` of the network.

    Every persona relies on sidewalk tags (the base infrastructure score);
    personas whose infrastructure mix includes kerb, surface or lit also rely
    on that data. Returns (data label, share) pairs, lowest share first.
    """
    gaps = []
    for part in persona.infrastructure_mix:
        key = MIX_DATA[part]
        share = getattr(data, key)
        if not np.isnan(share) and share < threshold:
            gaps.append((DATA_LABELS[key], share))
    return sorted(gaps, key=lambda g: g[1])
