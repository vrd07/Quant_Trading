"""Bounded take-profit snap onto un-swept liquidity pools.

Pure geometry. The fitted conditional-logit model is deliberately NOT used — pools
inside a +/-25% band all sit at similar distances, and the model's within-snapshot
ranking is monotone in distance, so a probability filter could not change which pool
is chosen. See the "Honest scope" section of
docs/superpowers/specs/2026-08-07-liquidity-tp-overlay-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .liquidity_levels import DEFAULTS, LevelParams, build_choice_set, build_context

# Fixed, NOT a tunable. build_context computes ATR and EMA recursively from index 0
# and ranks ATR over a 500-bar window, so the pool set depends on how many bars are
# supplied. A different count in live than in backtest makes the two silently disagree.
HISTORY_BARS = 2500


@dataclass(frozen=True)
class SnapConfig:
    band_pct: float = 0.25
    buffer_atr: float = 0.05
    min_rr: float = 1.2
    min_stops_distance: float = 0.0


def _pool_prices(bars: pd.DataFrame, params: LevelParams) -> list[float]:
    """Prices of the current choice set. Separated so tests can substitute it."""
    ctx = build_context(bars, params)
    return [lv.price for lv in build_choice_set(ctx, len(bars) - 1, params)]


def snap_take_profit(
    entry: float,
    side_is_buy: bool,
    stop_loss: float,
    take_profit: float,
    bars: pd.DataFrame,
    atr: float | None,
    cfg: SnapConfig = SnapConfig(),
    params: LevelParams = DEFAULTS,
) -> tuple[float, str]:
    """Move the target to just short of a pool inside the band, or leave it alone.

    Returns (take_profit, reason). `reason` is one of: snapped, no_pool, below_min_rr,
    broker_min, no_bars, error.

    Every non-"snapped" outcome returns the ORIGINAL take_profit unchanged.
    """
    if atr is None or not (atr > 0) or not take_profit or not entry:
        return take_profit, "no_bars"

    d = abs(take_profit - entry)
    risk = abs(entry - stop_loss)
    if d <= 0 or risk <= 0:
        return take_profit, "no_bars"

    try:
        prices = _pool_prices(bars, params)
    except Exception:
        return take_profit, "error"

    lo, hi = d * (1.0 - cfg.band_pct), d * (1.0 + cfg.band_pct)
    if side_is_buy:
        cand = [p for p in prices if p > entry and lo <= (p - entry) <= hi]
    else:
        cand = [p for p in prices if p < entry and lo <= (entry - p) <= hi]
    if not cand:
        return take_profit, "no_pool"

    # The first wall price meets is the one that can stop the move.
    pool = min(cand) if side_is_buy else max(cand)
    buf = cfg.buffer_atr * atr
    new_tp = pool - buf if side_is_buy else pool + buf

    new_dist = abs(new_tp - entry)
    if new_dist <= 0 or (new_tp > entry) != side_is_buy:
        return take_profit, "no_pool"
    if cfg.min_stops_distance > 0 and new_dist < cfg.min_stops_distance:
        return take_profit, "broker_min"
    if new_dist / risk < cfg.min_rr:
        return take_profit, "below_min_rr"

    return new_tp, "snapped"
