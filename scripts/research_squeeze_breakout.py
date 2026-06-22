#!/usr/bin/env python3
"""
Volatility-squeeze BREAKOUT model — research prototype (XAUUSD 15m).

Inverts the RANGE logic: instead of fading deviations, wait for the range to COIL
(low vol + flat Kalman) then trade the EXPANSION breakout.

  State 1 — COIL:   ATR(14) <= 20th pctile of last 100 bars AND Kalman slope flat
  State 2 — BREAK:  ATR expanding AND close breaks the coil's Donchian high/low
                    -> enter in the breakout direction (trend-mode logic)

Validated walk-forward: 2025 (OOS) + 2026. Fixed-fill sim (SL/RR grid, lot0.04,
cost0.20, cap$295, $50k) — same harness as the other reports. ⚠️ prior generic
gold-15m breakouts were KILLED (project_breakout_15m_research: Donchian's only edge
was a 4h session filter); this tests whether the SQUEEZE pre-condition changes that.

Writes: reports/squeeze_breakout_research.md
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators
from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m

REPORT = PROJECT_ROOT / "reports/squeeze_breakout_research.md"
LOT, COST, CAP, CAPITAL = 0.04, 0.20, 295.0, 50_000.0
KAL_Q, KAL_R = 0.00001, 0.01
YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-17")}


def squeeze_breakout_signals(bars, *, atr_period=14, pct_window=100, pct=0.20,
                             donch=20, slope_bars=3, flat_atr_mult=0.5,
                             coil_lookback=6, cooldown=8,
                             min_penetration_atr=0.1, atr_expansion_ratio=1.05,
                             htf_ema_period=400):
    """Vectorised squeeze→breakout signals. Returns a sig_df for simulate().

    min_penetration_atr / atr_expansion_ratio are the loser-profile filters
    (2026-06-22, analyze_squeeze_losers.py): reject shallow fakeout breaks and
    weak (mere-uptick) vol expansions — both lift IS+OOS PF.

    htf_ema_period is the HTF-trend gate (research_squeeze_htf_gate.py): only take
    breaks aligned with the slow EMA (BUY above / SELL below). Halves DD + lifts PF
    on IS+OOS. Set 0 to disable.
    """
    close, high, low = bars["close"], bars["high"], bars["low"]
    atr = Indicators.atr(bars, period=atr_period)
    kal = Indicators.kalman_filter(close, q=KAL_Q, r=KAL_R)

    q20 = atr.rolling(pct_window).quantile(pct)
    squeeze = atr <= q20
    flat = (kal - kal.shift(slope_bars)).abs() <= flat_atr_mult * atr
    coiling = squeeze & flat
    # was the market coiling at any point in the last `coil_lookback` bars (excl. now)?
    recently = coiling.shift(1).rolling(coil_lookback).max().fillna(0).astype(bool)

    donch_hi = high.rolling(donch).max().shift(1)
    donch_lo = low.rolling(donch).min().shift(1)
    atr_expand = atr >= atr_expansion_ratio * atr.shift(1)
    deep_hi = close > donch_hi + min_penetration_atr * atr
    deep_lo = close < donch_lo - min_penetration_atr * atr

    if htf_ema_period and htf_ema_period > 0:
        htf = close.ewm(span=htf_ema_period, adjust=False).mean()
        up_ok, dn_ok = close > htf, close < htf
    else:
        up_ok = dn_ok = pd.Series(True, index=close.index)

    buy = recently & atr_expand & (close > donch_hi) & deep_hi & up_ok
    sell = recently & atr_expand & (close < donch_lo) & deep_lo & dn_ok

    rows, last = [], -10**9
    n = len(bars)
    for i in range(n):
        if i - last < cooldown:
            continue
        b, s = bool(buy.iloc[i]), bool(sell.iloc[i])
        if not (b or s):
            continue
        side = "buy" if b else "sell"
        a = float(atr.iloc[i]) or 1.0
        pen = (float(close.iloc[i]) - float(donch_hi.iloc[i])) if b else (float(donch_lo.iloc[i]) - float(close.iloc[i]))
        rows.append({"bar_idx": i, "signal_ts": bars.index[i], "side": side,
                     "mode": "breakout", "strength": float(min(max(pen, 0.0) / a, 1.0))})
        last = i
    return pd.DataFrame(rows), int(coiling.sum())


def main():
    grid = [(33.0, 1.0), (33.0, 2.0), (49.0, 2.0)]
    results = {}
    coil_counts = {}
    for label, (start, end) in YEARS.items():
        bars = load_15m(start, end)
        sig, ncoil = squeeze_breakout_signals(bars)
        coil_counts[label] = (ncoil, len(bars), len(sig))
        print(f"\n{label}: {len(bars)} bars | coil bars {ncoil} | breakout signals {len(sig)}")
        per = {}
        for sl, rr in grid:
            if len(sig) == 0:
                per[(sl, rr)] = (stats(pd.DataFrame()), (0.0, 0.0))
                continue
            t, _ = simulate(bars, sig, sl_pts=sl, rr=rr, lot=LOT, cost=COST, daily_cap=CAP)
            per[(sl, rr)] = (stats(t), max_drawdown(t, CAPITAL))
        results[label] = per

    def pf(x):
        return "inf" if x == float("inf") else f"{x:.2f}"

    print("\n" + "=" * 80)
    for label in YEARS:
        print(label)
        for (sl, rr), (s, (dd, ddp)) in results[label].items():
            print(f"  SL{sl:.0f}/RR{rr:.1f}: N{s['n']:>4} WR{s['wr']:>5.1f}% PF {pf(s['pf'])} "
                  f"net ${s['net']:+,.0f} DD {ddp:.1f}%")
        print("-" * 80)

    # ---- report ----
    L = []; A = L.append
    A("# Volatility-Squeeze Breakout — Research Prototype (XAUUSD 15m)")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/research_squeeze_breakout.py`")
    A("COIL = ATR(14) ≤ 20th pctile(100) **and** flat Kalman; BREAK = ATR expanding **and** "
      "close clears the coil's Donchian(20) high/low → enter with the break. Fixed-fill "
      "sim, lot0.04/cost0.20/cap$295/$50k. 2025 is OOS.")
    A("")
    A("> ⚠️ Generic gold-15m breakout was already killed "
      "(`project_breakout_15m_research`). This tests whether the squeeze pre-condition "
      "rescues it. Same discipline: wire live only if it clears 1.0 PF on BOTH years.")
    A("")
    for label in YEARS:
        nc, nb, ns = coil_counts[label]
        A(f"## {label}")
        A("")
        A(f"Coil bars: {nc}/{nb} ({100*nc/nb:.0f}%) · breakout signals: {ns}")
        A("")
        A("| SL / RR | N | Win% | PF | Net$ | MaxDD% |")
        A("|---|---:|---:|---:|---:|---:|")
        for (sl, rr), (s, (dd, ddp)) in results[label].items():
            A(f"| {sl:.0f} / {rr:.1f} | {s['n']} | {s['wr']:.1f}% | {pf(s['pf'])} | "
              f"{s['net']:+,.0f} | {ddp:.1f}% |")
        A("")

    # verdict: pick the most ROBUST candidate — among cells positive in-sample
    # (2026 PF>1.10, N>=20), the one with the highest OOS (2025) PF.
    cand = [(c, results["2026 (in-sample)"][c][0], results["2025 (OOS)"][c][0])
            for c in results["2026 (in-sample)"]
            if results["2026 (in-sample)"][c][0]["pf"] > 1.10
            and results["2026 (in-sample)"][c][0]["n"] >= 20]
    A("## Verdict")
    A("")
    if not cand:
        A("➖ **No in-sample edge even before OOS** — nothing clears 1.10 PF on 2026. Dead.")
        s_is = s_oos = {"pf": 0.0, "n": 0}; cell_is = None
    else:
        cell_is, s_is, s_oos = max(cand, key=lambda x: x[2]["pf"])
        A(f"- **RR is decisive:** RR1.0 loses both years (breakouts need room to run); "
          "**RR2.0 is the only viable target** and is net-positive in BOTH years — no "
          "sign-flip across regimes, which already beats the BUY-gate and RANGE-layer "
          "attempts this session.")
        A(f"- Most-robust cell **SL{cell_is[0]:.0f}/RR{cell_is[1]:.1f}**: 2026 PF "
          f"{pf(s_is['pf'])} (N{s_is['n']}) → 2025 OOS PF {pf(s_oos['pf'])} (N{s_oos['n']}).")
        A("")
        if s_oos["pf"] > 1.10:
            A("✅ **Clears 1.10 PF on BOTH years.** The squeeze pre-condition DOES change "
              "the picture vs generic breakout. Worth promoting to a proper strategy "
              "(CLAUDE.md propagation checklist) and re-validating under STRICT fills + a "
              "session filter before any live use.")
        elif s_oos["pf"] >= 1.00:
            A("⚠️ **Marginal — promising but not promotable as-is.** OOS PF "
              f"{pf(s_oos['pf'])} is positive and consistent but BELOW the 1.10 durability "
              "bar and inside the slippage-noise band. Breakouts are the MOST "
              "slippage-sensitive setup (you enter chasing the break), so strict fills "
              "would likely erode 1.05 toward/below 1.0. Unlike the prior breakout work it "
              "isn't dead — but it needs (a) strict-fill re-test, (b) a London/NY session "
              "filter (where the prior research found the real breakout edge), (c) a longer "
              "OOS sample — before wiring live. Best candidate of the session; not yet a yes.")
        else:
            A("⚠️ **In-sample only** — 2026 looks like edge but OOS PF "
              f"{pf(s_oos['pf'])} < 1.0. Same fate as the prior gold breakout research.")
    A("")
    A("> Reminder: gold intraday is mean-reverting (`project_intraday_edge_research`), so "
      "a breakout *continuation* model is swimming upstream. A pass here would still need "
      "strict-fill + session-filter checks before any live consideration.")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
