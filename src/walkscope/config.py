"""Load and validate the YAML config in config/.

Personas come from personas.yaml; thresholds and tag mappings from
indicators.yaml. Both are checked on load so a typo fails loudly instead of
silently skewing scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

INDICATORS = ("amenity", "connectivity", "infrastructure", "traffic_stress", "transit")
INFRASTRUCTURE_PARTS = ("base", "kerb", "surface", "lit")

_TOLERANCE = 1e-9


class ConfigError(ValueError):
    """Raised when a config file is missing values or inconsistent."""


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    weights: dict[str, float]
    infrastructure_mix: dict[str, float]
    impassable_highways: tuple[str, ...] = ()


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def _check_shares(shares: Any, allowed: tuple[str, ...], required: tuple[str, ...], where: str) -> dict[str, float]:
    """Validate a {name: share} mapping: known keys, non-negative numbers, sum 100."""
    if not isinstance(shares, dict) or not shares:
        raise ConfigError(f"{where}: must be a non-empty mapping")
    unknown = set(shares) - set(allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}; allowed {list(allowed)}")
    missing = set(required) - set(shares)
    if missing:
        raise ConfigError(f"{where}: missing keys {sorted(missing)}")
    out: dict[str, float] = {}
    for name, value in shares.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"{where}.{name}: must be a non-negative number, got {value!r}")
        out[name] = float(value)
    total = sum(out.values())
    if abs(total - 100) > _TOLERANCE:
        raise ConfigError(f"{where}: shares must sum to 100, got {total:g}")
    return out


def load_personas(path: str | Path | None = None) -> dict[str, Persona]:
    """Return personas keyed by id, in file order."""
    path = Path(path) if path else CONFIG_DIR / "personas.yaml"
    raw = _read_yaml(path).get("personas")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{path}: needs a non-empty 'personas' mapping")

    personas: dict[str, Persona] = {}
    for key, spec in raw.items():
        where = f"personas.{key}"
        if not isinstance(spec, dict):
            raise ConfigError(f"{where}: must be a mapping")
        weights = _check_shares(spec.get("weights"), INDICATORS, INDICATORS, f"{where}.weights")
        mix = _check_shares(
            spec.get("infrastructure_mix"), INFRASTRUCTURE_PARTS, ("base",), f"{where}.infrastructure_mix"
        )
        impassable = spec.get("impassable_highways", [])
        if not isinstance(impassable, list) or not all(isinstance(h, str) for h in impassable):
            raise ConfigError(f"{where}.impassable_highways: must be a list of highway values")
        personas[key] = Persona(
            key=key,
            label=str(spec.get("label", key)),
            weights=weights,
            infrastructure_mix=mix,
            impassable_highways=tuple(impassable),
        )
    return personas


_REQUIRED_SECTIONS = (
    "area",
    "walking",
    "pedestrian_only_highways",
    "routing_only",
    "amenity",
    "connectivity",
    "infrastructure",
    "traffic_stress",
    "transit",
)


def load_indicator_params(path: str | Path | None = None) -> dict[str, Any]:
    """Return the indicator parameters after basic consistency checks."""
    path = Path(path) if path else CONFIG_DIR / "indicators.yaml"
    params = _read_yaml(path)

    missing = [s for s in _REQUIRED_SECTIONS if s not in params]
    if missing:
        raise ConfigError(f"{path}: missing sections {missing}")

    conn = params["connectivity"]
    if not conn["density_zero"] < conn["density_full"]:
        raise ConfigError("connectivity: density_zero must be below density_full")

    transit = params["transit"]
    if not transit["full_score_m"] < transit["zero_score_m"]:
        raise ConfigError("transit: full_score_m must be below zero_score_m")

    if not params["amenity"].get("categories"):
        raise ConfigError("amenity: needs at least one category")

    _check_shares(params["infrastructure"]["base_mix"], ("sidewalk", "crossing"), ("sidewalk", "crossing"),
                  "infrastructure.base_mix")

    curve = params["traffic_stress"]["speed_curve"]
    speeds = [p[0] for p in curve]
    if len(curve) < 2 or any(len(p) != 2 for p in curve) or speeds != sorted(set(speeds)):
        raise ConfigError("traffic_stress.speed_curve: needs 2+ [speed, score] points with rising speeds")
    if "other" not in params["traffic_stress"]["default_speed_kmh"]:
        raise ConfigError("traffic_stress.default_speed_kmh: needs an 'other' fallback")

    return params
