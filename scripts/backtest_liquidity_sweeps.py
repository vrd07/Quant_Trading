"""
Liquidity sweep → reversal backtest (ICT / SMC convention).

For each historical UTC day we:
  1. Determine PDH and PDL from the prior day's high/low.
  2. Determine Asia H / Asia L from today's UTC 00–07 window.
  3. Walk today's bars looking for the FIRST sweep of each level:
        a 'high' level is swept when high > level AND close < level on the
        same bar (wick-out, close-back).
  4. Measure the forward N-bar return from the sweep bar's close.

The ICT claim:
    Sweep of a high level (PDH / Asia H) precedes a downward reversal —
    expected forward return is NEGATIVE.
    Sweep of a low level  (PDL / Asia L) precedes an upward reversal —
    expected forward return is POSITIVE.

To know whether the sweep itself adds signal, we compare the conditional
forward return against the **unconditional next-N-bar return** at the same
bar position in the day (matched-position base rate would be ideal; here
we use the daily unconditional mean for simplicity).

Sample sizes are quite small — only ~1 sweep per day per level — so the
test is most reliable on XAUUSD where we have the most data.

Usage:
    python scripts/backtest_liquidity_sweeps.py --symbols XAUUSD EURUSD BTCUSD ETHUSD --tf 5m --horizon 12
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


def _load_bars(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    for p in [ROOT / f"data/historical/{symbol}_{tf}_real.csv",
              ROOT / f"data/historical/{symbol}_{tf}.csv"]:
        if p.exists():
            df = pd.read_csv(p, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            return df[["open", "high", "low", "close", "volume"]].astype(float)
    return None


def _first_sweep(today_bars: pd.DataFrame, level: float, is_high: bool) -> Optional[int]:
    if not np.isfinite(level) or len(today_bars) == 0:
        return None
    highs = today_bars["high"].values
    lows = today_bars["low"].values
    closes = today_bars["close"].values
    if is_high:
        mask = (highs > level) & (closes < level)
    else:
        mask = (lows < level) & (closes > level)
    if not mask.any():
        return None
    return int(np.argmax(mask))


def _evaluate_symbol(bars: pd.DataFrame, horizon: int) -> Optional[Dict[str, Any]]:
    days = sorted(pd.Series(bars.index.normalize()).unique())
    if len(days) < 3:
        return None

    by_day = {d: bars[bars.index.normalize() == d] for d in days}

    # Per-level accumulators: list of fwd-N-bar returns conditional on sweep.
    fwd: Dict[str, List[float]] = {k: [] for k in ("pdh", "pdl", "asia_h", "asia_l")}
    # Also: unconditional next-N-bar returns at every same-day bar position
    # so we can compute a base-rate mean for comparison.
    unconditional: List[float] = []

    for i in range(1, len(days)):
        prior = by_day[days[i - 1]]
        today = by_day[days[i]]
        if len(prior) < 20 or len(today) < horizon + 2:
            continue
        pdh = float(prior["high"].max())
        pdl = float(prior["low"].min())

        asia_today = today[(today.index.hour >= 0) & (today.index.hour < 7)]
        asia_h = float(asia_today["high"].max()) if len(asia_today) else None
        asia_l = float(asia_today["low"].min()) if len(asia_today) else None

        closes = today["close"].values

        # unconditional sample: pick the mid-day return at each bar idx
        for j in range(len(today) - horizon):
            unconditional.append(float(closes[j + horizon] / closes[j] - 1.0))

        for name, lvl, is_high in [
            ("pdh", pdh, True), ("pdl", pdl, False),
            ("asia_h", asia_h, True), ("asia_l", asia_l, False),
        ]:
            if lvl is None:
                continue
            idx = _first_sweep(today, lvl, is_high)
            if idx is None or idx + horizon >= len(today):
                continue
            ret = float(closes[idx + horizon] / closes[idx] - 1.0)
            fwd[name].append(ret)

    if not unconditional:
        return None

    base_mean = float(np.mean(unconditional))

    def _stats(returns: List[float]) -> Dict[str, float]:
        if not returns:
            return {"n": 0, "mean": 0.0, "hit_rate": 0.0, "lift_vs_base": 0.0}
        arr = np.array(returns)
        return {
            "n": len(arr),
            "mean": float(arr.mean()),
            # Hit rate convention: for HIGH sweeps the expected direction is DOWN, so a
            # "hit" is a negative forward return; symmetric for LOW sweeps.
            "hit_rate": 0.0,
            "lift_vs_base": float(arr.mean() - base_mean),
        }

    out = {
        "base_mean": base_mean,
        "n_unconditional": len(unconditional),
        "levels": {},
    }
    for name in fwd:
        is_high_level = name.endswith("h")
        s = _stats(fwd[name])
        if s["n"]:
            arr = np.array(fwd[name])
            if is_high_level:
                s["hit_rate"] = float((arr < 0).mean())  # expected DOWN after high sweep
            else:
                s["hit_rate"] = float((arr > 0).mean())  # expected UP after low sweep
        s["expected_sign"] = "-" if is_high_level else "+"
        out["levels"][name] = s

    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"])
    p.add_argument("--tf", default="5m")
    p.add_argument("--horizon", type=int, default=12)
    args = p.parse_args()

    print(f"Forward horizon: {args.horizon} bars ({args.horizon} × {args.tf})")
    print()

    rows: List[Tuple[str, Dict[str, Any]]] = []
    for sym in args.symbols:
        bars = _load_bars(sym, args.tf)
        if bars is None:
            print(f"{sym:8s}  no data — skipped")
            continue
        m = _evaluate_symbol(bars, args.horizon)
        if m is None:
            print(f"{sym:8s}  too short — skipped")
            continue
        rows.append((sym, m))

    if not rows:
        return 1

    print("=" * 96)
    print(f"{'Sym':<8} {'Level':<8} {'N sweeps':>9} "
          f"{'mean fwd_ret':>13} {'hit rate':>10} "
          f"{'lift vs base':>13}  base_mean")
    print("-" * 96)
    for sym, m in rows:
        for name in ("pdh", "pdl", "asia_h", "asia_l"):
            s = m["levels"][name]
            print(f"{sym:<8} {name:<8} {s['n']:>9}  "
                  f"{s['mean']:>+12.4%}  {s['hit_rate']:>9.2%}  "
                  f"{s['lift_vs_base']:>+12.4%}  "
                  f"{m['base_mean']:>+8.5%}")
        print()

    print("Verdict (lift = conditional mean - unconditional base mean):")
    for sym, m in rows:
        for name in ("pdh", "pdl", "asia_h", "asia_l"):
            s = m["levels"][name]
            if s["n"] < 20:
                v = f"sample too small (n={s['n']})"
            else:
                expected_sign_neg = name.endswith("h")
                actual_neg = s["mean"] < 0
                if expected_sign_neg == actual_neg and abs(s["mean"]) > abs(m["base_mean"]) * 1.5:
                    v = "REVERSAL CONFIRMED — sweep predicts the expected direction with meaningful magnitude"
                elif expected_sign_neg == actual_neg:
                    v = "weak — direction right, magnitude small"
                else:
                    v = "FAILS — direction is wrong relative to ICT claim"
            print(f"  {sym:<8} {name:<8}  n={s['n']:>3}  mean={s['mean']:+.4%}  {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
