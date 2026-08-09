#!/usr/bin/env python3
"""Why does WaveletCycleStrategy's MEAN_REVERT branch never fire?

Reproducer for the comments in `src/strategies/wavelet_cycle_strategy.py._side`
and `tests/unit/test_wavelet_cycle_strategy.py::TestMeanRevertEntries`.

The published walk-forward took 0 MEAN_REVERT trades, which left `entry_dev_atr`
(read only by that branch) as a dead grid axis and half the 12-cell grid a
duplicate. This instruments the funnel stage by stage to say WHY, and tests the
two obvious bug hypotheses -- both of which it refutes:

  H_sign   the branch has the phase/deviation convention inverted relative to
           the TREND branch, making its conditions contradictory.
           REFUTED: phase and deviation sign are INDEPENDENT (0.47 / 0.53).
  H_scale  `entry_dev_atr` is denominated in ATR but `deviation` is a
           delay-compensated residual on a much smaller scale, so the threshold
           sits outside the distribution.
           REFUTED: |dev|/ATR median 0.27, and 21.9% of bars clear 0.5.

What is actually true: two independently rare gates multiplied together.

Reads the train slice only; the frozen OOS slice is never touched.

    python scripts/research_wavelet_meanrevert_funnel.py
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from src.cycles.cycle_tracker import CycleTracker  # noqa: E402
from src.cycles.regime import RegimeDetector  # noqa: E402
from src.data.indicators import Indicators  # noqa: E402
import research_wavelet_cycle as rwc  # noqa: E402

LOOKBACK = 400          # matches the replay window the research harness uses
STRIDE = 3              # the question is about rates, not exact timing
ENTRY_DEV_ATR = 0.5     # the shipped default


def collect(bars: pd.DataFrame) -> tuple[pd.DataFrame, Counter, int]:
    tracker = CycleTracker.for_asset("gold", min_prominence=4.0)
    regime_det = RegimeDetector(atr_period=14)

    rows: list[dict] = []
    regimes: Counter = Counter()
    for i in range(LOOKBACK, len(bars), STRIDE):
        w = bars.iloc[i - LOOKBACK:i + 1]
        cyc = tracker.update(w["close"])
        reg = regime_det.classify(w)
        regimes[reg.regime.value] += 1
        if not cyc.tradeable or cyc.phase is None:
            continue
        atr = float(Indicators.atr(w, period=14).iloc[-1])
        if not (atr > 0):
            continue
        rows.append(dict(pos=float(cyc.phase.position), dev=float(cyc.deviation),
                         atr=atr, regime=reg.regime.value))
    return pd.DataFrame(rows), regimes, sum(regimes.values())


def main() -> None:
    bars = rwc.slice_split(rwc.load("XAUUSD"), "train")
    print(f"train slice: {len(bars):,} 15m bars", flush=True)

    df, regimes, n_eval = collect(bars)
    print(f"\nevaluated {n_eval:,} bars (stride {STRIDE})")
    print("regime mix: "
          + ", ".join(f"{k} {v / n_eval * 100:.1f}%" for k, v in regimes.items()))
    print(f"cycle-tradeable bars: {len(df):,} ({len(df) / n_eval * 100:.2f}%)")
    if df.empty:
        print("no tradeable bars -- nothing to diagnose")
        return

    print("\n--- funnel: the two gates have to coincide ---")
    print(f"  tradeable cycle                : {len(df):5d}")
    for r in ("TREND", "MEAN_REVERT", "UNCERTAIN"):
        print(f"    ... and regime {r:<12s} : {int((df['regime'] == r).sum()):5d}")

    print("\n--- H_sign: does phase predict the deviation sign? ---")
    for mask, label in [
        ((df["pos"] >= 0.40) & (df["pos"] <= 0.60), "at_trough [0.40,0.60] wants dev<0"),
        ((df["pos"] >= 0.90) | (df["pos"] <= 0.10), "at_peak  [>=.90|<=.10] wants dev>0"),
    ]:
        band = df[mask]
        if len(band):
            print(f"  {label:38s} n={len(band):4d} "
                  f"P(dev<0)={np.mean(band['dev'] < 0):.2f} "
                  f"P(dev>0)={np.mean(band['dev'] > 0):.2f}")
    print("  independent (~0.50/0.50) => phase carries no information about "
          "where price sits vs trend")

    print("\n--- the branch's own conditions, applied two ways ---")
    for label, sub in (("ignoring the regime gate", df),
                       ("MEAN_REVERT regime only", df[df["regime"] == "MEAN_REVERT"])):
        if sub.empty:
            print(f"  {label} (n=0)")
            continue
        thr = ENTRY_DEV_ATR * sub["atr"]
        at_trough = (sub["pos"] >= 0.40) & (sub["pos"] <= 0.60)
        at_peak = (sub["pos"] >= 0.90) | (sub["pos"] <= 0.10)
        buys = int(((sub["dev"] < -thr) & at_trough).sum())
        sells = int(((sub["dev"] > thr) & at_peak).sum())
        print(f"  {label} (n={len(sub)}): would BUY {buys}, would SELL {sells} "
              f"=> {buys + sells} entries ({(buys + sells) / len(sub) * 100:.1f}%)")

    ratio = np.abs(df["dev"]) / df["atr"]
    print("\n--- H_scale: |deviation| / ATR ---")
    print(f"  median {ratio.median():.3f} | p90 {ratio.quantile(0.90):.3f} "
          f"| max {ratio.max():.3f} | share over the {ENTRY_DEV_ATR} threshold "
          f"{np.mean(ratio > ENTRY_DEV_ATR) * 100:.1f}%")

    print("\nConclusion: the conditions are satisfiable; the REGIME GATE is the "
          "bottleneck. Do not loosen it -- H_sign shows the unlocked entries "
          "would be uninformative.")


if __name__ == "__main__":
    main()
