#!/usr/bin/env python3
"""
EURUSD 4-day streak fade — deep-dive and REJECTION record (2026-06-12).

The daily scan (research_eurusd_daily.py) surfaced one gate-passing cell:
fade after 4 consecutive same-direction daily closes, hold 5 days
(close-to-close 22y: IS PF 1.39 / OOS 1.47, all eras positive, both sides
positive, pair-selective — GBPUSD 1.01 / AUDUSD 0.78 / USDJPY 1.43).

This script reproduces the three checks that killed it:
  1. entry-delay: entering one close later collapses IS to PF 1.06 —
     the edge concentrates in the hours right after the signal close.
  2. stops: on true-OHLC Dukascopy 2024-26 (next-open entry, ATR stop on
     real highs/lows) PF 1.56 unstopped -> 1.14-1.24 with any stop that
     ever fires (1.0-3.0xATR). The profit IS the unstopped recoveries; a
     no-stop strategy cannot pass the risk engine, and monday_drift only
     shipped because its PF held WITH the 1xATR stop.
  3. year stability on true data: 2024 PF 0.57 / 2025 1.99 / 2026 0.53 —
     all profit from one year of 2.5.

House rule: flat-cost research PF must clear ~1.3 in the IMPLEMENTABLE
form to survive the strict-fill gate. Implementable form = 1.14-1.24.

VERDICT: REJECT. Do not re-test streak/RSI2/IBS daily fades on EURUSD.

Data note: data/historical/EURUSD_daily.csv (yfinance EURUSD=X) has FAKE
opens after ~2013 (open == same-day close snapshot, median |close-open|
< 1 pip, 30p jumps from prev close). Only close-to-close logic is valid
on that file; anything using opens/highs/lows must use Dukascopy 5m.

Usage:
    python scripts/research_eurusd_streak.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
PIP = 0.0001
COST = 1.3           # round-trip pips
SWL, SWS = -0.6, 0.1  # swap pips/night long, short
IS_END = "2019-12-31"


def stats(p: pd.Series, label: str) -> None:
    n = len(p)
    if n == 0:
        print(f"  {label:<34} n=0")
        return
    w, l = p[p > 0], p[p < 0]
    pf = w.sum() / -l.sum() if len(l) else float("inf")
    t = p.mean() / p.std() * np.sqrt(n) if n > 1 else 0
    print(f"  {label:<34} n={n:<4} PF={pf:<5.2f} WR={100 * len(w) / n:4.1f}% "
          f"avg={p.mean():+7.2f}p t={t:+5.2f} tot={p.sum():+8.0f}p")


def streak_sig(close: pd.Series, n_streak: int = 4) -> pd.Series:
    ret = close.diff()
    sig = pd.Series(0, index=close.index)
    sig[(ret < 0).rolling(n_streak).sum() == n_streak] = 1
    sig[(ret > 0).rolling(n_streak).sum() == n_streak] = -1
    return sig


def main() -> int:
    # ---- 22y yfinance closes (close-to-close only; opens are fake) ----
    df = pd.read_csv(PROJECT_ROOT / "data/historical/EURUSD_daily.csv",
                     parse_dates=["timestamp"], index_col="timestamp")
    c = df.close
    sig = streak_sig(c)
    print("=== 1. entry-delay test (22y c2c): enter close t+d, exit close t+d+5 ===")
    for delay in (0, 1, 2):
        out, i = {}, 0
        sv, cv, idx, n = sig.values, c.values, c.index, len(c)
        while i < n - 5 - delay:
            s = int(sv[i])
            if s == 0:
                i += 1
                continue
            out[idx[i]] = ((cv[i + delay + 5] - cv[i + delay]) * s / PIP
                           - COST + 5 * (SWL if s == 1 else SWS))
            i += delay + 6
        p = pd.Series(out)
        stats(p[p.index <= IS_END], f"delay={delay} IS 2004-19")
        stats(p[p.index > IS_END], f"delay={delay} OOS 2020-26")

    # ---- true-OHLC Dukascopy 2024-26: implementable form ----
    m5 = pd.read_csv(PROJECT_ROOT / "data/historical/EURUSD_5m_real.csv",
                     parse_dates=["timestamp"], index_col="timestamp")
    flat = (m5.open == m5.close) & (m5.high == m5.low) & (m5.volume == 0)
    m5 = m5[~flat]
    dk = m5.resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    pc = dk.close.shift(1)
    atr = pd.concat([dk.high - dk.low, (dk.high - pc).abs(), (dk.low - pc).abs()],
                    axis=1).max(axis=1).rolling(14).mean()
    sigk = streak_sig(dk.close)
    cv, ov, hv, lv = dk.close.values, dk.open.values, dk.high.values, dk.low.values
    sv, av, idx, n = sigk.values, atr.values, dk.index, len(dk)

    def sim_dk(stop_atr: float) -> pd.Series:
        out, i = {}, 0
        while i < n - 6:
            s, a = int(sv[i]), av[i]
            if s == 0 or np.isnan(a) or a <= 0:
                i += 1
                continue
            e = i + 1
            entry, stop, pnl, xi = ov[e], ov[e] - s * stop_atr * av[i], None, e + 4
            for j in range(e, min(e + 5, n)):
                if (s == 1 and lv[j] <= stop) or (s == -1 and hv[j] >= stop):
                    pnl, xi = (stop - entry) * s, j
                    break
            if pnl is None:
                xi = min(e + 4, n - 1)
                pnl = (cv[xi] - entry) * s
            out[idx[e]] = pnl / PIP - COST + (xi - e) * (SWL if s == 1 else SWS)
            i = xi + 1
        return pd.Series(out)

    print("\n=== 2. Dukascopy 2024-26 true OHLC, next-open entry, 5d time exit ===")
    for sa in (1.0, 1.5, 2.0, 3.0, 99.0):
        stats(sim_dk(sa), f"stop={sa}xATR" if sa < 99 else "no stop")

    print("\n=== 3. yearly on true data (stop=1.5xATR) ===")
    p = sim_dk(1.5)
    for y in (2024, 2025, 2026):
        stats(p[p.index.year == y], str(y))

    print("\nVERDICT: REJECT — implementable form PF 1.14-1.24 < 1.3 gate; "
          "edge exists only unstopped, year-flips, entry-delay fragile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
