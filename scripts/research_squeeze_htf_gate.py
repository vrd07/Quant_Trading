#!/usr/bin/env python3
"""
Does an HTF-trend gate make squeeze_breakout more profitable? (XAUUSD 15m)

The residual -6.25% DD is a single ~8-week whipsaw (Apr-May 2026): coil -> break ->
reverse, a 12-loss stop string. Hypothesis: only take breaks ALIGNED with a higher-
timeframe trend (continuation) and you dodge the counter-trend chop.

Walk-forward: IS 2026 vs OOS 2025, separately, on the SAME fixed-fill harness as
research_squeeze_breakout (SL33/RR2.0). A gate only ships if it improves BOTH years
(or holds PF while cutting DD) — anything that only helps 2026 is overfit.

HTF trend is computed on the 15m close itself (EMA/SMA of N bars; 1H=4 bars, so
EMA200_15m ~ EMA50_1H). Modes:
  side   : BUY only if close > HTF, SELL only if close < HTF  (price-vs-line)
  slope  : also require the HTF line itself sloping the trade's way over `slope_n`
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backtest_kalman_2026_fixed import simulate, stats, max_drawdown
from scripts.validate_kalman_buygate import load_15m
from scripts.research_squeeze_breakout import squeeze_breakout_signals

SL, RR, LOT, COST, CAP, CAPITAL = 33.0, 2.0, 0.04, 0.20, 295.0, 50_000.0
YEARS = {"2026 IS": ("2026-01-01", "2026-06-17"),
         "2025 OOS": ("2025-02-01", "2026-01-01")}


def htf_line(close, n, kind="ema"):
    if kind == "ema":
        return close.ewm(span=n, adjust=False).mean()
    return close.rolling(n).mean()


def apply_gate(bars, sig, *, n, kind, mode, slope_n=8):
    """Keep only signals whose side aligns with the HTF trend at the signal bar."""
    close = bars["close"]
    line = htf_line(close, n, kind)
    keep = []
    for _, s in sig.iterrows():
        i = int(s["bar_idx"])
        c, ln = float(close.iloc[i]), float(line.iloc[i])
        if np.isnan(ln):
            continue
        up = c > ln
        if mode == "slope":
            sl = ln - float(line.iloc[i - slope_n]) if i - slope_n >= 0 else 0.0
            up = up and sl > 0
            dn = (c < ln) and sl < 0
        else:
            dn = c < ln
        side = str(s["side"]).lower()
        if (side == "buy" and up) or (side == "sell" and dn):
            keep.append(s)
    return pd.DataFrame(keep) if keep else pd.DataFrame(columns=sig.columns)


def run(bars, sig):
    if len(sig) == 0:
        return dict(n=0, pf=0.0, net=0.0, dd=0.0)
    t, _ = simulate(bars, sig[["bar_idx", "signal_ts", "side", "mode", "strength"]],
                    sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
    s = stats(t)
    _, ddp = max_drawdown(t, CAPITAL)
    return dict(n=s["n"], pf=s["pf"], net=s["net"], dd=ddp)


VARIANTS = [
    ("baseline (no HTF gate)", None),
    ("EMA100 side",   dict(n=100, kind="ema", mode="side")),
    ("EMA200 side",   dict(n=200, kind="ema", mode="side")),
    ("EMA400 side",   dict(n=400, kind="ema", mode="side")),
    ("SMA200 side",   dict(n=200, kind="sma", mode="side")),
    ("EMA200 slope",  dict(n=200, kind="ema", mode="slope", slope_n=8)),
    ("EMA400 slope",  dict(n=400, kind="ema", mode="slope", slope_n=12)),
]


def main():
    cache = {}
    for lbl, (a, b) in YEARS.items():
        bars = load_15m(a, b)
        sig, _ = squeeze_breakout_signals(bars)
        cache[lbl] = (bars, sig)

    print(f"{'variant':26} | {'2026 IS':>26} | {'2025 OOS':>26}")
    print("-" * 84)
    for name, params in VARIANTS:
        row = f"{name:26} |"
        for lbl in YEARS:
            bars, sig = cache[lbl]
            g = sig if params is None else apply_gate(bars, sig, **params)
            r = run(bars, g)
            pf = "inf" if r["pf"] == float("inf") else f"{r['pf']:.2f}"
            row += f" N{r['n']:>3} PF{pf:>4} ${r['net']:>+6,.0f} DD{r['dd']:>5.1f}% |"
        print(row)


if __name__ == "__main__":
    main()
