"""
MTA direction validation backtest.

Question: does multi-timeframe direction (direction_mta over lookbacks
20/80/240 on a single bar series) predict forward returns better than
single-TF direction (the existing 20-bar deadband logic)?

For each evaluation point t we record:
  - single_dir: existing direction_from_returns(lookback=20)
  - mta_consensus + n_aligned/n_total
  - fwd_ret: (close_{t+H} / close_t) - 1, where H is the forward horizon

Then we bucket fwd_ret by predictor + prediction. A useful direction signal:
  - UP   → fwd_ret > 0 on average, hit rate > 50%
  - DOWN → fwd_ret < 0 on average, hit rate > 50%
  - FLAT → fwd_ret near 0, |fwd_ret| smaller than UP/DOWN buckets

MTA is worth wiring in iff:
  - Full-alignment (3/3) UP/DOWN signals produce LARGER conditional fwd_ret
    and HIGHER hit rate than single-TF UP/DOWN.
  - Partial-alignment (2/3) signals sit between full and single-TF — i.e. the
    strength dial is monotone.

Usage:
    python scripts/backtest_mta_direction.py --symbols XAUUSD EURUSD BTCUSD ETHUSD --tf 5m --horizon 12
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

from src.monitoring.atr_forecast import direction_from_returns, direction_mta  # noqa: E402


def _load_bars(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    for path in [
        ROOT / f"data/historical/{symbol}_{tf}_real.csv",
        ROOT / f"data/historical/{symbol}_{tf}.csv",
    ]:
        if path.exists():
            df = pd.read_csv(path, parse_dates=["timestamp"]).set_index("timestamp").sort_index()
            return df[["open", "high", "low", "close", "volume"]].astype(float)
    return None


def _evaluate(
    bars: pd.DataFrame,
    horizon: int,
    warmup: int = 250,
    stride: int = 5,
) -> Optional[pd.DataFrame]:
    n = len(bars)
    if n < warmup + horizon + 10:
        return None
    close = bars["close"]

    rows: List[Dict[str, Any]] = []
    for t in range(warmup, n - horizon, stride):
        c_now = float(close.iloc[t])
        c_fwd = float(close.iloc[t + horizon])
        if c_now <= 0:
            continue
        fwd_ret = c_fwd / c_now - 1.0

        sub = close.iloc[: t + 1]
        single = direction_from_returns(sub, lookback=20, deadband=0.001)
        mta = direction_mta(sub, lookbacks=(20, 80, 240), deadband=0.001)

        rows.append({
            "fwd_ret": fwd_ret,
            "single_dir": single,
            "mta_dir": mta["consensus"],
            "mta_n_aligned": mta["n_aligned"],
            "mta_n_total": mta["n_total"],
            "mta_alignment": f"{mta['n_aligned']}/{mta['n_total']}" if mta["n_total"] else "0/0",
            "mta_score": mta["score"],
        })

    return pd.DataFrame(rows) if rows else None


def _hit_rate(group: pd.Series, pred_dir: str) -> float:
    """Fraction of fwd_ret signs matching the predicted direction."""
    if pred_dir == "UP":
        return float((group > 0).mean())
    if pred_dir == "DOWN":
        return float((group < 0).mean())
    return float((group.abs() < group.abs().median()).mean()) if len(group) else 0.0


def _summarize(df: pd.DataFrame, label: str) -> None:
    print(f"\n--- {label} ---")
    print(f"{'predictor':<22} {'N':>6}  {'mean_fwd':>9}  {'median':>9}  {'hit_rate':>9}")
    print("-" * 70)
    for predictor_col, predictor_label in [
        ("single_dir", "single-TF (20)"),
        ("mta_dir",    "MTA consensus"),
    ]:
        for direction in ("UP", "DOWN", "FLAT"):
            sub = df.loc[df[predictor_col] == direction, "fwd_ret"]
            if len(sub) == 0:
                continue
            print(f"  {predictor_label:<14}  {direction:<5} "
                  f"{len(sub):>6}  {sub.mean():>+9.4%}  "
                  f"{sub.median():>+9.4%}  {_hit_rate(sub, direction):>9.2%}")

    print()
    print("MTA stratified by alignment count:")
    print(f"{'alignment':<14} {'direction':<7} {'N':>6}  {'mean_fwd':>9}  {'hit_rate':>9}")
    print("-" * 70)
    for align in ("3/3", "2/3", "1/3", "0/3"):
        for direction in ("UP", "DOWN", "FLAT"):
            sub = df.loc[
                (df["mta_alignment"] == align) & (df["mta_dir"] == direction), "fwd_ret"
            ]
            if len(sub) == 0:
                continue
            print(f"  {align:<12}  {direction:<5} "
                  f"{len(sub):>6}  {sub.mean():>+9.4%}  {_hit_rate(sub, direction):>9.2%}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD"])
    p.add_argument("--tf", default="5m")
    p.add_argument("--horizon", type=int, default=12,
                   help="Forward bars to measure return over (12×5m = 1h)")
    p.add_argument("--warmup", type=int, default=250)
    p.add_argument("--stride", type=int, default=5)
    args = p.parse_args()

    print(f"Forward horizon: {args.horizon} bars ({args.horizon} × {args.tf})")
    print()

    summary: List[Tuple[str, float, float, float, float]] = []
    for sym in args.symbols:
        bars = _load_bars(sym, args.tf)
        if bars is None or len(bars) < args.warmup + args.horizon + 10:
            print(f"{sym:8s}  no data or too short — skipped")
            continue
        df = _evaluate(bars, args.horizon, warmup=args.warmup, stride=args.stride)
        if df is None or len(df) == 0:
            print(f"{sym:8s}  produced no rows — skipped")
            continue

        _summarize(df, sym)

        # gather summary stats for verdict table
        single_up = df.loc[df["single_dir"] == "UP", "fwd_ret"]
        mta_up_full = df.loc[(df["mta_dir"] == "UP") & (df["mta_alignment"] == "3/3"), "fwd_ret"]
        single_dn = df.loc[df["single_dir"] == "DOWN", "fwd_ret"]
        mta_dn_full = df.loc[(df["mta_dir"] == "DOWN") & (df["mta_alignment"] == "3/3"), "fwd_ret"]

        summary.append((
            sym,
            single_up.mean() if len(single_up) else 0.0,
            mta_up_full.mean() if len(mta_up_full) else 0.0,
            single_dn.mean() if len(single_dn) else 0.0,
            mta_dn_full.mean() if len(mta_dn_full) else 0.0,
        ))

    if summary:
        print()
        print("=" * 92)
        print("Single-TF vs MTA (3/3 aligned) — mean forward return at the chosen horizon")
        print("-" * 92)
        print(f"{'Symbol':<8}  {'single UP':>11}  {'MTA UP 3/3':>11}  "
              f"{'single DOWN':>13}  {'MTA DOWN 3/3':>13}  Verdict")
        print("-" * 92)
        for sym, su, mu, sd, md in summary:
            up_ok   = mu > su and mu > 0
            down_ok = md < sd and md < 0
            if up_ok and down_ok:
                v = "MTA adds value both ways"
            elif up_ok or down_ok:
                v = "MTA helps one side only"
            else:
                v = "MTA does NOT improve over single-TF"
            print(f"{sym:<8}  {su:>+11.4%}  {mu:>+11.4%}  "
                  f"{sd:>+13.4%}  {md:>+13.4%}  {v}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
