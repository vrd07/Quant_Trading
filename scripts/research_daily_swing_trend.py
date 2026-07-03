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


def simulate_chandelier(bars: pd.DataFrame, sig_df: pd.DataFrame, *,
                         atr_mult: float, cooldown_bars: int,
                         lot: float = LOT, cost: float = COST,
                         value_per_lot: float = VALUE_PER_LOT) -> pd.DataFrame:
    """Daily chandelier-trail simulation. One position at a time (this
    strategy commits one XAUUSD daily slot, matching the live design's
    single-symbol-slot scope). Entry fills at the OPEN of the bar after the
    signal (signal confirmed on close of day t -> fill at open of day t+1).

    Exit = ATR-chandelier trail only, no take-profit:
      stop = highest-high-since-entry - atr_mult*atr_at_entry   (long)
      stop = lowest-low-since-entry   + atr_mult*atr_at_entry   (short)
    atr_at_entry is FROZEN at entry (matches this repo's existing
    TrailingStopManager convention of computing the initial ATR distance
    once and never re-deriving it mid-trade — see design spec's Risk-Engine
    Wiring section). The trail only ever ratchets favourably.

    Gap-aware: if the day's OPEN is already through the trail, fill at that
    (worse) open price; otherwise assume the trail is hit intrabar at the
    trail price itself. Mirrors backtest_kalman_2026_fixed.simulate's
    gap-through-stop convention.
    """
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    l = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    ts = bars.index
    n = len(bars)

    by_entry = defaultdict(list)
    for _, s in sig_df.iterrows():
        eb = int(s["bar_idx"]) + 1
        if eb < n:
            by_entry[eb].append(s)

    pos = None
    trades = []
    last_exit_bar = -10 ** 9

    def record(entry_ts, exit_ts, side, entry, fill, reason, bars_held):
        sign = 1.0 if side == 1 else -1.0
        pnl = (fill - entry) * lot * value_per_lot * sign
        trades.append({
            "entry_ts": entry_ts, "exit_ts": exit_ts,
            "side": "buy" if side == 1 else "sell",
            "entry": entry, "exit": fill, "exit_reason": reason,
            "bars_held": bars_held, "pnl": pnl,
        })

    for i in range(n):
        # --- Manage the open position first ---
        if pos is not None and i > pos["entry_bar"]:
            if pos["side"] == 1:
                pos["extreme"] = max(pos["extreme"], h[i])
                trail = pos["extreme"] - pos["atr_dist"]
                if o[i] <= trail:
                    fill, reason = o[i] - cost, "trail_gap"
                elif l[i] <= trail:
                    fill, reason = trail - cost, "trail"
                else:
                    fill = None
            else:
                pos["extreme"] = min(pos["extreme"], l[i])
                trail = pos["extreme"] + pos["atr_dist"]
                if o[i] >= trail:
                    fill, reason = o[i] + cost, "trail_gap"
                elif h[i] >= trail:
                    fill, reason = trail + cost, "trail"
                else:
                    fill = None

            if fill is not None:
                record(pos["entry_ts"], ts[i], pos["side"], pos["entry"], fill,
                       reason, i - pos["entry_bar"])
                last_exit_bar = i
                pos = None

        # --- Consider a new entry only when flat, past the cooldown ---
        if pos is None and (i - last_exit_bar) >= cooldown_bars:
            for s in by_entry.get(i, []):
                side = 1 if str(s["side"]).upper() == "BUY" else -1
                entry = o[i] + cost if side == 1 else o[i] - cost
                atr_dist = atr_mult * float(s["atr_at_entry"])
                extreme = h[i] if side == 1 else l[i]
                trail0 = extreme - atr_dist if side == 1 else extreme + atr_dist
                hit = (l[i] <= trail0) if side == 1 else (h[i] >= trail0)
                if hit:
                    # Entry-day range already crashes through the initial trail.
                    fill = trail0 - cost if side == 1 else trail0 + cost
                    record(ts[i], ts[i], side, entry, fill, "trail_same_day", 0)
                    last_exit_bar = i
                else:
                    pos = {"side": side, "entry": entry, "entry_bar": i,
                           "entry_ts": ts[i], "atr_dist": atr_dist, "extreme": extreme}
                break  # one position at a time; ignore a same-day 2nd signal

    if pos is not None:
        record(pos["entry_ts"], ts[-1], pos["side"], pos["entry"], c[-1],
               "end_of_data", n - 1 - pos["entry_bar"])

    return pd.DataFrame(trades)


STAGE1_GRID = {
    "donch_n": [20, 40, 55],
    "atr_mult": [2.0, 3.0, 4.0],
    "confirm_bars": [1, 2, 3],
    "atr_expansion_required": [False, True],
}


