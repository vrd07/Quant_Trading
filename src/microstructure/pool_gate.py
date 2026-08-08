"""Adverse-liquidity proximity test for entry gating.

Pure geometry over the liquidity level set. The fitted conditional-logit model and
its baked coefficients are deliberately NOT used: a veto needs to know where the
pools are, not how likely each is to be hit first. That also drops the history
requirement from the model's 2500 bars (500-bar ATR-percentile feature) to roughly
scan_bars + warmup.

⚠️ Not to be confused with `src/monitoring/liquidity_levels.py`, an unrelated older
module of ICT reference levels.
"""
from __future__ import annotations

import pandas as pd

from .liquidity_levels import DEFAULTS, LevelParams, build_choice_set, build_context


def nearest_adverse_pool_atr(
    bars: pd.DataFrame,
    entry: float,
    side_is_buy: bool,
    atr: float | None,
    params: LevelParams = DEFAULTS,
) -> float | None:
    """ATR-normalised distance from `entry` to the nearest un-swept adverse pool.

    Adverse is BELOW entry for a BUY and ABOVE entry for a SELL — the side price is
    drawn toward to sweep resting stops, which is the side the stop sits on.

    Sides are computed against `entry` rather than against the frame's last close, so
    the answer stays correct if a caller ever passes a fill price that differs from
    the signal bar's close.

    Returns None when the frame is too short, the ATR is unusable, the frame is
    malformed, or no adverse pool exists. Callers MUST treat None as "do not veto".
    """
    if atr is None or not (atr > 0):
        return None

    need = params.scan_bars + 2 * params.pivot_n + params.atr_period
    if bars is None or len(bars) < need:
        return None

    try:
        ctx = build_context(bars, params)
        levels = build_choice_set(ctx, len(bars) - 1, params)
    except Exception:
        # Fail open. A malformed frame must never veto a signal.
        return None

    if not levels:
        return None

    if side_is_buy:
        dists = [entry - lv.price for lv in levels if lv.price < entry]
    else:
        dists = [lv.price - entry for lv in levels if lv.price > entry]

    dists = [d for d in dists if d > 0.0]
    if not dists:
        return None
    return min(dists) / atr
