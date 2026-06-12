#!/usr/bin/env python3
"""
EURUSD daily-horizon edge scan on ~22y of yfinance daily bars
(research only, nothing wired).

Why daily: the intraday scan (research_eurusd.py, 2026-06-12) found nothing
— EURUSD is the efficient major and the eye-catching 22:00 drift is a
rollover bid-spread artifact. Daily-horizon, wide-stop strategies are the
family that survives the strict-fill gate (monday_drift lesson), and 22y
gives real sample sizes that 2.5y of 5m data cannot.

Data: data/historical/EURUSD_daily.csv (yfinance EURUSD=X, 2004→now).
⚠️ DATA TRAP (found 2026-06-12): this file has FAKE opens after ~2013 —
open == same-day close snapshot (median |close-open| < 1p, 30p jumps from
prev close). Sims below enter at "open" and are therefore distorted from
2013 on; only close-to-close logic is valid on this file. The decisive
re-runs live in research_eurusd_streak.py (close-to-close + Dukascopy
true-OHLC validation).

VERDICT 2026-06-12: REJECT — best cell (streak N=4 hold=5d) passed the
gate close-to-close (IS 1.39 / OOS 1.47) but died in the implementable
form: PF 1.14-1.24 with any real ATR stop on true OHLC, entry-delay
fragile, all true-data profit from 2025 alone. See research_eurusd_streak.py.

Candidates (signal at close t, enter at open t+1, costs + swap charged):
  A rsi2_fade   — RSI(2) extremes, exit on RSI cross 50 / max 5d, 1.5atr stop
  B streak_fade — N consecutive same-direction closes, fade, hold k days
  C ibs_fade    — internal bar strength (close-low)/(high-low) extremes
  D dow         — day-of-week holds (diagnostic; Monday drift trap noted)
  E trend       — 20d breakout control (expected dead on EURUSD daily)

Split: IS = 2004-01-01..2019-12-31, OOS = 2020-01-01..end.
Era breakdown (2004-12 / 13-19 / 20-26) reported for stability — a daily
edge that died a decade ago is not an edge.
Gate: PF_net >= 1.3 IS AND same-direction OOS, no dead eras.

Usage:
    python scripts/research_eurusd_daily.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_ROOT))

from research_fx_majors import stats

PIPSZ = 0.0001
COST_P = 1.3            # round-trip, pips
SWAP_LONG_P = -0.6      # per night, pips (long EUR pays carry on average)
SWAP_SHORT_P = +0.1
IS_END = pd.Timestamp("2019-12-31")
ERAS = [("2004-12", "2004-01-01", "2012-12-31"),
        ("2013-19", "2013-01-01", "2019-12-31"),
        ("2020-26", "2020-01-01", "2030-01-01")]


def load_daily() -> pd.DataFrame:
    df = pd.read_csv(PROJECT_ROOT / "data/historical/EURUSD_daily.csv",
                     parse_dates=["timestamp"], index_col="timestamp")
    # scrub bad ticks: zero/negative range or range > 6x rolling ATR
    rng = df.high - df.low
    atr = rng.rolling(20).median()
    bad = (rng <= 0) | (rng > 6 * atr.fillna(rng.median()))
    return df[~bad]


def atr14(d: pd.DataFrame) -> pd.Series:
    pc = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(14).mean()


def rsi(close: pd.Series, n: int = 2) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def sim(d: pd.DataFrame, sig: pd.Series, atr: pd.Series, stop_atr: float,
        max_days: int, exit_rule=None) -> pd.DataFrame:
    """sig[t] in {-1,0,1} known at close t → enter open t+1. Stop checked on
    daily high/low (pessimistic: stop before profit). exit_rule(i_abs)->bool
    checked on each close after entry; else exit at max_days close."""
    rows = {}
    i = 1
    idx = d.index
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    sg = sig.reindex(d.index).fillna(0).values
    av = atr.reindex(d.index).values
    n = len(d)
    while i < n - 1:
        s = int(sg[i])
        a = av[i]
        if s == 0 or np.isnan(a) or a <= 0:
            i += 1
            continue
        e_i = i + 1
        entry = o[e_i]
        stop = entry - s * stop_atr * a
        exit_i, exit_px = None, None
        for j in range(e_i, min(e_i + max_days, n)):
            if (s == 1 and l[j] <= stop) or (s == -1 and h[j] >= stop):
                exit_i, exit_px = j, stop
                break
            if exit_rule is not None and exit_rule(j) and j > e_i - 1:
                exit_i, exit_px = j, c[j]
                break
        if exit_i is None:
            exit_i = min(e_i + max_days - 1, n - 1)
            exit_px = c[exit_i]
        nights = exit_i - e_i
        swap = nights * (SWAP_LONG_P if s == 1 else SWAP_SHORT_P)
        pnl = ((exit_px - entry) * s) / PIPSZ - COST_P + swap
        rows[idx[e_i]] = (pnl, stop_atr * a / PIPSZ)
        i = exit_i + 1
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


# ---------------------------------------------------------------- output

def show_df(df: pd.DataFrame, label: str) -> None:
    r = stats(df.pnl if len(df) else pd.Series(dtype=float), label)
    if r.get("n", 0) == 0:
        print(f"    {label:<22} n=0")
        return
    print(f"    {r['label']:<22} n={r['n']:<5} PF={r['pf']:<5.2f} WR={r['wr']:5.1f}%  "
          f"avg={r['mean_pips']:+6.2f}p  t={r['t']:+5.2f}  total={r['sum_pips']:+8.0f}p")


def run(name: str, df: pd.DataFrame) -> None:
    print(f"  {name}:")
    if len(df) == 0:
        print("    n=0")
        return
    show_df(df[df.index <= IS_END], "IS  2004..2019")
    show_df(df[df.index > IS_END], "OOS 2020..end")
    for era, a, b in ERAS:
        show_df(df[(df.index >= a) & (df.index <= b)], f"  era {era}")


def main() -> int:
    d = load_daily()
    a = atr14(d)
    print(f"EURUSD daily: {d.index[0].date()} → {d.index[-1].date()}  ({len(d)} bars, "
          f"median ATR {a.median() / PIPSZ:.0f}p, cost {COST_P}p RT + swap)")

    r2 = rsi(d.close, 2)

    print("\nA rsi2_fade (exit RSI cross 50 or 5d, 1.5atr stop):")
    for lo, hi in ((10, 90), (5, 95)):
        sig = pd.Series(0, index=d.index)
        sig[r2 < lo] = 1
        sig[r2 > hi] = -1
        rvals = r2.values
        # longs and shorts simulated separately so the RSI-cross-50 exit
        # rule is unambiguous per side
        longs = sig.clip(lower=0)
        shorts = sig.clip(upper=0)
        tl = sim(d, longs, a, 1.5, 5, exit_rule=lambda j: rvals[j] >= 50)
        ts = sim(d, shorts, a, 1.5, 5, exit_rule=lambda j: rvals[j] <= 50)
        run(f"lo={lo} hi={hi} BOTH", pd.concat([tl, ts]).sort_index())
        run(f"lo={lo} long-only", tl)
        run(f"hi={hi} short-only", ts)

    print("\nB streak_fade (N same-direction closes, hold k days, 2atr stop):")
    ret = d.close.diff()
    for nstreak in (3, 4, 5):
        dn = (ret < 0).rolling(nstreak).sum() == nstreak
        up = (ret > 0).rolling(nstreak).sum() == nstreak
        for k in (2, 5):
            sig = pd.Series(0, index=d.index)
            sig[dn] = 1
            sig[up] = -1
            run(f"N={nstreak} hold={k}d",
                sim(d, sig, a, 2.0, k))

    print("\nC ibs_fade (IBS extremes, exit next close..3d, 1.5atr stop):")
    ibs = (d.close - d.low) / (d.high - d.low).replace(0, np.nan)
    for lo, hi in ((0.1, 0.9), (0.2, 0.8)):
        sig = pd.Series(0, index=d.index)
        sig[ibs < lo] = 1
        sig[ibs > hi] = -1
        for k in (1, 3):
            run(f"lo={lo} hi={hi} hold={k}d", sim(d, sig, a, 1.5, k))

    print("\nD dow (enter open, exit next open ≈ close-to-close 1d hold, both sides):")
    for dow, name in ((0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri")):
        for s in (1, -1):
            sig = pd.Series(0, index=d.index)
            # signal on prior day's close → entry at open of `dow`
            prev = d.index.dayofweek == ((dow - 1) % 7 if dow > 0 else 4)
            sig[prev & (pd.Series(d.index, index=d.index).shift(-1).dt.dayofweek == dow)] = s
            t = sim(d, sig, a, 99.0, 1)  # effectively no stop, 1-day hold
            if len(t) > 100:
                r = stats(t.pnl, f"{name} {'long' if s == 1 else 'short'}")
                ist = t[t.index <= IS_END]
                oot = t[t.index > IS_END]
                ri, ro = stats(ist.pnl, ""), stats(oot.pnl, "")
                print(f"    {name} {'L' if s == 1 else 'S'}: all n={r['n']} "
                      f"avg={r['mean_pips']:+5.2f}p t={r['t']:+5.2f} | "
                      f"IS avg={ri['mean_pips']:+5.2f}p t={ri['t']:+5.2f} | "
                      f"OOS avg={ro['mean_pips']:+5.2f}p t={ro['t']:+5.2f}")

    print("\nE trend control (20d Donchian breakout, exit 10d opposite, no TP):")
    hi20 = d.high.rolling(20).max().shift(1)
    lo20 = d.low.rolling(20).min().shift(1)
    sig = pd.Series(0, index=d.index)
    sig[d.close > hi20] = 1
    sig[d.close < lo20] = -1
    run("donchian20", sim(d, sig, a, 2.0, 10))

    print("\nGate: PF_net >= 1.3 IS AND same-direction OOS, no dead eras. "
          "Winners must re-validate on Dukascopy 5m before promotion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