def run_stage1(is_bars: pd.DataFrame) -> dict:
    """Grid-search Stage-1 params on the in-sample slice only. Returns
    {(donch_n, atr_mult, confirm_bars, atr_expansion_required): (stats, (dd, dd_pct))}.
    """
    results = {}
    for n in STAGE1_GRID["donch_n"]:
        for mult in STAGE1_GRID["atr_mult"]:
            for cb in STAGE1_GRID["confirm_bars"]:
                for expand in STAGE1_GRID["atr_expansion_required"]:
                    sig = daily_swing_trend_signals(
                        is_bars, donch_n=n, confirm_bars=cb,
                        atr_expansion_required=expand,
                    )
                    trades = simulate_chandelier(
                        is_bars, sig, atr_mult=mult, cooldown_bars=max(cb, 1) * 2,
                    )
                    key = (n, mult, cb, expand)
                    results[key] = (stats(trades), max_drawdown(trades, CAPITAL))
    return results


def pick_stage1_winner(results: dict, min_trades: int = 20) -> tuple:
    """Most-robust candidate: among cells with PF > 1.10 and N >= min_trades,
    the one with the highest PF (same selection discipline
    research_squeeze_breakout.py used)."""
    candidates = [(k, s) for k, (s, _) in results.items()
                  if s["pf"] > 1.10 and s["n"] >= min_trades]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[1]["pf"])[0]


def run_stage2(is_bars: pd.DataFrame, stage1_winner: tuple) -> dict:
    """Layer HTF-alignment and min-penetration filters on top of the Stage-1
    winner, on/off only (both fixed at squeeze_breakout's validated values —
    see Global Constraints). Returns {(htf_on, pen_on): (stats, (dd, dd_pct))}.
    """
    n, mult, cb, expand = stage1_winner
    results = {}
    for htf_on in (False, True):
        for pen_on in (False, True):
            sig = daily_swing_trend_signals(
                is_bars, donch_n=n, confirm_bars=cb,
                atr_expansion_required=expand,
                htf_ema_period=HTF_EMA_PERIOD if htf_on else 0,
                min_penetration_atr=MIN_PENETRATION_ATR if pen_on else 0.0,
            )
            trades = simulate_chandelier(is_bars, sig, atr_mult=mult,
                                          cooldown_bars=max(cb, 1) * 2)
            results[(htf_on, pen_on)] = (stats(trades), max_drawdown(trades, CAPITAL))
    return results


def pick_final_params(stage1_winner: tuple, stage2_results: dict) -> dict:
    """Pick the Stage-2 cell with the highest PF (ties broken toward fewer
    active filters — simpler is preferred when performance is equal)."""
    n, mult, cb, expand = stage1_winner
    ranked = sorted(stage2_results.items(),
                     key=lambda kv: (kv[1][0]["pf"], not kv[0][0], not kv[0][1]),
                     reverse=True)
    (htf_on, pen_on), _ = ranked[0]
    return {
        "donch_n": n, "atr_mult": mult, "confirm_bars": cb,
        "atr_expansion_required": expand,
        "htf_ema_period": HTF_EMA_PERIOD if htf_on else 0,
        "min_penetration_atr": MIN_PENETRATION_ATR if pen_on else 0.0,
        "cooldown_bars": max(cb, 1) * 2,
    }


def evaluate_final(bars: pd.DataFrame, params: dict, cost: float = COST) -> tuple:
    """Run the fully-resolved parameter set on a given bar slice. Used for
    both the untouched OOS validation and the cost-robustness re-run (with
    a widened `cost`)."""
    sig = daily_swing_trend_signals(
        bars, donch_n=params["donch_n"], confirm_bars=params["confirm_bars"],
        atr_expansion_required=params["atr_expansion_required"],
        htf_ema_period=params["htf_ema_period"],
        min_penetration_atr=params["min_penetration_atr"],
    )
    trades = simulate_chandelier(bars, sig, atr_mult=params["atr_mult"],
                                  cooldown_bars=params["cooldown_bars"], cost=cost)
    return stats(trades), max_drawdown(trades, CAPITAL)


def yearly_breakdown(bars: pd.DataFrame, params: dict) -> dict:
    """Per-calendar-year PF, for the gate's 'positive or flat every full
    year' criterion."""
    sig = daily_swing_trend_signals(
        bars, donch_n=params["donch_n"], confirm_bars=params["confirm_bars"],
        atr_expansion_required=params["atr_expansion_required"],
        htf_ema_period=params["htf_ema_period"],
        min_penetration_atr=params["min_penetration_atr"],
    )
    trades = simulate_chandelier(bars, sig, atr_mult=params["atr_mult"],
                                  cooldown_bars=params["cooldown_bars"])
    if trades.empty:
        return {}
    trades["year"] = pd.to_datetime(trades["exit_ts"]).dt.year
    return {int(yr): stats(sub) for yr, sub in trades.groupby("year")}


if __name__ == "__main__":
    daily = load_daily_bars()
    is_slice, oos_slice = split_is_oos(daily)
    print(f"Total daily bars: {len(daily)} ({daily.index.min()} -> {daily.index.max()})")
    print(f"IN-SAMPLE:  {len(is_slice)} bars ({is_slice.index.min()} -> {is_slice.index.max()})")
    print(f"OUT-OF-SAMPLE: {len(oos_slice)} bars ({oos_slice.index.min()} -> {oos_slice.index.max()})")
