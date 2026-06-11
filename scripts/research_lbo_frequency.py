#!/usr/bin/env python3
"""
USDJPY london_breakout — trade-FREQUENCY tuning research.

Question (2026-06-12): can the strategy take more trades without killing
the edge? The shipped config caps at one trade per day (first 15m close
beyond the Asia range, 07:00-09:59 UTC entry window).

Levers tested here, same harness as research_usdjpy_lbo.py (15m Dukascopy,
1.7 pip round-trip cost, IS 2024-01..2025-09 / OOS 2025-10..2026-06):

  A  baseline          shipped mechanics (one trade/day, win 07:00-09:45)
  B  wide window       entry window extended to 11:45
  C  re-entry          after a stop-out, take the NEXT close beyond either
                       side of the range (max 2 trades/day)
  D  reverse-on-stop   after a stop-out, immediately enter the opposite
                       direction at next bar open (failed-break reversal),
                       stop = stop_frac x range
  E  B+C combined      wide window + re-entry

NOT retested (already dead): other symbols (GBPUSD/AUDUSD PF 0.55-0.88),
TP capping (kills edge), NY-session continuation (research_gbpusd.py).
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research_fx_majors import load, resample, stats, IS_END  # noqa: E402

COST = 0.017   # round-trip price units (1.7 pips)
PIP = 0.01
STOP_FRAC = 0.5
EXIT_HOUR = 15


def _manage(later: pd.DataFrame, entry: float, side: int, stop: float):
    """Walk bars until SL or time exit. Returns (pnl, exit_idx or None=time)."""
    for j, (_, b) in enumerate(later.iterrows()):
        if (side == 1 and b.low <= stop) or (side == -1 and b.high >= stop):
            return (stop - entry) * side, j
    return (later.iloc[-1].close - entry) * side, None


def run_variant(m15: pd.DataFrame, win_end: str, max_entries: int,
                reverse_on_stop: bool) -> pd.Series:
    out = {}
    for day, g in m15.groupby(m15.index.date):
        asia = g.between_time("00:00", "06:59")
        if len(asia) < 12:
            continue
        hi, lo = float(asia.high.max()), float(asia.low.min())
        rng = hi - lo
        if rng <= 0:
            continue
        window = g.between_time("07:00", win_end)
        exit_w = g[(g.index.hour >= int(win_end[:2])) & (g.index.hour <= EXIT_HOUR)]
        if window.empty:
            continue

        entries = 0
        i = 0
        win_bars = list(window.iterrows())
        while i < len(win_bars) and entries < max_entries:
            ts, bar = win_bars[i]
            side = 1 if bar.close > hi else (-1 if bar.close < lo else 0)
            if side == 0:
                i += 1
                continue
            later = pd.concat([window.iloc[i + 1:],
                               exit_w[exit_w.index > window.index[-1]]])
            if later.empty:
                break
            entry = float(later.iloc[0].open)
            stop = entry - side * STOP_FRAC * rng
            pnl, exit_j = _manage(later, entry, side, stop)
            out[ts] = (pnl - COST) / PIP
            entries += 1

            if exit_j is None:        # time exit — day is over
                break

            if reverse_on_stop and entries < max_entries:
                rev = later.iloc[exit_j + 1:]
                if not rev.empty:
                    r_entry = float(rev.iloc[0].open)
                    r_side = -side
                    r_stop = r_entry - r_side * STOP_FRAC * rng
                    r_pnl, _ = _manage(rev, r_entry, r_side, r_stop)
                    out[rev.index[0]] = (r_pnl - COST) / PIP
                    entries += 1
                break

            # re-entry mode: resume scanning window bars after the exit bar
            exit_ts = later.index[exit_j]
            while i < len(win_bars) and win_bars[i][0] <= exit_ts:
                i += 1
    return pd.Series(out, dtype=float).sort_index()


def report(name: str, tr_is: pd.Series, tr_oos: pd.Series, tr_all: pd.Series):
    si, so = stats(tr_is, "IS"), stats(tr_oos, "OOS")
    yrs = []
    for yr, grp in tr_all.groupby(tr_all.index.year):
        s = stats(grp, str(yr))
        yrs.append(f"{yr}:{s['pf']:.2f}({s['n']})")
    print(f"{name:<18} IS  n={si['n']:<4} PF={si['pf']:.2f} t={si['t']:+.2f} | "
          f"OOS n={so['n']:<4} PF={so['pf']:.2f} t={so['t']:+.2f} "
          f"avg={so['mean_pips']:+.2f}p | yearly PF(n): {'  '.join(yrs)}")


def main() -> int:
    df = load("USDJPY")
    m15 = resample(df, "15min")
    m15_is = m15[m15.index <= IS_END]
    m15_oos = m15[m15.index > IS_END]

    variants = [
        ("A baseline",       "09:45", 1, False),
        ("B wide window",    "11:45", 1, False),
        ("C re-entry x2",    "09:45", 2, False),
        ("D reverse-on-stop","09:45", 2, True),
        ("E wide + re-entry","11:45", 2, False),
    ]
    print(f"USDJPY LBO frequency variants — stop_frac={STOP_FRAC}, "
          f"exit_hour={EXIT_HOUR}, cost={COST/PIP:.1f}p\n")
    for name, we, mx, rev in variants:
        tr_is = run_variant(m15_is, we, mx, rev)
        tr_oos = run_variant(m15_oos, we, mx, rev)
        tr_all = run_variant(m15, we, mx, rev)
        report(name, tr_is, tr_oos, tr_all)

    # isolate the marginal trades of C: do SECOND entries alone make money?
    print("\nMarginal (2nd-entry-only) P&L for C and D:")
    for name, we, mx, rev in [("C 2nd entries", "09:45", 2, False),
                              ("D reversals",   "09:45", 2, True)]:
        base = run_variant(m15, we, 1, False)
        full = run_variant(m15, we, mx, rev)
        marg = full[~full.index.isin(base.index)]
        s = stats(marg, name)
        if s["n"]:
            print(f"  {name:<16} n={s['n']:<4} PF={s['pf']:.2f} "
                  f"WR={s['wr']:.1f}% avg={s['mean_pips']:+.2f}p t={s['t']:+.2f}")
        else:
            print(f"  {name:<16} n=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
