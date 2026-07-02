#!/usr/bin/env python3
"""
Daily-bar Donchian breakout + ATR-chandelier trend-follower — research
prototype (XAUUSD, candidate strategy #14).

Design spec: docs/superpowers/specs/2026-07-01-daily-swing-trend-design.md

Every gold-specific strategy already live (kalman_regime, squeeze_breakout,
stoch_pullback) trades 15m bars and holds minutes-to-hours. This tests a
DAILY-bar trend-follower: Donchian(N) breakout entry (with optional
confirmation filters to fight the false-breakout/whipsaw failure mode common
to breakout trend systems), ATR-chandelier trailing exit (no fixed TP,
winners ride until the trail catches them or a fresh opposite breakout
fires). Two-stage walk-forward: Stage 1 picks N / atr_mult / confirm_bars /
atr_expansion-required on the in-sample slice; Stage 2 layers an HTF-EMA
trend-alignment filter and a min-breakout-penetration filter (both already
validated for the same failure mode on squeeze_breakout) on top of the
Stage-1 winner; a final OOS run and a cost-robustness re-run decide ship/no-ship
against this repo's 4-part gate.

Writes: reports/daily_swing_trend_research.md
"""

import sys
import logging
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators
from scripts.backtest_kalman_2026_fixed import stats, max_drawdown

DATA_CSV = PROJECT_ROOT / "data/historical/XAUUSD_5m_real.csv"
REPORT = PROJECT_ROOT / "reports/daily_swing_trend_research.md"

VALUE_PER_LOT = 100.0   # XAUUSD: $100 per 1.0 price move per 1.0 lot
LOT = 0.02              # XAUUSD min_lot — what live actually floors to
COST = 0.20             # per-side spread+slippage in price points
CAPITAL = 5_000.0       # gate criteria evaluated at $5k live sizing

ATR_PERIOD = 20
ATR_EXPANSION_RATIO = 1.05   # fixed at squeeze_breakout's validated value
HTF_EMA_PERIOD = 200         # daily-timeframe "slow trend" analogue
MIN_PENETRATION_ATR = 0.1    # fixed at squeeze_breakout's validated value

# IS/OOS split: IS covers as much pre-2024 history as Dukascopy actually
# served (auto-detected, not assumed); OOS is 2024 through the present,
# deliberately including gold's most extreme recent bull run so a
# trend-follower's most favourable regime is the UNTOUCHED test, not the
# tuning set.
OOS_START = "2024-01-01"


def load_daily_bars(start=None, end=None) -> pd.DataFrame:
    """Load XAUUSD daily bars resampled from the canonical 5m CSV.

    Uses label="left", closed="left" on the "1D" rule — the exact convention
    src/data/data_engine.py uses live (data_engine.py:348-359) — so research
    levels match what the live strategy would actually see.
    """
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    daily = (df.resample("1D", label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min",
                   "close": "last", "volume": "sum"})
             .dropna(subset=["open", "high", "low", "close"]))
    if start is not None:
        daily = daily[daily.index >= pd.Timestamp(start, tz=daily.index.tz)]
    if end is not None:
        daily = daily[daily.index < pd.Timestamp(end, tz=daily.index.tz)]
    return daily


def split_is_oos(daily: pd.DataFrame) -> tuple:
    """Split into (in_sample, out_of_sample) on OOS_START. IS start is
    whatever the data actually has (auto-detected), not assumed."""
    is_slice = daily[daily.index < pd.Timestamp(OOS_START, tz=daily.index.tz)]
    oos_slice = daily[daily.index >= pd.Timestamp(OOS_START, tz=daily.index.tz)]
    return is_slice, oos_slice


def daily_swing_trend_signals(bars: pd.DataFrame, *, donch_n: int, confirm_bars: int,
                               atr_expansion_required: bool,
                               htf_ema_period: int = 0,
                               min_penetration_atr: float = 0.0,
                               atr_period: int = ATR_PERIOD,
                               atr_expansion_ratio: float = ATR_EXPANSION_RATIO) -> pd.DataFrame:
    """Vectorised Donchian(N) breakout signals with optional confirmation filters.

    donch_n: Donchian channel length, computed EXCLUDING the current bar
      (.shift(1)) so a bar can't trivially "break" its own high/low.
    confirm_bars: require the close to remain beyond the channel for this
      many consecutive bars before firing (1 = fire on the first breakout
      close — the un-confirmed baseline).
    atr_expansion_required: require ATR(atr_period) >= atr_expansion_ratio *
      ATR(atr_period) one bar ago on the firing bar (mirrors squeeze_breakout's
      validated fakeout filter).
    htf_ema_period: if > 0, only take breaks aligned with this EMA of daily
      closes (BUY above / SELL below); 0 disables the filter.
    min_penetration_atr: reject breaks that clear the channel by less than
      this many ATRs; 0 disables the filter.

    NOTE: a genuine breakout can hold `confirmed_buy`/`confirmed_sell` True
    for many consecutive days (as long as price stays beyond the channel) —
    this is intentional, not a bug. The simulator (Task 4) only opens a new
    position when flat, so a multi-day-true signal does not open duplicate
    positions; it does let the system re-enter promptly (subject to
    `cooldown_bars`) if a chandelier stop-out happens while the breakout is
    still structurally valid.
    """
    close, high, low = bars["close"], bars["high"], bars["low"]
    atr = Indicators.atr(bars, period=atr_period)

    donch_hi = high.rolling(donch_n).max().shift(1)
    donch_lo = low.rolling(donch_n).min().shift(1)

    raw_buy = close > donch_hi
    raw_sell = close < donch_lo

    confirmed_buy = raw_buy.rolling(confirm_bars).sum() >= confirm_bars
    confirmed_sell = raw_sell.rolling(confirm_bars).sum() >= confirm_bars

    if atr_expansion_required:
        atr_expand = atr >= atr_expansion_ratio * atr.shift(1)
    else:
        atr_expand = pd.Series(True, index=close.index)

    if htf_ema_period and htf_ema_period > 0:
        htf = close.ewm(span=htf_ema_period, adjust=False).mean()
        up_ok, dn_ok = close > htf, close < htf
    else:
        up_ok = dn_ok = pd.Series(True, index=close.index)

    if min_penetration_atr and min_penetration_atr > 0:
        deep_hi = close > donch_hi + min_penetration_atr * atr
        deep_lo = close < donch_lo - min_penetration_atr * atr
    else:
        deep_hi = deep_lo = pd.Series(True, index=close.index)

    buy = confirmed_buy & atr_expand & up_ok & deep_hi
    sell = confirmed_sell & atr_expand & dn_ok & deep_lo

    rows = []
    for i in range(len(bars)):
        b, s = bool(buy.iloc[i]), bool(sell.iloc[i])
        if not (b or s):
            continue
        rows.append({
            "bar_idx": i, "signal_ts": bars.index[i],
            "side": "BUY" if b else "SELL",
            "atr_at_entry": float(atr.iloc[i]),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    daily = load_daily_bars()
    is_slice, oos_slice = split_is_oos(daily)
    print(f"Total daily bars: {len(daily)} ({daily.index.min()} -> {daily.index.max()})")
    print(f"IN-SAMPLE:  {len(is_slice)} bars ({is_slice.index.min()} -> {is_slice.index.max()})")
    print(f"OUT-OF-SAMPLE: {len(oos_slice)} bars ({oos_slice.index.min()} -> {oos_slice.index.max()})")
