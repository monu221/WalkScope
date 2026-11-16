"""Combine indicator scores with persona weights, and pick the weakest streets."""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from walkscope.config import INDICATORS, Persona
from walkscope.tags import as_list, road_classes


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
