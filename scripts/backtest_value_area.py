"""
80% Rule (Dalton, Market Profile) validation backtest.

Two claims to test on each (prior_day, today) pair:

  (A) The "weak" claim — open outside prior VA ⇒ price re-touches VA today.
        Compared against the unconditional base rate of any random day
        touching the prior VA.

  (B) The Dalton 80% rule — open outside prior VA AND today re-enters VA
        twice ⇒ traverses the entire VA (touches the opposite extreme).
        Reported with the rate when only ONE re-entry has occurred, so we
        can see whether the second re-entry actually adds information.

Notes:
  - Sessions are bucketed by UTC date (the bot's clock). For FX/gold/crypto
    this is the bot's "trading day".
  - Re-entries are detected sequentially (bar by bar) — no lookahead.
  - The traverse target is the FAR side of VA from the opening side
    (open above VAH ⇒ target VAL; open below VAL ⇒ target VAH).

Usage:
    python scripts/backtest_value_area.py --symbols XAUUSD EURUSD BTCUSD ETHUSD --tf 5m
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monitoring.value_area import compute_value_area  # noqa: E402


def _load_bars(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    for path in [
        ROOT / f"data/historical/{symbol}_{tf}_real.csv",
        ROOT / f"data/historical/{symbol}_{tf}.csv",
    ]:
        if path.exists():
            df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            return df[["open", "high", "low", "close", "volume"]].astype(float)
    return None


def _evaluate_symbol(bars: pd.DataFrame, min_bars_per_day: int = 50) -> Optional[Dict[str, Any]]:
    days = sorted({d for d in bars.index.normalize()})
    if len(days) < 3:
        return None

    # Build a dict of date → bars-for-that-day for fast access.
    by_day: Dict[pd.Timestamp, pd.DataFrame] = {
        d: bars[bars.index.normalize() == d] for d in days
    }

    # Stats
    n_days_evaluated = 0
    n_open_outside = 0
    n_open_outside_touches = 0  # touched VA at any point after open
    n_base_rate_touches = 0     # any day where today's range overlapped prior VA
    n_one_reentry = 0
    n_two_reentry = 0
    n_two_reentry_traverse = 0
    n_one_reentry_traverse = 0

    for i in range(1, len(days)):
        prior_bars = by_day[days[i - 1]]
        today_bars = by_day[days[i]]
        if len(prior_bars) < min_bars_per_day or len(today_bars) < min_bars_per_day:
            continue

        va = compute_value_area(prior_bars)
        if va is None:
            continue
        vah = va["vah"]
        val = va["val"]
        if not (vah > val):
            continue

        n_days_evaluated += 1

        # Base rate: did today's full range overlap the prior VA at all?
        today_high = float(today_bars["high"].max())
        today_low = float(today_bars["low"].min())
        if today_high >= val and today_low <= vah:
            n_base_rate_touches += 1

        # Open-outside detection
        open_price = float(today_bars["open"].iloc[0])
        if val <= open_price <= vah:
            continue   # opened inside — not part of the 80%-rule setup

        n_open_outside += 1
        open_above = open_price > vah

        # Did price touch VA at all later in the day?
        touched_va = (today_high >= val) and (today_low <= vah)
        if touched_va:
            n_open_outside_touches += 1

        # Sequential scan to detect re-entries and conditional traverse.
        # close_inside per bar, then count OUTSIDE→INSIDE transitions.
        closes = today_bars["close"].values
        highs = today_bars["high"].values
        lows = today_bars["low"].values

        inside_flags = (closes >= val) & (closes <= vah)
        reentries = 0
        # The "prior" state for the very first bar is the open's side.
        prev_inside = (val <= open_price <= vah)

        one_re_traversed = False
        two_re_seen = False
        two_re_traversed = False

        for j, is_in in enumerate(inside_flags):
            if is_in and not prev_inside:
                reentries += 1
                # After this bar onward, look forward for traverse to far side.
                far_target = val if open_above else vah
                # Range from j+1 onward
                if j + 1 < len(closes):
                    fwd_high = float(highs[j + 1:].max())
                    fwd_low = float(lows[j + 1:].min())
                    if open_above:
                        traversed = fwd_low <= val
                    else:
                        traversed = fwd_high >= vah
                else:
                    traversed = False

                if reentries == 1 and not one_re_traversed:
                    one_re_traversed = bool(traversed)
                    n_one_reentry += 1
                    if one_re_traversed:
                        n_one_reentry_traverse += 1
                if reentries == 2 and not two_re_seen:
                    two_re_seen = True
                    two_re_traversed = bool(traversed)
                    n_two_reentry += 1
                    if two_re_traversed:
                        n_two_reentry_traverse += 1
                    break

            prev_inside = bool(is_in)

    if n_days_evaluated == 0:
        return None

    def _rate(num: int, den: int) -> float:
        return float(num) / den if den else 0.0

    return {
        "n_days":                 n_days_evaluated,
        "base_rate_touch_pct":    _rate(n_base_rate_touches, n_days_evaluated),
        "n_open_outside":         n_open_outside,
        "open_outside_touch_pct": _rate(n_open_outside_touches, n_open_outside),
        "n_one_reentry":          n_one_reentry,
        "one_reentry_traverse_pct": _rate(n_one_reentry_traverse, n_one_reentry),
        "n_two_reentry":          n_two_reentry,
        "two_reentry_traverse_pct": _rate(n_two_reentry_traverse, n_two_reentry),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"])
    p.add_argument("--tf", default="5m")
    p.add_argument("--min-bars-per-day", type=int, default=50)
    args = p.parse_args()

    results: List[Tuple[str, Dict[str, Any]]] = []
    for sym in args.symbols:
        bars = _load_bars(sym, args.tf)
        if bars is None:
            print(f"{sym:8s}  no data — skipped")
            continue
        m = _evaluate_symbol(bars, min_bars_per_day=args.min_bars_per_day)
        if m is None:
            print(f"{sym:8s}  too few sessions — skipped")
            continue
        results.append((sym, m))

    if not results:
        return 1

    print()
    print("=" * 96)
    print(f"{'Symbol':<8} {'days':>5}  {'base touch%':>11}  {'open-out touch%':>15}  "
          f"{'1re→traverse%':>14}  {'2re→traverse%':>14}")
    print("-" * 96)
    for sym, m in results:
        print(
            f"{sym:<8} {m['n_days']:>5}  "
            f"{m['base_rate_touch_pct']:>11.2%}  "
            f"{m['open_outside_touch_pct']:>15.2%}  "
            f"{m['one_reentry_traverse_pct']:>14.2%}  "
            f"{m['two_reentry_traverse_pct']:>14.2%}"
        )
    print()
    print(f"{'Symbol':<8}  {'N open-out':>10}  {'N 1-reentry':>11}  {'N 2-reentry':>11}")
    print("-" * 96)
    for sym, m in results:
        print(f"{sym:<8}  {m['n_open_outside']:>10}  {m['n_one_reentry']:>11}  {m['n_two_reentry']:>11}")

    print()
    print("Verdict (claim A: open-outside touch%  vs  base-rate touch%):")
    for sym, m in results:
        lift_a = m["open_outside_touch_pct"] - m["base_rate_touch_pct"]
        print(f"  {sym:<8}  lift = {lift_a:+.2%}  ({'helpful' if lift_a > 0.02 else 'noise' if abs(lift_a) <= 0.02 else 'NEGATIVE'})")

    print()
    print("Verdict (claim B: 2-reentry traverse%  vs  1-reentry traverse%):")
    for sym, m in results:
        delta = m["two_reentry_traverse_pct"] - m["one_reentry_traverse_pct"]
        rate = m["two_reentry_traverse_pct"]
        if rate >= 0.75:
            verdict = "STRONG — second re-entry implies traverse ≥ 75%"
        elif rate >= 0.60 and delta > 0.05:
            verdict = "MODEST — second re-entry adds signal"
        elif delta <= 0.0:
            verdict = "FAILS — second re-entry does not improve over first"
        else:
            verdict = "WEAK — second re-entry adds little"
        print(f"  {sym:<8}  rate2={rate:.2%}  rate1→2 lift={delta:+.2%}  {verdict}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
