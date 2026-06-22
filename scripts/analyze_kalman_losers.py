#!/usr/bin/env python3
"""
What do kalman_regime LOSING trades have in common? (XAUUSD 15m).

Replays the REAL KalmanRegimeStrategy.on_bar() (current config_live_5000 params,
incl. the shipped SELL gate 0.85) over the full span, capturing the full signal
metadata (mode, zscore, adx, rsi, atr, strength), runs the fixed-fill sim
(SL33/RR1/lot0.02/BE — the live-equivalent geometry), then profiles LOSERS vs
WINNERS across every captured feature + hour/weekday/month.

Caches the replay (slow) at data/backtests/kalman_loser_sig_<year>.csv.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.disable(logging.INFO)
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
from scripts.backtest_kalman_2026_fixed import build_symbol, simulate, stats
from scripts.validate_kalman_buygate import load_15m

CFG = PROJECT_ROOT / "config/config_live_5000.yaml"
SPAN = ("2025-02-01", "2026-06-22")


def replay_rich(bars, kcfg, cfg, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_csv(cache, parse_dates=["signal_ts"])
    strat = KalmanRegimeStrategy(build_symbol(cfg), kcfg)
    rows, n = [], len(bars)
    print(f"  replaying {n} bars -> {cache.name}")
    for i in range(n):
        window = bars.iloc[max(0, i + 1 - 1000):i + 1]
        if len(window) < 50:
            continue
        sig = strat.on_bar(window)
        if sig is not None:
            md = sig.metadata or {}
            rows.append({"bar_idx": i, "signal_ts": bars.index[i],
                         "side": sig.side.value, "strength": float(sig.strength),
                         "mode": md.get("mode"), "zscore": md.get("zscore"),
                         "adx": md.get("adx"), "rsi": md.get("rsi"),
                         "atr": md.get("atr")})
        if (i + 1) % 5000 == 0:
            print(f"    {i+1}/{n}, {len(rows)} sig")
    df = pd.DataFrame(rows)
    df.to_csv(cache, index=False)
    return df


def grp(df, col, bins=None, labels=None):
    g = pd.cut(df[col], bins=bins, labels=labels) if bins is not None else df[col]
    out = df.groupby(g, observed=True).agg(n=("pnl", "size"), wr=("win", "mean"),
                                           net=("pnl", "sum"), avg=("pnl", "mean"))
    out["wr"] = (out["wr"] * 100).round(1)
    out["net"] = out["net"].round(0); out["avg"] = out["avg"].round(1)
    return out


def main():
    cfg = yaml.safe_load(CFG.read_text())
    kcfg = dict(cfg["strategies"]["kalman_regime"]); kcfg["enabled"] = True
    kcfg.setdefault("htf_sell_filter_enabled", True)
    bars = load_15m(*SPAN)
    sig = replay_rich(bars, kcfg, cfg, PROJECT_ROOT / "data/backtests/kalman_loser_sig_full.csv")
    print(f"signals: {len(sig)}  (SELL gate {kcfg.get('min_signal_strength_sell')})")

    trades, _ = simulate(bars, sig)            # SL33/RR1/lot0.02/BE — live-equiv
    s = stats(trades)
    # attach features by signal->entry (entry fills next bar)
    feats = sig.set_index("signal_ts")
    sig_ts = feats.index.sort_values()
    trades["sig_ts"] = trades["entry_ts"].map(
        lambda e: (sig_ts[sig_ts < e][-1] if (sig_ts < e).any() else pd.NaT))
    t = trades.merge(feats, left_on="sig_ts", right_index=True, how="left", suffixes=("", "_f"))
    t["win"] = (t["pnl"] > 0).astype(int)

    print("=" * 70)
    print(f"KALMAN losers  (XAUUSD 15m, {SPAN[0]}->{SPAN[1]}, SL33/RR1/BE)")
    print(f"N {s['n']}  WR {s['wr']:.1f}%  PF {s['pf']:.2f}  net ${s['net']:+,.0f}")
    print("=" * 70)
    w, l = t[t.win == 1], t[t.win == 0]
    print(f"winners {len(w)}  losers {len(l)}")
    print("\n--- WIN vs LOSS feature means ---")
    cols = ["strength", "zscore", "adx", "rsi", "atr", "bars_held"]
    cmp = pd.DataFrame({"win": w[cols].mean(), "loss": l[cols].mean()}).round(3)
    print(cmp)
    print("\n--- by SIDE ---");  print(grp(t, "side"))
    print("\n--- by MODE ---");  print(grp(t, "mode"))
    print("\n--- by SIDE x MODE ---")
    t["side_mode"] = t["side"] + "/" + t["mode"].astype(str)
    print(grp(t, "side_mode"))
    t["hour"] = pd.to_datetime(t["entry_ts"]).dt.hour
    print("\n--- by HOUR (UTC) ---"); print(grp(t, "hour"))
    print("\n--- by ADX (trend strength) ---")
    print(grp(t, "adx", bins=[0, 18, 22, 28, 35, 100], labels=["<18", "18-22", "22-28", "28-35", ">35"]))
    print("\n--- by RSI ---")
    print(grp(t, "rsi", bins=[0, 35, 45, 55, 65, 100], labels=["<35", "35-45", "45-55", "55-65", ">65"]))
    print("\n--- by |zscore| ---")
    t["absz"] = t["zscore"].abs()
    print(grp(t, "absz", bins=[0, 1, 2, 3, 100], labels=["<1", "1-2", "2-3", ">3"]))
    print("\n--- by ATR quartile ---")
    t["atr_q"] = pd.qcut(t["atr"], 4, labels=["Q1-low", "Q2", "Q3", "Q4-high"])
    print(grp(t, "atr_q"))
    print("\n--- by MONTH ---")
    t["mo"] = pd.to_datetime(t["entry_ts"]).dt.strftime("%Y-%m")
    print(grp(t, "mo").to_string())
    t.to_csv(PROJECT_ROOT / "data/backtests/kalman_loser_analysis.csv", index=False)


if __name__ == "__main__":
    main()
