#!/usr/bin/env python3
"""Single-pass parameter sweep for fibonacci_retracement strategy."""

import sys
import logging
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


def run_one(params: dict, base_cfg: dict, symbol: Symbol, bars: pd.DataFrame,
            start: str, end: str, capital: Decimal):
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
    result = engine.run(bars=bars, start_date=start, end_date=end)
    return result


def main():
    cfg_path = project_root / "config/config_live_10000.yaml"
    with open(cfg_path) as f:
        base_cfg = yaml.safe_load(f)

    symbol = make_symbol(base_cfg)
    bars = load_bars(str(project_root / "data/historical/XAUUSD_5m_real.csv"))
    capital = Decimal("10000")
    start, end = "2025-10-01", "2026-03-27"

    # Focused grid — single pass, ~48 combos
    grid = {
        "swing_lookback":       [3, 5, 7],
        "min_swing_atr_mult":   [1.5, 2.5, 3.5],
        "min_rejection_ratio":  [0.35, 0.55, 0.70],
        "adx_min_threshold":    [15, 25],
        "cooldown_bars":        [5, 12],
        # Keep these fixed — noise dimensions
        "adx_max_threshold":    [60],
        "min_strength":         [0.0],
    }

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"Running {len(combos)} configurations...")

    rows = []
    for i, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))
        try:
            r = run_one(params, base_cfg, symbol, bars, start, end, capital)
            # ~125 trading days in 6 months
            per_day = r.total_return / 125.0 if r.total_trades > 0 else 0.0
            rows.append({
                **params,
                "trades": r.total_trades,
                "win_rate": round(r.win_rate, 1),
                "pf": round(r.profit_factor, 2),
                "sharpe": round(r.sharpe_ratio, 2),
                "dd_pct": round(r.max_drawdown_pct, 1),
                "expectancy": round(r.expectancy, 2),
                "total_ret": round(r.total_return, 0),
                "per_day": round(per_day, 1),
            })
            print(f"  [{i:2d}/{len(combos)}] trades={r.total_trades:3d} "
                  f"PF={r.profit_factor:4.2f} DD={r.max_drawdown_pct:5.1f}% "
                  f"ret=${r.total_return:>7.0f} $/day={per_day:5.1f}  {params}")
        except Exception as e:
            print(f"  [{i:2d}/{len(combos)}] FAILED: {e}  params={params}")

    df = pd.DataFrame(rows)
    df = df[df["trades"] >= 30]  # Simons: need samples

    out = project_root / "data/backtests/fib_tune_results.csv"
    df.to_csv(out, index=False)
    print(f"\nSaved {len(df)} valid runs to {out}")

    print("\n=== TOP 10 BY PROFIT FACTOR (min 30 trades) ===")
    top_pf = df.sort_values("pf", ascending=False).head(10)
    print(top_pf.to_string(index=False))

    print("\n=== TOP 10 BY $/DAY (min 30 trades, DD < 20%) ===")
    top_day = df[df["dd_pct"].abs() < 20].sort_values("per_day", ascending=False).head(10)
    print(top_day.to_string(index=False))

    print("\n=== TOP 10 BY SHARPE ===")
    print(df.sort_values("sharpe", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
