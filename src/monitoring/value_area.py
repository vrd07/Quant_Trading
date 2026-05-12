"""
Value Area / Market Profile primitives for the live monitor.

Given a session's bars (with volume), compute:
  - POC (Point of Control)   = price level with the most volume
  - VAH / VAL                = upper / lower edge of the price range that
                                contains `value_pct` of total volume
                                (canonical Market Profile uses 70 %)

Then, for the current session, classify where price sits relative to the
prior session's VA and count re-entries from outside back inside — the
inputs the "80 % rule" needs (Dalton, "Mind Over Markets").

Notes:
  - Bars without real volume (broker reports 0) fall back to TPO mode:
    each bar's typical price gets one tick of "volume" — equivalent to
    Steidlmayer's original Time-Price-Opportunity profile.
  - Volume is distributed only to the bar's typical price bin; finer
    distribution across high-low is possible but rarely improves the VA
    location materially on intraday data.

Pure pandas/numpy. No new dependencies.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def compute_value_area(
    bars: pd.DataFrame,
    value_pct: float = 0.70,
    n_bins: int = 50,
) -> Optional[Dict[str, Any]]:
    """Compute VAH / VAL / POC from a session's OHLCV bars.

    Returns a dict {vah, val, poc, total_volume, n_bins} or None if the input
    is too small / has no price spread.
    """
    if bars is None or len(bars) < 5:
        return None
    for col in ("high", "low", "close"):
        if col not in bars.columns:
            return None

    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    close = bars["close"].astype(float)

    price_min = float(low.min())
    price_max = float(high.max())
    if not (np.isfinite(price_min) and np.isfinite(price_max)) or price_max <= price_min:
        return None

    typical = (high + low + close) / 3.0
    # TPO fallback when broker volume is missing or all zero.
    if "volume" in bars.columns and float(bars["volume"].sum()) > 0:
        volume = bars["volume"].astype(float).values
    else:
        volume = np.ones(len(bars), dtype=float)

    edges = np.linspace(price_min, price_max, n_bins + 1)
    # np.digitize with edges[1:-1] gives bin indices 0..n_bins-1
    bin_idx = np.clip(np.digitize(typical.values, edges[1:-1]), 0, n_bins - 1)

    vol_by_bin = np.zeros(n_bins, dtype=float)
    # np.add.at handles duplicate indices correctly (sums into the same bin).
    np.add.at(vol_by_bin, bin_idx, volume)

    total = float(vol_by_bin.sum())
    if total <= 0.0:
        return None

    # POC = highest-volume bin.
    poc_bin = int(np.argmax(vol_by_bin))
    target = value_pct * total
    cum = float(vol_by_bin[poc_bin])

    low_b = high_b = poc_bin
    # Symmetric expansion from POC: at each step, advance toward whichever side
    # has more volume in its next bin. This is the standard MP construction.
    while cum < target and (low_b > 0 or high_b < n_bins - 1):
        next_low = float(vol_by_bin[low_b - 1]) if low_b > 0 else -1.0
        next_high = float(vol_by_bin[high_b + 1]) if high_b < n_bins - 1 else -1.0
        if next_low < 0 and next_high < 0:
            break
        if next_high >= next_low:
            high_b += 1
            cum += next_high
        else:
            low_b -= 1
            cum += next_low

    poc_price = float((edges[poc_bin] + edges[poc_bin + 1]) / 2.0)
    val_price = float(edges[low_b])
    vah_price = float(edges[high_b + 1])

    return {
        "vah": vah_price,
        "val": val_price,
        "poc": poc_price,
        "total_volume": total,
        "n_bins": n_bins,
        "value_pct_actual": cum / total,
    }


def value_area_state(
    today_bars: pd.DataFrame, vah: float, val: float
) -> Dict[str, Any]:
    """Classify where the current session sits vs the prior session's VA.

    Returns:
        {
          "state":         "INSIDE" | "ABOVE" | "BELOW",
          "open_inside":   bool — did this session open inside the prior VA?
          "reentries":     int — count of OUTSIDE→INSIDE transitions so far,
          "two_touch_rule": bool — open_outside AND reentries >= 2 (Dalton 80 %),
        }
    """
    if today_bars is None or len(today_bars) == 0 or not (vah > val):
        return {"state": "INSIDE", "open_inside": True, "reentries": 0, "two_touch_rule": False}

    open_price = float(today_bars["open"].iloc[0])
    last_price = float(today_bars["close"].iloc[-1])
    open_inside = (val <= open_price <= vah)

    # We classify each bar's close inside/outside the VA. Closes (not highs/lows)
    # are the standard MP convention — a wick doesn't constitute "trading inside".
    inside = ((today_bars["close"] >= val) & (today_bars["close"] <= vah)).values

    reentries = 0
    prev_inside = open_inside
    for is_in in inside:
        if is_in and not prev_inside:
            reentries += 1
        prev_inside = bool(is_in)

    if last_price > vah:
        state = "ABOVE"
    elif last_price < val:
        state = "BELOW"
    else:
        state = "INSIDE"

    return {
        "state": state,
        "open_inside": open_inside,
        "reentries": reentries,
        "two_touch_rule": (not open_inside) and reentries >= 2,
    }
