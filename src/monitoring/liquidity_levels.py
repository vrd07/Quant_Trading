"""
Liquidity reference levels (ICT / SMC convention) + pure stop adjustment.

The institutional 'liquidity sweep' setup targets clusters of resting stop
orders — these sit at obvious chart features rather than at the centroid of
yesterday's volume. The canonical levels are:

  PDH / PDL          — prior UTC day's high / low (buyside / sellside liquidity)
  Asia H / Asia L    — high / low of the UTC 00:00–07:00 window (commonly
                        swept in the London open)
  Recent swing H/L   — the most recent confirmed higher-high / lower-low,
                        relative liquidity for the current intraday move

This module emits *levels* only. A separate backtest validates whether
'price wicks past level then closes back inside' actually reverses; if it
does, the levels earn their place in the live monitor.

Pure pandas / numpy. No new dependencies. Stateless.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


def _ensure_ts(bars: pd.DataFrame) -> pd.Series:
    """Return bar timestamps as a tz-aware UTC datetime Series, regardless of
    whether bars carries them in `timestamp` column (DataEngine.get_bars()
    convention) or in a DatetimeIndex (historical CSV convention)."""
    if "timestamp" in bars.columns:
        ts = pd.to_datetime(bars["timestamp"], utc=True)
    else:
        ts = pd.to_datetime(bars.index, utc=True)
        ts = pd.Series(ts, index=bars.index)
    return ts


def compute_liquidity_levels(
    bars: pd.DataFrame,
    asia_start_utc_hour: int = 0,
    asia_end_utc_hour: int = 7,
    swing_lookback: int = 60,
    equal_tolerance_atr: float = 0.10,
) -> Optional[Dict[str, Any]]:
    """Compute liquidity reference levels from an OHLCV bar series.

    Returns a dict:
        {
          "pdh":       prior UTC day's high,
          "pdl":       prior UTC day's low,
          "asia_h":    today's UTC 00–07 high (if today has any bars in window),
          "asia_l":    today's UTC 00–07 low  (",                              ),
          "swing_h":   most recent N-bar swing high (within `swing_lookback`),
          "swing_l":   most recent N-bar swing low,
          "equal_highs": [list of prices where ≥ 2 swings cluster within
                         equal_tolerance_atr × ATR],
          "equal_lows":  [same for lows],
        }
    or None if there isn't enough data (need ≥ 2 UTC days).
    """
    if bars is None or len(bars) < 50:
        return None
    for col in ("high", "low"):
        if col not in bars.columns:
            return None

    ts = _ensure_ts(bars)
    if ts.isna().any():
        return None
    dates = ts.dt.normalize()
    unique_days = sorted(pd.Series(dates).dropna().unique())
    if len(unique_days) < 2:
        return None

    today = unique_days[-1]
    prior = unique_days[-2]

    prior_mask = (dates == prior).values
    today_mask = (dates == today).values

    prior_bars = bars[prior_mask]
    today_bars = bars[today_mask]
    if len(prior_bars) == 0 or len(today_bars) == 0:
        return None

    pdh = float(prior_bars["high"].max())
    pdl = float(prior_bars["low"].min())

    # Asia session: UTC hours [asia_start, asia_end). On forex/gold/crypto
    # this is overnight relative to NY and ends shortly after London opens —
    # the classical "Asian range" the morning is biased to sweep.
    hours = ts.dt.hour
    asia_today_mask = today_mask & (hours.values >= asia_start_utc_hour) & (hours.values < asia_end_utc_hour)
    if asia_today_mask.any():
        asia_h = float(bars.loc[asia_today_mask, "high"].max())
        asia_l = float(bars.loc[asia_today_mask, "low"].min())
    else:
        asia_h = None
        asia_l = None

    # Most recent swing high/low within the lookback window.
    # A "swing high at bar i" = high[i] > high[i-k:i] and high[i] > high[i+1:i+k+1]
    # We use a 3-bar pivot (k=3) which is the lightest filter that still
    # excludes single-bar noise.
    k = 3
    recent = bars.iloc[-swing_lookback:] if len(bars) > swing_lookback else bars
    highs = recent["high"].values
    lows = recent["low"].values

    swing_highs: list = []
    swing_lows: list = []
    for i in range(k, len(recent) - k):
        if highs[i] >= highs[i - k:i].max() and highs[i] >= highs[i + 1:i + k + 1].max():
            swing_highs.append(float(highs[i]))
        if lows[i] <= lows[i - k:i].min() and lows[i] <= lows[i + 1:i + k + 1].min():
            swing_lows.append(float(lows[i]))

    swing_h = max(swing_highs) if swing_highs else None
    swing_l = min(swing_lows) if swing_lows else None

    # Equal highs / equal lows: cluster swing highs (or lows) within a
    # tolerance proportional to ATR. Each cluster of ≥ 2 swings becomes one
    # 'equal' level reported at the mean of its members.
    atr_proxy = float((recent["high"] - recent["low"]).tail(14).mean()) or 1e-9
    tol = equal_tolerance_atr * atr_proxy

    def _cluster(points: list) -> list:
        if not points:
            return []
        pts = sorted(points)
        clusters: list = []
        current = [pts[0]]
        for p in pts[1:]:
            if abs(p - current[-1]) <= tol:
                current.append(p)
            else:
                if len(current) >= 2:
                    clusters.append(sum(current) / len(current))
                current = [p]
        if len(current) >= 2:
            clusters.append(sum(current) / len(current))
        return clusters

    equal_highs = _cluster(swing_highs)
    equal_lows = _cluster(swing_lows)

    return {
        "pdh": pdh,
        "pdl": pdl,
        "asia_h": asia_h,
        "asia_l": asia_l,
        "swing_h": swing_h,
        "swing_l": swing_l,
        "equal_highs": equal_highs,
        "equal_lows": equal_lows,
    }


def detect_sweeps(
    today_bars: pd.DataFrame,
    levels: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """For each level in `levels`, classify whether today has swept it.

    A sweep = a bar with high > level (or low < level for a 'low' level) AND
    a close back on the original side. The wick is the stop-run, the close
    is the failed continuation.

    Returns: {level_name: {"swept": bool, "swept_bar_idx": int|None, "side": str}}
    """
    out: Dict[str, Dict[str, Any]] = {}
    if today_bars is None or len(today_bars) == 0:
        return {k: {"swept": False, "swept_bar_idx": None, "side": ""} for k in levels}

    highs = today_bars["high"].values
    lows = today_bars["low"].values
    closes = today_bars["close"].values

    for name, lvl in levels.items():
        if lvl is None or not np.isfinite(lvl):
            out[name] = {"swept": False, "swept_bar_idx": None, "side": ""}
            continue
        # "high" levels (pdh, asia_h, swing_h): sweep = high > lvl AND close < lvl
        # "low"  levels (pdl, asia_l, swing_l): sweep = low  < lvl AND close > lvl
        is_high_level = name.endswith("h") or "high" in name
        if is_high_level:
            mask = (highs > lvl) & (closes < lvl)
            side = "above"
        else:
            mask = (lows < lvl) & (closes > lvl)
            side = "below"
        idx = int(np.argmax(mask)) if mask.any() else None
        out[name] = {"swept": bool(mask.any()), "swept_bar_idx": idx, "side": side}

    return out


# ── pure stop adjustment ───────────────────────────────────────────────────
# Carmack lens: a pure function with two non-negotiable invariants. No I/O,
# no globals, no logging — the caller logs at the mutation site so the change
# is visible in the same place SL/TP get assigned to the Signal.

def adjust_stops_for_liquidity(
    entry: Decimal,
    sl: Decimal,
    tp: Decimal,
    side: str,  # "BUY" or "SELL" — matches OrderSide enum string values
    levels: Dict[str, Optional[Decimal]],
    buffer: Decimal,
) -> Tuple[Decimal, Decimal, List[str]]:
    """Adjust SL/TP using liquidity reference levels.

    Two invariants — both enforced by the if-clauses below, **not** by trust:
      1. TP is NEVER widened (only tightened or unchanged).
      2. SL is NEVER tightened (only widened or unchanged).

    Both invariants exist so this function can only make a trade *safer* —
    earlier profit-taking or further-away stops. The expected-value cost is
    bounded: at worst the trade behaves like the pre-adjustment plan.

    Rules:
      For a BUY (long):
        TP rule — find the LOWEST liquidity level that is above entry but
                  below the current TP. If found, tighten TP to (level - buffer):
                  institutions target that level; better to exit just before
                  rather than have price reverse out of it.
        SL rule — find the HIGHEST liquidity level that is below entry but
                  above the current SL. If found, push SL to (level - buffer):
                  the level acts as a magnet for stop-runs; placing our SL
                  inside the sweep zone gets us run before the real reversal.
      For a SELL: mirror image.

    `levels` keys can be any subset of {pdh, pdl, asia_h, asia_l, swing_h,
    swing_l, ...}. Values may be None (missing data) — these are skipped.

    Returns:
        (new_sl, new_tp, reasons) — reasons is a list of human-readable
        strings describing each adjustment, for the caller to log.
    """
    reasons: List[str] = []

    # Filter levels: drop None, drop non-finite.
    clean: Dict[str, Decimal] = {}
    for name, val in (levels or {}).items():
        if val is None:
            continue
        try:
            d = Decimal(str(val))
        except Exception:
            continue
        if d <= 0:
            continue
        clean[name] = d

    if not clean:
        return sl, tp, reasons

    is_buy = (side == "BUY") or (side == "buy")

    if is_buy:
        # ── TP tightening: lowest level strictly between entry and current TP
        candidates_above = [
            (name, lvl) for name, lvl in clean.items()
            if lvl > entry and lvl < tp
        ]
        if candidates_above:
            name, lvl = min(candidates_above, key=lambda kv: kv[1])
            new_tp = lvl - buffer
            if new_tp > entry and new_tp < tp:           # invariant: never widen
                reasons.append(f"TP tightened to {name}-buffer ({tp} → {new_tp})")
                tp = new_tp

        # ── SL widening: highest level strictly between current SL and entry
        candidates_below = [
            (name, lvl) for name, lvl in clean.items()
            if lvl > sl and lvl < entry
        ]
        if candidates_below:
            name, lvl = max(candidates_below, key=lambda kv: kv[1])
            new_sl = lvl - buffer
            if new_sl < sl:                              # invariant: never tighten
                reasons.append(f"SL widened past {name} ({sl} → {new_sl})")
                sl = new_sl

    else:  # SELL
        # ── TP tightening: highest level strictly between current TP and entry
        candidates_below = [
            (name, lvl) for name, lvl in clean.items()
            if lvl < entry and lvl > tp
        ]
        if candidates_below:
            name, lvl = max(candidates_below, key=lambda kv: kv[1])
            new_tp = lvl + buffer
            if new_tp < entry and new_tp > tp:           # invariant: never widen
                reasons.append(f"TP tightened to {name}+buffer ({tp} → {new_tp})")
                tp = new_tp

        # ── SL widening: lowest level strictly between entry and current SL
        candidates_above = [
            (name, lvl) for name, lvl in clean.items()
            if lvl > entry and lvl < sl
        ]
        if candidates_above:
            name, lvl = min(candidates_above, key=lambda kv: kv[1])
            new_sl = lvl + buffer
            if new_sl > sl:                              # invariant: never tighten
                reasons.append(f"SL widened past {name} ({sl} → {new_sl})")
                sl = new_sl

    return sl, tp, reasons
