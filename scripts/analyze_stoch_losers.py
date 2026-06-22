#!/usr/bin/env python3
"""
What do stoch_pullback LOSING trades have in common? (XAUUSD 15m).

Mirrors analyze_squeeze_losers: generate the shipped signals (London->NY session,
RR2.0, structural stop, risk-bypassed) over the full span, profile LOSERS vs
WINNERS across every feature attachable at the signal bar — side, hour, weekday,
%K level, EMA-distance (trend extension), structural stop width, range width,
ATR-at-entry, bars_held, exit_reason.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.indicators import Indicators
from scripts.research_stoch_pullback import (
    load_tf, stoch_pullback_signals, simulate, stats, SESSIONS)

RR = 2.0
START, END = "2025-02-01", "2026-06-22"


def attach_features(bars, sig):
    close, high, low = bars["close"], bars["high"], bars["low"]
    ema = Indicators.ema(bars, period=50)
    k, d = Indicators.stochastic(bars, period=14)
    atr = Indicators.atr(bars, period=14)
    feats = []
    for _, s in sig.iterrows():
        i = int(s["bar_idx"])
        c = float(close.iloc[i]); a = float(atr.iloc[i]) or 1.0
        stop = float(s["stop_price"])
        dist = abs(c - stop)
        rng = float(high.iloc[i - 5:i].max() - low.iloc[i - 5:i].min())
        feats.append({
            "signal_ts": s["signal_ts"], "side": s["side"],
            "k": float(k.iloc[i]), "d": float(d.iloc[i]),
            "ema_dist_atr": (c - float(ema.iloc[i])) / a,   # signed trend extension
            "stop_pts": dist, "stop_atr": dist / a,
            "range_atr": rng / a, "atr": a,
            "strength": float(s["strength"]),
        })
    return pd.DataFrame(feats)


def grp(df, col, bins=None, labels=None):
    g = pd.cut(df[col], bins=bins, labels=labels) if bins is not None else df[col]
    out = df.groupby(g, observed=True).agg(n=("pnl", "size"), wr=("win", "mean"),
                                           net=("pnl", "sum"), avg=("pnl", "mean"))
    out["wr"] = (out["wr"] * 100).round(1)
    out["net"] = out["net"].round(0); out["avg"] = out["avg"].round(1)
    return out


def main():
    bars = load_tf(15, START, END)
    sig = stoch_pullback_signals(bars, session_hours=SESSIONS["london_ny"])
    trades = simulate(bars, sig, rr=RR)            # risk-bypassed
    feats = attach_features(bars, sig).set_index("signal_ts")

    sig_ts = feats.index.sort_values()
    def prior(entry_ts):
        prev = sig_ts[sig_ts < entry_ts]
        return prev[-1] if len(prev) else pd.NaT
    trades["sig_ts"] = trades["entry_ts"].map(prior)
    t = trades.merge(feats, left_on="sig_ts", right_index=True, how="left", suffixes=("", "_f"))
    t["win"] = (t["pnl"] > 0).astype(int)
    s = stats(trades)

    print("=" * 70)
    print(f"STOCH_PULLBACK losers  (XAUUSD 15m london_ny, {START}->{END})")
    print(f"N {s['n']}  WR {s['wr']:.1f}%  PF {s['pf']:.2f}  net ${s['net']:+,.0f}")
    print("=" * 70)
    wins, losses = t[t.win == 1], t[t.win == 0]
    print(f"winners {len(wins)}  losers {len(losses)}")
    print("\n--- exit_reason ---")
    print(t.groupby("exit_reason").agg(n=("pnl", "size"), net=("pnl", "sum")).round(0))
    print("\n--- WIN vs LOSS feature means ---")
    cols = ["k", "d", "ema_dist_atr", "stop_atr", "range_atr", "atr", "strength", "bars_held"]
    cmp = pd.DataFrame({"win": wins[cols].mean(), "loss": losses[cols].mean()}).round(3)
    cmp["loss/win"] = (cmp["loss"] / cmp["win"]).round(2)
    print(cmp)
    print("\n--- by SIDE ---");        print(grp(t, "side"))
    t["hour"] = pd.to_datetime(t["entry_ts"]).dt.hour
    print("\n--- by HOUR (UTC) ---");  print(grp(t, "hour"))
    t["wd"] = pd.to_datetime(t["entry_ts"]).dt.weekday
    print("\n--- by WEEKDAY (0=Mon) ---"); print(grp(t, "wd"))
    print("\n--- by %K at entry ---")
    print(grp(t, "k", bins=[0, 20, 40, 60, 80, 100], labels=["<20", "20-40", "40-60", "60-80", ">80"]))
    print("\n--- by EMA-distance/ATR (trend extension; +far above, -far below) ---")
    print(grp(t, "ema_dist_atr", bins=[-10, -2, -1, -0.3, 0.3, 1, 2, 10],
              labels=["<-2", "-2..-1", "-1..-.3", "-.3..3", ".3..1", "1..2", ">2"]))
    print("\n--- by STRUCTURAL STOP width / ATR ---")
    print(grp(t, "stop_atr", bins=[0, 0.5, 1, 1.5, 2.5, 20], labels=["<0.5", "0.5-1", "1-1.5", "1.5-2.5", ">2.5"]))
    print("\n--- by RANGE width / ATR (consolidation tightness) ---")
    print(grp(t, "range_atr", bins=[0, 1, 1.5, 2.5, 20], labels=["<1", "1-1.5", "1.5-2.5", ">2.5"]))
    print("\n--- by ATR-at-entry quartile ---")
    t["atr_q"] = pd.qcut(t["atr"], 4, labels=["Q1-low", "Q2", "Q3", "Q4-high"])
    print(grp(t, "atr_q"))
    print("\n--- by MONTH ---")
    t["mo"] = pd.to_datetime(t["entry_ts"]).dt.strftime("%Y-%m")
    print(grp(t, "mo"))
    out = PROJECT_ROOT / "data/backtests/stoch_loser_analysis.csv"
    t.to_csv(out, index=False)
    print(f"\nper-trade CSV -> {out}")


if __name__ == "__main__":
    main()
