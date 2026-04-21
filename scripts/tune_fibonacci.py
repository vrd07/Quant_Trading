#!/usr/bin/env python3
"""Single-pass parameter sweep for fibonacci_retracement — small focused grid."""

import sys
import logging
import time
import csv
from pathlib import Path
from decimal import Decimal
import itertools
import yaml
import pandas as pd

logging.disable(logging.WARNING)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.fibonacci_retracement_strategy import FibonacciRetracementStrategy
from src.core.types import Symbol


def load_bars(path: str) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")


def make_symbol(cfg: dict) -> Symbol:
    s = cfg.get("symbols", {}).get("XAUUSD", {})
    return Symbol(
        ticker="XAUUSD",
        pip_value=Decimal(str(s.get("pip_value", 0.01))),
        min_lot=Decimal(str(s.get("min_lot", 0.01))),
        max_lot=Decimal(str(s.get("max_lot", 100))),
        lot_step=Decimal(str(s.get("lot_step", 0.01))),
        value_per_lot=Decimal(str(s.get("value_per_lot", 1))),
        min_stops_distance=Decimal(str(s.get("min_stops_distance", 0))),
        leverage=Decimal(str(s.get("leverage", 1))),
    )


def run_one(params, base_cfg, symbol, bars, start, end, capital):
    strat_cfg = dict(base_cfg["strategies"].get("fibonacci_retracement", {}))
    strat_cfg["enabled"] = True
    strat_cfg.update(params)
    strat = FibonacciRetracementStrategy(symbol, strat_cfg)
    engine = BacktestEngine(
        strategy=strat,
        initial_capital=capital,
        risk_config=base_cfg,
        commission_per_trade=Decimal("0"),
        slippage_model="realistic",
        bypass_risk_limits=True,
    )
    return engine.run(bars=bars, start_date=start, end_date=end)


def main():
    cfg_path = project_root / "config/config_live_10000.yaml"
    with open(cfg_path) as f:
        base_cfg = yaml.safe_load(f)

    symbol = make_symbol(base_cfg)
    print("Loading bars...", flush=True)
    bars = load_bars(str(project_root / "data/historical/XAUUSD_5m_real.csv"))
    capital = Decimal("10000")
    start, end = "2025-10-01", "2026-03-27"

    # Hand-picked combos (not full grid) — explore the envelope
    combos = [
        # (name, params)
        ("baseline",      dict(swing_lookback=5, min_swing_atr_mult=2.0, min_rejection_ratio=0.50, adx_min_threshold=20, adx_max_threshold=55, cooldown_bars=8)),
        # Loosen: more trades
        ("loose_swing",   dict(swing_lookback=5, min_swing_atr_mult=1.5, min_rejection_ratio=0.35, adx_min_threshold=15, adx_max_threshold=60, cooldown_bars=5)),
        ("very_loose",    dict(swing_lookback=3, min_swing_atr_mult=1.0, min_rejection_ratio=0.25, adx_min_threshold=10, adx_max_threshold=70, cooldown_bars=3)),
        # Tighten: quality over quantity
        ("tight_quality", dict(swing_lookback=7, min_swing_atr_mult=3.0, min_rejection_ratio=0.65, adx_min_threshold=25, adx_max_threshold=50, cooldown_bars=12)),
        ("very_tight",    dict(swing_lookback=10, min_swing_atr_mult=4.0, min_rejection_ratio=0.75, adx_min_threshold=30, adx_max_threshold=45, cooldown_bars=20)),
        # Reject-quality emphasis
        ("reject_high",   dict(swing_lookback=5, min_swing_atr_mult=2.0, min_rejection_ratio=0.70, adx_min_threshold=20, adx_max_threshold=55, cooldown_bars=8)),
        # ADX band variations
        ("adx_wide",      dict(swing_lookback=5, min_swing_atr_mult=2.0, min_rejection_ratio=0.50, adx_min_threshold=12, adx_max_threshold=70, cooldown_bars=8)),
        ("adx_strong",    dict(swing_lookback=5, min_swing_atr_mult=2.0, min_rejection_ratio=0.50, adx_min_threshold=28, adx_max_threshold=65, cooldown_bars=8)),
        # Shorter cooldown (more trades)
        ("low_cooldown",  dict(swing_lookback=5, min_swing_atr_mult=2.0, min_rejection_ratio=0.50, adx_min_threshold=20, adx_max_threshold=55, cooldown_bars=3)),
        # Trend-chaser: big swings + strong ADX
        ("big_trend",     dict(swing_lookback=5, min_swing_atr_mult=3.5, min_rejection_ratio=0.55, adx_min_threshold=25, adx_max_threshold=70, cooldown_bars=10)),
        # Scalp mode: small fast setups
        ("scalp",         dict(swing_lookback=3, min_swing_atr_mult=1.2, min_rejection_ratio=0.45, adx_min_threshold=15, adx_max_threshold=60, cooldown_bars=4)),
        # Quality trend
        ("qual_trend",    dict(swing_lookback=7, min_swing_atr_mult=2.5, min_rejection_ratio=0.60, adx_min_threshold=22, adx_max_threshold=55, cooldown_bars=10)),
    ]

    out_csv = project_root / "data/backtests/fib_tune_results.csv"
    fieldnames = ["name", "swing_lookback", "min_swing_atr_mult", "min_rejection_ratio",
                  "adx_min_threshold", "adx_max_threshold", "cooldown_bars",
                  "trades", "win_rate", "pf", "sharpe", "dd_pct",
                  "expectancy", "total_ret", "per_day"]
    f = open(out_csv, "w", newline="")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader(); f.flush()

    print(f"Running {len(combos)} configs. Out: {out_csv}", flush=True)

    for i, (name, params) in enumerate(combos, 1):
        t0 = time.time()
        try:
            r = run_one(params, base_cfg, symbol, bars, start, end, capital)
            per_day = r.total_return / 125.0 if r.total_trades > 0 else 0.0
            row = {
                "name": name,
                **params,
                "trades": r.total_trades,
                "win_rate": round(r.win_rate, 1),
                "pf": round(r.profit_factor, 2),
                "sharpe": round(r.sharpe_ratio, 2),
                "dd_pct": round(r.max_drawdown_pct, 1),
                "expectancy": round(r.expectancy, 2),
                "total_ret": round(r.total_return, 0),
                "per_day": round(per_day, 1),
            }
            writer.writerow(row); f.flush()
            dt = time.time() - t0
            print(f"[{i:2d}/{len(combos)}] {name:14s}  trades={r.total_trades:3d}  "
                  f"WR={r.win_rate:4.1f}%  PF={r.profit_factor:4.2f}  "
                  f"DD={r.max_drawdown_pct:5.1f}%  ret=${r.total_return:>7.0f}  "
                  f"$/day={per_day:5.1f}  ({dt:.0f}s)", flush=True)
        except Exception as e:
            print(f"[{i:2d}/{len(combos)}] {name:14s}  FAILED: {e}", flush=True)

    f.close()
    print("\n=== DONE ===", flush=True)


if __name__ == "__main__":
    main()
