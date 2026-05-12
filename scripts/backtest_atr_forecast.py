"""
Forecast-accuracy backtest for src/monitoring/atr_forecast.compute_forecast.

This is NOT a strategy P&L backtest — the forecast is display-only and does
not affect sizing or signals, so the 8-gate spec in backtest.md does not apply.

The forecaster emits a categorical `vol_outlook ∈ {RISING, STABLE, FALLING}`
because magnitude prediction cannot beat naive persistence on this data (ATR%
is too autocorrelated). The grade we care about is therefore classification,
not regression.

Ground truth for each forecast point t:
    next_atr / current_atr > 1.05  →  RISING
    next_atr / current_atr < 0.95  →  FALLING
    else                            →  STABLE

Reported per symbol:
    - Overall accuracy
    - Per-class precision / recall (so we know if e.g. RISING is reliable
      even when STABLE drowns out the support)
    - Confusion matrix
    - Base-rate baseline (always-predict-majority-class accuracy)

News events cannot be reconstructed point-in-time from bar data alone, so
events=[] is passed throughout. The news_pressure feature is therefore zero
in this backtest — production output near real events is a separate question
the bars-only backtest can't answer.

Usage:
    python scripts/backtest_atr_forecast.py --symbols XAUUSD EURUSD BTCUSD ETHUSD --tf 5m
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Add repo root to sys.path so we can import src.* when invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.monitoring.atr_forecast import compute_forecast, wilder_atr  # noqa: E402


def _load_bars(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    candidates = [
        ROOT / f"data/historical/{symbol}_{tf}_real.csv",
        ROOT / f"data/historical/{symbol}_{tf}.csv",
    ]
    for path in candidates:
        if path.exists():
            df = pd.read_csv(path, parse_dates=["timestamp"])
            df = df.set_index("timestamp").sort_index()
            df = df[["open", "high", "low", "close", "volume"]].astype(float)
            return df
    return None


_CLASSES = ("RISING", "STABLE", "FALLING")


def _truth_label(cur: float, nxt: float, thresh: float = 0.05) -> str:
    if cur <= 0:
        return "STABLE"
    r = nxt / cur - 1.0
    if r > thresh:
        return "RISING"
    if r < -thresh:
        return "FALLING"
    return "STABLE"


def _evaluate_symbol(
    bars: pd.DataFrame,
    symbol: str,
    window: int = 1000,
    eval_stride: int = 20,
    warmup: int = 1000,
) -> Optional[Dict[str, Any]]:
    """Walk-forward classification accuracy for vol_outlook."""
    n = len(bars)
    if n < warmup + 100:
        return None

    realized_atr = (
        wilder_atr(bars["high"], bars["low"], bars["close"], period=14)
        / bars["close"] * 100.0
    )

    eval_points = list(range(warmup, n - 1, eval_stride))
    if not eval_points:
        return None

    pred: List[str] = []
    truth: List[str] = []
    for t in eval_points:
        window_bars = bars.iloc[max(0, t - window + 1): t + 1]
        fc = compute_forecast(window_bars, symbol, upcoming_events=[])
        if fc is None:
            continue
        cur = float(realized_atr.iloc[t])
        nxt = float(realized_atr.iloc[t + 1])
        if not (np.isfinite(cur) and np.isfinite(nxt)) or cur <= 0:
            continue
        pred.append(fc.vol_outlook)
        truth.append(_truth_label(cur, nxt))

    if not pred:
        return None

    pred_s = pd.Series(pred)
    truth_s = pd.Series(truth)

    overall_acc = float((pred_s == truth_s).mean())
    majority = truth_s.value_counts().idxmax()
    base_rate = float((truth_s == majority).mean())

    # Confusion matrix as nested dict: confusion[truth][pred]
    confusion: Dict[str, Dict[str, int]] = {c: {p: 0 for p in _CLASSES} for c in _CLASSES}
    for t_lbl, p_lbl in zip(truth, pred):
        confusion[t_lbl][p_lbl] += 1

    # Per-class precision/recall
    per_class: Dict[str, Dict[str, float]] = {}
    for c in _CLASSES:
        tp = confusion[c][c]
        fp = sum(confusion[other][c] for other in _CLASSES if other != c)
        fn = sum(confusion[c][other] for other in _CLASSES if other != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        per_class[c] = {
            "precision": prec,
            "recall": rec,
            "support": int((truth_s == c).sum()),
        }

    return {
        "n_forecasts": len(pred),
        "accuracy": overall_acc,
        "majority_class": majority,
        "base_rate": base_rate,
        "lift_over_base_rate": overall_acc - base_rate,
        "per_class": per_class,
        "confusion": confusion,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"])
    p.add_argument("--tf", default="5m")
    p.add_argument("--window", type=int, default=1000,
                   help="Bars supplied to compute_forecast at each eval point")
    p.add_argument("--stride", type=int, default=20,
                   help="Evaluate every N bars (smaller = slower, more samples)")
    p.add_argument("--warmup", type=int, default=1000)
    args = p.parse_args()

    results: List[Tuple[str, Dict[str, float]]] = []
    for sym in args.symbols:
        bars = _load_bars(sym, args.tf)
        if bars is None or len(bars) < args.warmup + 100:
            print(f"{sym:8s}  no data or too short — skipped")
            continue
        print(f"{sym:8s}  evaluating {len(bars)} bars, "
              f"~{(len(bars) - args.warmup) // args.stride} forecast points…")
        m = _evaluate_symbol(
            bars, sym,
            window=args.window, eval_stride=args.stride, warmup=args.warmup,
        )
        if m is None:
            print(f"{sym:8s}  evaluation produced no rows — skipped")
            continue
        results.append((sym, m))

    if not results:
        print("\nNo results — check data paths.")
        return 1

    print()
    print("=" * 86)
    print(f"{'Symbol':<8} {'N':>6}  {'Accuracy':>9}  {'BaseRate':>9}  {'Lift':>8}  Majority")
    print("-" * 86)
    for sym, m in results:
        print(
            f"{sym:<8} {m['n_forecasts']:>6}  "
            f"{m['accuracy']:>9.2%}  "
            f"{m['base_rate']:>9.2%}  "
            f"{m['lift_over_base_rate']:>+8.2%}  "
            f"{m['majority_class']}"
        )
    print()
    print("Per-class precision / recall  (support = #ground-truth observations of that class)")
    print("-" * 86)
    for sym, m in results:
        print(f"  {sym}")
        for c in _CLASSES:
            pc = m["per_class"][c]
            print(
                f"     {c:<8}  precision={pc['precision']:>6.2%}  "
                f"recall={pc['recall']:>6.2%}  support={pc['support']}"
            )
    print()
    print("Confusion matrix (rows = truth, cols = prediction)")
    print("-" * 86)
    for sym, m in results:
        cm = m["confusion"]
        print(f"  {sym}")
        header = "          " + "".join(f"{p:>10}" for p in _CLASSES)
        print(header)
        for truth_class in _CLASSES:
            row = f"   {truth_class:<7} " + "".join(
                f"{cm[truth_class][p]:>10}" for p in _CLASSES
            )
            print(row)
    print()

    print("Verdict:")
    for sym, m in results:
        lift = m["lift_over_base_rate"]
        rising_p = m["per_class"]["RISING"]["precision"]
        falling_p = m["per_class"]["FALLING"]["precision"]
        if lift > 0.02 and (rising_p > 0.50 or falling_p > 0.50):
            verdict = "ADDS VALUE — beats base-rate AND non-STABLE classes are >50% precise"
        elif lift > 0.0:
            verdict = "marginal lift over base rate — display, but don't trust class-by-class"
        else:
            verdict = "NO LIFT vs always-predict-majority — kill the column"
        print(f"  {sym:<8}  lift={lift:+.2%}  RISING_p={rising_p:.2%}  FALLING_p={falling_p:.2%}   {verdict}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
