#!/usr/bin/env python3
"""
monday_drift design study — GBPUSD / EURUSD / AUDUSD (user-mandated build).

Context (project_gbpusd_no_edge): the Monday-long effect (+16.5p/24h GBPUSD,
t=+3.56; same on EURUSD/AUDUSD, inverted on USDJPY) is anti-USD regime drift
of 2025-26, NOT a structural edge — near-zero in 2024 and it will reverse in
a USD-strength regime. User chose to harvest it anyway (LBO precedent).
This study settles the implementation knobs so the live strategy is the
least-fragile version of the trade:

  entry  — Sun 22:30 (open+30m) vs Mon 00:00 vs Mon 07:00 UTC.
           Sunday 22:00 itself is OFF the table (BID-spread artifact).
  exit   — Mon 21:00 UTC (before rollover) vs +24h hold.
  stop   — none (time exit only) vs 0.75 x dailyATR.
  gate   — none vs close>50dMA (USD-downtrend proxy) vs rolling sum of the
           last 8 Monday-trade PnLs > 0 (self-referential kill-switch).

Costs: 2p round trip (normal-hours spread; Sun 22:30 charged 3p).
Split: IS 2024-01..2025-09, OOS 2025-10..end. Judge plateaus, not winners.

Usage:
    python scripts/research_monday_drift.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_ROOT))

from research_fx_majors import PIP, load, resample
from research_gbpusd import _daily_atr

COST_P = 2.0          # round-trip pips, normal hours
COST_P_SUN = 3.0      # round-trip pips, Sunday night
IS_END = "2025-09-30 23:59:59+00:00"


def monday_trades(df: pd.DataFrame, symbol: str, entry_mode: str,
                  exit_mode: str, stop_atr: float | None) -> pd.DataFrame:
    """One long per week. entry_mode: 'sun2230' | 'mon0000' | 'mon0700'.
    exit_mode: 'mon2100' | 'h24'. stop_atr: None or ATR multiple."""
    pipsz = PIP[symbol]
    atr_d = _daily_atr(df)
    idx = df.index
    weekend = np.where(idx.to_series().diff() > pd.Timedelta(hours=24))[0]
    rows = {}
    for i in weekend:
        t_open = idx[i]
        if entry_mode == "sun2230":
            t_entry = t_open + pd.Timedelta(minutes=30)
            cost = COST_P_SUN
        else:
            # next calendar day (Monday) at the given hour
            day1 = (t_open + pd.Timedelta(days=1)).normalize()
            hh = 0 if entry_mode == "mon0000" else 7
            t_entry = day1 + pd.Timedelta(hours=hh)
            cost = COST_P
        sub = df.iloc[i:i + 2000]
        after = sub[sub.index >= t_entry]
        if after.empty:
            continue
        entry_ts = after.index[0]
        entry = after.open.iloc[0]
        if exit_mode == "mon2100":
            t_exit = (t_open + pd.Timedelta(days=1)).normalize() + pd.Timedelta(hours=21)
        else:
            t_exit = entry_ts + pd.Timedelta(hours=24)
        if t_exit <= entry_ts:
            continue
        atr = atr_d.asof(entry_ts)
        if stop_atr is not None and (np.isnan(atr) or atr <= 0):
            continue
        stop = entry - stop_atr * atr if stop_atr is not None else -np.inf
        window = after[after.index <= t_exit]
        if len(window) < 2:
            continue
        pnl = None
        for _, b in window.iloc[1:].iterrows():
            if b.low <= stop:
                pnl = stop - entry
                break
        if pnl is None:
            pnl = window.close.iloc[-1] - entry
        stop_p = (entry - stop) / pipsz if stop_atr is not None else np.nan
        rows[entry_ts] = ((pnl / pipsz) - cost, stop_p)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def apply_gate(trades: pd.DataFrame, df: pd.DataFrame, symbol: str,
               gate: str) -> pd.DataFrame:
    if gate == "none" or trades.empty:
        return trades
    if gate == "50dma":
        d1 = resample(df, "1D")
        ma = d1.close.rolling(50).mean()
        keep = [not np.isnan(ma.asof(ts)) and df.close.asof(ts) > ma.asof(ts)
                for ts in trades.index]
        return trades[keep]
    if gate == "roll8":
        # trade only when the sum of the previous 8 UNGATED Monday PnLs > 0
        prior = trades.pnl.rolling(8).sum().shift(1)
        return trades[prior > 0]
    raise ValueError(gate)


def line(label: str, t: pd.DataFrame) -> None:
    if len(t) == 0:
        print(f"    {label:<34} n=0")
        return
    p = t.pnl
    w, l = p[p > 0], p[p < 0]
    pf = w.sum() / -l.sum() if len(l) and l.sum() < 0 else float("inf")
    tstat = p.mean() / p.std() * np.sqrt(len(p)) if len(p) > 1 else 0
    eq = p.cumsum()
    dd = (eq - eq.cummax()).min()
    print(f"    {label:<34} n={len(p):<4} PF={pf:5.2f} avg={p.mean():+6.1f}p "
          f"t={tstat:+5.2f} total={p.sum():+7.0f}p maxDD={dd:+6.0f}p "
          f"worst={p.min():+6.1f}p")


def main() -> int:
    for sym in ("GBPUSD", "EURUSD", "AUDUSD"):
        df = load(sym)
        print(f"\n{'=' * 78}\n{sym}\n{'=' * 78}")
        print("  -- entry/exit grid (no stop, no gate) --")
        for em in ("sun2230", "mon0000", "mon0700"):
            for xm in ("mon2100", "h24"):
                t = monday_trades(df, sym, em, xm, None)
                for split, lab in ((t[t.index <= IS_END], "IS "),
                                   (t[t.index > IS_END], "OOS")):
                    line(f"{em}/{xm} {lab}", split)
        print("  -- stop variants (mon0000/mon2100) --")
        for sa in (0.5, 0.75, 1.0):
            t = monday_trades(df, sym, "mon0000", "mon2100", sa)
            line(f"stop={sa}atr ALL", t)
        print("  -- regime gates (mon0000/mon2100, no stop) --")
        base = monday_trades(df, sym, "mon0000", "mon2100", None)
        for g in ("none", "50dma", "roll8"):
            t = apply_gate(base, df, sym, g)
            line(f"gate={g} ALL", t)
            for y, gy in t.groupby(t.index.year):
                line(f"  gate={g} {y}", gy)
    return 0


if __name__ == "__main__":
    sys.exit(main())
