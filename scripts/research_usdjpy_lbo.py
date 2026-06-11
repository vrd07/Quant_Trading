#!/usr/bin/env python3
"""
USDJPY London-breakout refinement (research only).

The FX-majors scan (research_fx_majors.py) found exactly one survivor:
USDJPY Asia-range breakout at London open — PF 1.23 IS / 1.46 OOS with
zero tuning. This script sweeps a small parameter grid ON IS ONLY
(2024-01..2025-09), ranks by t-stat (favors N over lucky PF), then runs
the single chosen config ONCE on OOS (2025-10..2026-06) and prints a
year-by-year consistency table.

Grid axes (deliberately small — 96 combos, no second pass):
  stop_frac   stop distance as fraction of Asia range behind entry
  exit_hour   flat-by hour UTC (London close-ish vs NY afternoon)
  win_end     last 15m bar that may trigger an entry
  rng_filter  none | narrow (range <= rolling-20d median: compression
              breeds expansion) | wide (range > median)
  tp_mult     optional TP at k x range (None = time/stop exit only)
"""

import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_fx_majors import load, resample, stats, IS_END  # noqa: E402

COST = 0.017   # round-trip price units (1.7 pips)
PIP = 0.01


def run_lbo(m15: pd.DataFrame, stop_frac: float, exit_hour: int,
            win_end: str, rng_filter: str, tp_mult) -> pd.Series:
    out = {}
    day_ranges = {}   # date -> asia range width, for the rolling median filter
    dates, widths = [], []
    for day, g in m15.groupby(m15.index.date):
        asia = g.between_time("00:00", "06:59")
        if len(asia) < 12:
            continue
        hi, lo = asia.high.max(), asia.low.min()
        rng = hi - lo
        if rng <= 0:
            continue
        # rolling 20-day median of past ranges (causal: excludes today)
        med = np.median(widths[-20:]) if len(widths) >= 5 else None
        dates.append(day); widths.append(rng)
        if rng_filter == "narrow" and (med is None or rng > med):
            continue
        if rng_filter == "wide" and (med is None or rng <= med):
            continue
        window = g.between_time("07:00", win_end)
        exit_w = g[(g.index.hour >= int(win_end[:2])) & (g.index.hour <= exit_hour)]
        if window.empty:
            continue
        for i, (ts, bar) in enumerate(window.iterrows()):
            side = 1 if bar.close > hi else (-1 if bar.close < lo else 0)
            if side == 0:
                continue
            later = pd.concat([window.iloc[i + 1:], exit_w[exit_w.index > window.index[-1]]])
            if later.empty:
                break
            entry = later.iloc[0].open
            stop = entry - side * stop_frac * rng
            tp = entry + side * tp_mult * rng if tp_mult else None
            pnl = None
            for _, b in later.iterrows():
                if (side == 1 and b.low <= stop) or (side == -1 and b.high >= stop):
                    pnl = (stop - entry) * side
                    break
                if tp is not None and ((side == 1 and b.high >= tp) or
                                       (side == -1 and b.low <= tp)):
                    pnl = (tp - entry) * side
                    break
            if pnl is None:
                pnl = (later.iloc[-1].close - entry) * side
            out[ts] = (pnl - COST) / PIP
            break  # one trade per day
    return pd.Series(out, dtype=float)


def main() -> int:
    df = load("USDJPY")
    m15 = resample(df, "15min")
    m15_is = m15[m15.index <= IS_END]
    m15_oos = m15[m15.index > IS_END]

    grid = list(product(
        (0.35, 0.5, 0.75, 1.0),          # stop_frac
        (12, 15, 18),                     # exit_hour
        ("09:45", "11:45"),               # win_end
        ("none", "narrow", "wide"),       # rng_filter
        (None, 1.0),                      # tp_mult
    ))
    print(f"Sweeping {len(grid)} combos on IS only ...")
    rows = []
    for sf, eh, we, rf, tp in grid:
        tr = run_lbo(m15_is, sf, eh, we, rf, tp)
        s = stats(tr, "")
        if s["n"] < 100:        # too few trades to trust
            continue
        rows.append({"stop_frac": sf, "exit_hour": eh, "win_end": we,
                     "rng_filter": rf, "tp_mult": tp, **{k: s[k] for k in
                     ("n", "pf", "wr", "mean_pips", "t")}})
    res = pd.DataFrame(rows).sort_values("t", ascending=False)
    print("\nTop 10 by IS t-stat:")
    print(res.head(10).to_string(index=False,
          formatters={"pf": "{:.2f}".format, "wr": "{:.1f}".format,
                      "mean_pips": "{:+.2f}".format, "t": "{:+.2f}".format}))

    best = res.iloc[0]
    print(f"\n>>> chosen config: stop_frac={best.stop_frac} exit_hour={best.exit_hour} "
          f"win_end={best.win_end} rng_filter={best.rng_filter} tp_mult={best.tp_mult}")

    print("\nSingle OOS run of chosen config:")
    tr_oos = run_lbo(m15_oos, best.stop_frac, int(best.exit_hour), best.win_end,
                     best.rng_filter, best.tp_mult)
    s = stats(tr_oos, "OOS")
    print(f"  OOS  n={s['n']}  PF={s['pf']:.2f}  WR={s['wr']:.1f}%  "
          f"avg={s['mean_pips']:+.2f}p  t={s['t']:+.2f}  total={s['sum_pips']:+.0f}p")

    print("\nYear-by-year (chosen config, full data):")
    tr_all = run_lbo(m15, best.stop_frac, int(best.exit_hour), best.win_end,
                     best.rng_filter, best.tp_mult)
    for yr, grp in tr_all.groupby(tr_all.index.year):
        s = stats(grp, str(yr))
        print(f"  {yr}: n={s['n']:<4} PF={s['pf']:.2f}  WR={s['wr']:.1f}%  "
              f"avg={s['mean_pips']:+.2f}p  total={s['sum_pips']:+.0f}p")

    # honesty check: the untuned baseline on the same OOS window
    tr_base = run_lbo(m15_oos, 0.5, 15, "09:45", "none", None)
    sb = stats(tr_base, "baseline OOS")
    print(f"\nUntuned baseline OOS for reference: n={sb['n']} PF={sb['pf']:.2f} "
          f"avg={sb['mean_pips']:+.2f}p")
    return 0


if __name__ == "__main__":
    sys.exit(main())
