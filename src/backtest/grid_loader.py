"""
GridLoader — parses config/backtest_grids/<strategy>.yaml into iterable combos.

Schema and tier semantics: see config/backtest_grids/README.md and backtest.md §7.

Tiers:
  1. tier1_entry — cartesian over entry-logic params, capped at max_combos.tier1
                   (random-sampled when product exceeds cap, with fixed seed for reproducibility)
  2. tier2_risk  — one-dimensional sweep: hold all other tier2 keys at anchor,
                   vary one key at a time. Total = sum(len(values)) across keys.
  3. tier3_filters — cartesian, sampled to max_combos.tier3.

Preset resolution:
  - Global presets: regime_preset, session_preset (defined in this module)
  - Local presets: any key ending in `_preset` may have a sibling top-level
    `<key>s` section in the YAML (e.g. `weights_preset` resolves via `weights_presets`)
  - Local presets win over global if both exist
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import yaml


# ---------------------------------------------------------------------------
# Global presets (mirror config/backtest_grids/README.md "Shared presets")
# ---------------------------------------------------------------------------

REGIME_PRESETS: Dict[str, Dict[str, Any]] = {
    "trend_only":     {"only_in_regime": "TREND"},
    "range_only":     {"only_in_regime": "RANGE"},
    "volatile_only":  {"only_in_regime": "VOLATILE"},
    "trend_volatile": {"enabled_regimes": ["TREND", "VOLATILE"]},
    "trend_range":    {"enabled_regimes": ["TREND", "RANGE"]},
    "all":            {"only_in_regime": None},
}

SESSION_PRESETS: Dict[str, List[int]] = {
    "all":       list(range(0, 24)),
    "asia":      list(range(0, 7)),
    "london":    list(range(7, 12)),
    "overlap":   list(range(12, 16)),
    "ny":        list(range(16, 22)),
    "london_ny": list(range(7, 22)),
    # `current` is sentinel — resolved at apply time using anchor.session_hours
}


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@dataclass
class Grid:
    """In-memory representation of one backtest_grids/<strategy>.yaml file."""
    strategy: str
    file: str
    description: str
    notes: str
    anchor: Dict[str, Any]
    tier1_entry: Dict[str, List[Any]]
    tier2_risk: Dict[str, List[Any]]
    tier3_filters: Dict[str, List[Any]]
    max_combos: Dict[str, int]
    local_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    seed: int = 42

    # ------------------------------------------------------------------
    # Tier iterators
    # ------------------------------------------------------------------
    def tier1_combos(self) -> List[Dict[str, Any]]:
        """
        Cartesian over tier1_entry, sampled to max_combos.tier1.
        Always pins the anchor's tier1 values at combo #0 — guarantees a
        known-good baseline is evaluated even when the cap forces sampling.
        """
        cap = self.max_combos.get("tier1", 200)
        combos = self._cartesian_sampled(self.tier1_entry, cap)
        anchor_combo = {k: self.anchor[k] for k in self.tier1_entry if k in self.anchor}
        if anchor_combo:
            combos = [c for c in combos if c != anchor_combo]
            combos.insert(0, anchor_combo)
            if len(combos) > cap:
                combos = combos[:cap]
        return combos

    def tier2_sweeps(self) -> List[Dict[str, Any]]:
        """One-dimensional sweep over tier2_risk: vary one key at a time, others = anchor."""
        out: List[Dict[str, Any]] = []
        for key, values in self.tier2_risk.items():
            anchor_val = self.anchor.get(key)
            for v in values:
                if v == anchor_val:
                    continue  # skip no-op
                out.append({key: v})
        return out

    def tier3_combos(self) -> List[Dict[str, Any]]:
        """Cartesian over tier3_filters, sampled to max_combos.tier3."""
        cap = self.max_combos.get("tier3", 50)
        return self._cartesian_sampled(self.tier3_filters, cap)

    # ------------------------------------------------------------------
    # Preset resolution: turn raw combo dict (e.g. {regime_preset: trend_only})
    # into final config keys (e.g. {only_in_regime: TREND}).
    # ------------------------------------------------------------------
    def resolve(self, combo: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve preset references in `combo` to concrete config keys."""
        out: Dict[str, Any] = {}
        for key, value in combo.items():
            if key.endswith("_preset"):
                resolved = self._resolve_preset(key, value)
                if isinstance(resolved, dict):
                    out.update(resolved)
                else:
                    target_key = key[:-len("_preset")]
                    if target_key.endswith("_hours"):
                        out["session_hours"] = resolved
                    elif target_key == "weights":
                        out["weights"] = resolved
                    elif target_key == "session":
                        out["session_hours"] = resolved
                    else:
                        out[target_key] = resolved
            else:
                out[key] = value
        return out

    def _resolve_preset(self, key: str, name: Any) -> Any:
        # Local presets win
        local_key = key + "s"  # e.g. weights_preset -> weights_presets
        if local_key in self.local_presets and name in self.local_presets[local_key]:
            return self.local_presets[local_key][name]
        if key == "regime_preset":
            return REGIME_PRESETS.get(name, {})
        if key == "session_preset":
            if name == "current":
                return self.anchor.get("session_hours") or self.anchor.get("allowed_hours") or list(range(24))
            return SESSION_PRESETS.get(name, list(range(24)))
        # Unknown preset — return raw name; caller can decide
        return name

    # ------------------------------------------------------------------
    # Build a final strategy config dict by merging anchor + resolved combo
    # ------------------------------------------------------------------
    def build_config(self, *combos: Dict[str, Any]) -> Dict[str, Any]:
        """Merge anchor + N combo dicts (later wins). Resolves presets."""
        out = dict(self.anchor)
        for combo in combos:
            out.update(self.resolve(combo))
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _cartesian_sampled(self, params: Dict[str, List[Any]], cap: int) -> List[Dict[str, Any]]:
        if not params:
            return [{}]
        keys = list(params.keys())
        value_lists = [params[k] for k in keys]
        full = list(itertools.product(*value_lists))
        if len(full) <= cap:
            return [dict(zip(keys, combo)) for combo in full]
        rng = random.Random(self.seed)
        sampled = rng.sample(full, cap)
        return [dict(zip(keys, combo)) for combo in sampled]


# ---------------------------------------------------------------------------
# YAML → Grid
# ---------------------------------------------------------------------------

def load_grid(path: str | Path) -> Grid:
    """Load a backtest_grids/<strategy>.yaml file into a Grid."""
    p = Path(path)
    with p.open("r") as f:
        raw = yaml.safe_load(f)

    local_presets: Dict[str, Dict[str, Any]] = {}
    for key, val in raw.items():
        if key.endswith("_presets") and isinstance(val, dict):
            local_presets[key] = val

    return Grid(
        strategy=raw["strategy"],
        file=raw.get("file", ""),
        description=raw.get("description", ""),
        notes=raw.get("notes", ""),
        anchor=dict(raw.get("anchor") or {}),
        tier1_entry=dict(raw.get("tier1_entry") or {}),
        tier2_risk=dict(raw.get("tier2_risk") or {}),
        tier3_filters=dict(raw.get("tier3_filters") or {}),
        max_combos=dict(raw.get("max_combos") or {"tier1": 200, "tier2": 30, "tier3": 50}),
        local_presets=local_presets,
    )


def load_grid_for(strategy_name: str, grids_dir: str | Path = "config/backtest_grids") -> Grid:
    """Load by strategy key (matches strategies.<key> in config_live_*.yaml)."""
    return load_grid(Path(grids_dir) / f"{strategy_name}.yaml")
