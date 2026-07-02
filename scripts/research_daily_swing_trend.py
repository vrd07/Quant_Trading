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


if __name__ == "__main__":
    daily = load_daily_bars()
    is_slice, oos_slice = split_is_oos(daily)
    print(f"Total daily bars: {len(daily)} ({daily.index.min()} -> {daily.index.max()})")
    print(f"IN-SAMPLE:  {len(is_slice)} bars ({is_slice.index.min()} -> {is_slice.index.max()})")
    print(f"OUT-OF-SAMPLE: {len(oos_slice)} bars ({oos_slice.index.min()} -> {oos_slice.index.max()})")
