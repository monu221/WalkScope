"""Helpers for reading OSM tag values as they appear in the segments table.

After OSMnx simplification a tag can be missing (None/NaN), a single string,
or a list of strings (when several OSM ways were merged into one segment).
OSM itself also uses `;` to put several values in one tag.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def as_list(value: Any) -> list[str]:
    """Every value of a tag as a list of stripped strings; [] if missing."""
    if value is None:
        return []
    if isinstance(value, float) and math.isnan(value):
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in items:
        if item is None or (isinstance(item, float) and math.isnan(item)):
            continue
        for part in str(item).split(";"):
            part = part.strip()
            if part:
                out.append(part)
    return out


def road_classes(highway: Any) -> list[str]:
    """Highway values with the `_link` suffix removed (primary_link -> primary)."""
    return [h.removesuffix("_link") for h in as_list(highway)]


def _all_in(value: Any, allowed: Iterable[str]) -> bool:
    values = as_list(value)
    allowed = set(allowed)
    return bool(values) and all(v in allowed for v in values)


def is_routing_only(tags: Mapping[str, Any], pedestrian_only: Iterable[str],
                    rules: Mapping[str, Iterable[str]]) -> bool:
    """True if a segment is kept for routing but not scored (see `routing_only` in the config).

    `tags` maps tag names to the segment's values. A rule matches when the
    segment has the tag and every value is in the rule's list. The `footway`
    rule only applies to pedestrian-only segments, so a plain footway (no
    `footway=` tag) is a path of its own and is scored.
    """
    for key, allowed in rules.items():
        if not _all_in(tags.get(key), allowed):
            continue
        if key == "footway" and not is_pedestrian_only(tags.get("highway"), pedestrian_only):
            continue
        return True
    return False


def is_pedestrian_only(highway: Any, pedestrian_only: Iterable[str]) -> bool:
    """True if every highway value of the segment is a pedestrian-only class.

    A merged segment that is partly a road counts as a road (the stricter case).
    """
    classes = road_classes(highway)
    allowed = set(pedestrian_only)
    return bool(classes) and all(c in allowed for c in classes)
