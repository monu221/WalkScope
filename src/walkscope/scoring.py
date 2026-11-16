"""Combine indicator scores with persona weights, and pick the weakest streets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from walkscope.config import INDICATORS, Persona
from walkscope.tags import as_list, is_pedestrian_only, road_classes


def score_segments(segments: pd.DataFrame, persona: Persona) -> pd.Series:
    """Combined 0-100 score: sum of indicator × weight / 100.

    Segments whose highway is in the persona's `impassable_highways` (e.g.
    steps for wheelchair users) score 0.
    """
    missing = [i for i in INDICATORS if i not in segments.columns]
    if missing:
        raise KeyError(f"Indicator columns not computed yet: {missing}")
    score = sum(segments[i] * persona.weights[i] for i in INDICATORS) / 100
    blocked = set(persona.impassable_highways)
    if blocked:
        impassable = segments["highway"].map(lambda h: bool(blocked & set(road_classes(h))))
        score[impassable] = 0.0
    return score.rename("score")


def display_name(name: object) -> str | None:
    """Street name for display; merged segments with two names become "A / B"."""
    names = list(dict.fromkeys(as_list(name)))
    return " / ".join(names) if names else None


def road_type(highway: object) -> str:
    """Highway value(s) for display, e.g. "footway / steps"."""
    return " / ".join(as_list(highway))


def weakest(segments: pd.DataFrame, column: str, n: int = 10,
            extra: Sequence[str] = ()) -> pd.DataFrame:
    """The n lowest-scoring named streets among scored segments.

    One row per street name (its lowest-scoring segment), so a long street
    doesn't fill the table. Unnamed segments are left out (docs/method.md).
    Columns: street, type, `column`, the `extra` columns of that segment, and
    `segment_id`.
    """
    seg = segments[segments["scored"]].copy()
    seg["street"] = seg["name"].map(display_name)
    seg = seg[seg["street"].notna()]
    seg["type"] = seg["highway"].map(road_type)
    seg["segment_id"] = seg.index
    worst = seg.sort_values(column, kind="stable").drop_duplicates("street")
    return worst.head(n)[["street", "type", column, *extra, "segment_id"]].reset_index(drop=True)


# --- Area summaries -------------------------------------------------------------

def length_weighted_mean(values: pd.Series, lengths: pd.Series) -> float:
    """Mean of values weighted by segment length; NaN if there is no length."""
    total = float(lengths.sum())
    return float(np.dot(values.to_numpy(dtype=float), lengths.to_numpy(dtype=float)) / total) if total > 0         else float("nan")


@dataclass(frozen=True)
class AreaSummary:
    """Area figures for one score column, over the scored segments (docs/method.md, "Area summaries").

    Footways, paths and other pedestrian-only segments score 100 on traffic
    stress and infrastructure by definition and make up about half of all
    segments, so they are kept out of the headline and reported separately.
    """
    road_mean: float            # headline: length-weighted mean over roads
    road_km: float
    pedestrian_mean: float      # length-weighted mean over pedestrian-only segments
    pedestrian_km: float

    @property
    def pedestrian_share(self) -> float:
        """Pedestrian-only share of the scored network length (0-1)."""
        total = self.road_km + self.pedestrian_km
        return self.pedestrian_km / total if total > 0 else float("nan")


def pedestrian_mask(segments: pd.DataFrame, pedestrian_only: Iterable[str]) -> pd.Series:
    allowed = list(pedestrian_only)
    return segments["highway"].map(lambda h: is_pedestrian_only(h, allowed)).astype(bool)


def area_summary(segments: pd.DataFrame, column: str, pedestrian_only: Iterable[str]) -> AreaSummary:
    """Length-weighted summary of `column` over scored segments, roads and pedestrian-only apart."""
    seg = segments[segments["scored"]]
    ped = pedestrian_mask(seg, pedestrian_only)
    roads, paths = seg[~ped], seg[ped]
    return AreaSummary(
        road_mean=length_weighted_mean(roads[column], roads["length"]),
        road_km=float(roads["length"].sum()) / 1000,
        pedestrian_mean=length_weighted_mean(paths[column], paths["length"]),
        pedestrian_km=float(paths["length"].sum()) / 1000,
    )
