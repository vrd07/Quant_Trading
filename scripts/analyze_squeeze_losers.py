#!/usr/bin/env python3
"""
What do squeeze_breakout LOSING trades have in common? (XAUUSD 15m).

Runs the production geometry (SL33 fixed / RR2.0, risk-bypassed fixed-fill sim,
same harness as research_squeeze_breakout) over the full available span, then
profiles LOSERS vs WINNERS across every feature we can cheaply attach at entry:
side, hour, weekday, month, breakout penetration, ATR-at-entry, coil tightness,
distance of the break beyond the Donchian edge, and bars_held / exit_reason.

Read-only analysis. Writes nothing but a printed report (+ optional CSV).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators
from scripts.backtest_kalman_2026_fixed import simulate, stats
from scripts.validate_kalman_buygate import load_15m
from scripts.research_squeeze_breakout import squeeze_breakout_signals, KAL_Q, KAL_R

SL, RR, LOT, COST, CAP = 33.0, 2.0, 0.04, 0.20, 295.0
START, END = "2025-02-01", "2026-06-17"   # full span (OOS+IS combined)


def attach_features(bars, sig):
    """Re-derive the entry-bar context for each signal so we can profile it."""
    close, high, low = bars["close"], bars["high"], bars["low"]
    atr = Indicators.atr(bars, period=14)
    kal = Indicators.kalman_filter(close, q=KAL_Q, r=KAL_R)
    q20 = atr.rolling(100).quantile(0.20)
    donch_hi = high.rolling(20).max().shift(1)
    donch_lo = low.rolling(20).min().shift(1)
    slope = (kal - kal.shift(3)).abs()

    feats = []
    for _, s in sig.iterrows():
        i = int(s["bar_idx"])
        a = float(atr.iloc[i]) or 1.0
        feats.append({
            "signal_ts": s["signal_ts"],
            "side": s["side"],
            "atr": a,
            "pen": float(s["strength"]) * a,           # penetration beyond Donchian
            "pen_atr": float(s["strength"]),            # penetration / ATR
            "atr_pctile": float(atr.iloc[i] / q20.iloc[i]) if q20.iloc[i] else np.nan,
            "kal_slope_atr": float(slope.iloc[i] / a),  # how flat the coil was
            "atr_jump": float(atr.iloc[i] / atr.iloc[i - 1]) if atr.iloc[i - 1] else np.nan,
        })
    return pd.DataFrame(feats)


def grp(df, col, bins=None, labels=None):
    g = pd.cut(df[col], bins=bins, labels=labels) if bins is not None else df[col]
    out = df.groupby(g, observed=True).agg(
        n=("pnl", "size"),
        wr=("win", "mean"),
        net=("pnl", "sum"),
        avg=("pnl", "mean"),
    )
    out["wr"] = (out["wr"] * 100).round(1)
    out["net"] = out["net"].round(0)
    out["avg"] = out["avg"].round(1)
    return out


def main():
    bars = load_15m(START, END)
    sig, ncoil = squeeze_breakout_signals(bars)
    trades, _ = simulate(bars, sig, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
    feats = attach_features(bars, sig)

    # join trades <- features on entry timestamp (signal fills next bar)
    feats = feats.set_index("signal_ts")
    # map each trade's entry_ts back to the signal bar (signal_ts = entry_bar-1)
    sig_ts_sorted = feats.index.sort_values()
    def prior_sig(entry_ts):
        prev = sig_ts_sorted[sig_ts_sorted < entry_ts]
        return prev[-1] if len(prev) else pd.NaT
    trades["sig_ts"] = trades["entry_ts"].map(prior_sig)
    t = trades.merge(feats, left_on="sig_ts", right_index=True, how="left", suffixes=("", "_f"))
    t["win"] = (t["pnl"] > 0).astype(int)
    s = stats(trades)

    L = [f.rstrip() for f in [
        "=" * 70,
        f"SQUEEZE_BREAKOUT losing-trade profile  (XAUUSD 15m, {START}->{END})",
        f"SL{SL:.0f}/RR{RR:.0f} fixed, risk-bypassed.  coil bars {ncoil}/{len(bars)}",
        "=" * 70,
        f"N {s['n']}  WR {s['wr']:.1f}%  PF {s['pf']:.2f}  net ${s['net']:+,.0f}"
        f"  avgW ${s['avg_w']:+.0f}  avgL ${s['avg_l']:+.0f}",
    ]]
    print("\n".join(L))

    wins, losses = t[t.win == 1], t[t.win == 0]
    print(f"\nwinners {len(wins)}  losers {len(losses)}")

    print("\n--- exit_reason breakdown ---")
    print(t.groupby("exit_reason").agg(n=("pnl", "size"), net=("pnl", "sum")).round(0))

    print("\n--- WIN vs LOSS feature means ---")
    cols = ["pen_atr", "atr", "atr_pctile", "kal_slope_atr", "atr_jump", "bars_held"]
    cmp = pd.DataFrame({
        "win": wins[cols].mean(),
        "loss": losses[cols].mean(),
    }).round(3)
    cmp["loss/win"] = (cmp["loss"] / cmp["win"]).round(2)
    print(cmp)

    print("\n--- by SIDE ---")
    print(grp(t, "side"))

    print("\n--- by HOUR (UTC) ---")
    t["hour"] = pd.to_datetime(t["entry_ts"]).dt.hour
    print(grp(t, "hour"))

    print("\n--- by WEEKDAY (0=Mon) ---")
    t["wd"] = pd.to_datetime(t["entry_ts"]).dt.weekday
    print(grp(t, "wd"))

    print("\n--- by PENETRATION/ATR (how far past Donchian the close broke) ---")
    print(grp(t, "pen_atr", bins=[0, 0.1, 0.25, 0.5, 1.0, 10],
              labels=["<0.1", "0.1-0.25", "0.25-0.5", "0.5-1.0", ">1.0"]))

    print("\n--- by ATR-at-entry quartile (vol level) ---")
    t["atr_q"] = pd.qcut(t["atr"], 4, labels=["Q1-low", "Q2", "Q3", "Q4-high"])
    print(grp(t, "atr_q"))

    print("\n--- by ATR_JUMP (expansion ratio atr_i/atr_i-1) ---")
    print(grp(t, "atr_jump", bins=[1.0, 1.02, 1.05, 1.1, 10],
              labels=["1.00-1.02", "1.02-1.05", "1.05-1.10", ">1.10"]))

    print("\n--- by COIL FLATNESS (kal_slope/ATR; lower = tighter coil) ---")
    print(grp(t, "kal_slope_atr", bins=[0, 0.15, 0.3, 0.5, 10],
              labels=["<0.15", "0.15-0.3", "0.3-0.5", ">0.5"]))

    print("\n--- by MONTH ---")
    t["mo"] = pd.to_datetime(t["entry_ts"]).dt.strftime("%Y-%m")
    print(grp(t, "mo"))

    out = PROJECT_ROOT / "data/backtests/squeeze_loser_analysis.csv"
    t.to_csv(out, index=False)
    print(f"\nper-trade CSV -> {out}")


if __name__ == "__main__":
    main()
