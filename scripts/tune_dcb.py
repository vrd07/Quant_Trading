#!/usr/bin/env python3
"""
Descending Channel Breakout — tuner + per-day win analysis.

Runs a small grid search over the highest-impact DCB parameters on the full
XAUUSD 5m history, then re-runs the best config and produces a per-day P&L
breakdown (wins, losses, expectancy, win rate, and the most-profitable days).

Outputs:
    data/backtests/dcb_tuning_grid.csv         — every grid combo with metrics
    data/backtests/dcb_best_trades.csv         — all trades from best config
    data/backtests/dcb_best_daily.csv          — per-day P&L summary
    Console table of per-day wins for the best config
"""

import sys
import logging
from pathlib import Path
from decimal import Decimal
from itertools import product
import pandas as pd
import yaml
from datetime import datetime

logging.disable(logging.WARNING)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.descending_channel_breakout_strategy import (
    DescendingChannelBreakoutStrategy,
)
from src.core.types import Symbol


DATA_FILE = project_root / "data" / "historical" / "XAUUSD_5m_real.csv"
CONFIG_FILE = project_root / "config" / "config_live_10000.yaml"
OUT_DIR = project_root / "data" / "backtests"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_bars() -> pd.DataFrame:
    df = pd.read_csv(DATA_FILE, parse_dates=["timestamp"], index_col="timestamp")
    return df


def make_symbol(config: dict) -> Symbol:
    sc = config.get("symbols", {}).get("XAUUSD", {})
    return Symbol(
        ticker="XAUUSD",
        pip_value=Decimal(str(sc.get("pip_value", 0.01))),
        min_lot=Decimal(str(sc.get("min_lot", 0.01))),
        max_lot=Decimal(str(sc.get("max_lot", 100))),
        lot_step=Decimal(str(sc.get("lot_step", 0.01))),
        value_per_lot=Decimal(str(sc.get("value_per_lot", 1))),
        min_stops_distance=Decimal(str(sc.get("min_stops_distance", 0))),
        leverage=Decimal(str(sc.get("leverage", 1))),
    )


def run_one(config: dict, bars: pd.DataFrame, overrides: dict) -> dict:
    symbol = make_symbol(config)
    cfg = dict(config.get("strategies", {}).get("descending_channel_breakout", {}))
    cfg["enabled"] = True
    cfg.update(overrides)

    strategy = DescendingChannelBreakoutStrategy(symbol, cfg)

    initial_capital = Decimal(str(config.get("account", {}).get("initial_balance", 10000)))

    engine = BacktestEngine(
        strategy=strategy,
        initial_capital=initial_capital,
        risk_config=config,
        commission_per_trade=Decimal("0"),
        slippage_model="realistic",
        bypass_risk_limits=True,
    )
    result = engine.run(bars=bars)

    return {
        "overrides": overrides,
        "trades": result.total_trades,
        "win_rate": result.win_rate,
        "pf": result.profit_factor,
        "sharpe": result.sharpe_ratio,
        "return_pct": result.total_return_pct,
        "return_usd": result.total_return,
        "max_dd_pct": result.max_drawdown_pct,
        "expectancy": result.expectancy,
        "avg_win": result.avg_win,
        "avg_loss": result.avg_loss,
        "trades_df": pd.DataFrame(result.trades),
    }


def score(row: dict) -> float:
    """Composite score: favors positive expectancy, PF, and trade count."""
    if row["trades"] < 20:
        return -1e9
    # Penalize excessive drawdown and reward PF + return
    dd_penalty = max(0, abs(row["max_dd_pct"]) - 5.0) * 0.5
    return (
        row["pf"] * 40
        + row["return_pct"] * 5
        + row["sharpe"] * 20
        - dd_penalty
    )


def daily_breakdown(trades_df: pd.DataFrame) -> pd.DataFrame:
    if trades_df.empty or "timestamp" not in trades_df.columns:
        return pd.DataFrame()
    df = trades_df.copy()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    df["pnl"] = df["pnl"].astype(float)
    daily = (
        df.groupby("date")
        .agg(
            trades=("pnl", "size"),
            wins=("pnl", lambda s: (s > 0).sum()),
            losses=("pnl", lambda s: (s < 0).sum()),
            gross_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            best_trade=("pnl", "max"),
            worst_trade=("pnl", "min"),
        )
        .reset_index()
    )
    daily["win_rate"] = (daily["wins"] / daily["trades"] * 100).round(1)
    daily = daily.sort_values("date")
    return daily


def main():
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    print("Loading XAUUSD 5m data ...")
    bars = load_bars()
    print(f"  {len(bars)} bars | {bars.index.min()} → {bars.index.max()}")

    # ── Parameter grid ────────────────────────────────────────────────────
    # Focused on the highest-impact filters: structure quality (HL count),
    # breakout decisiveness (ATR buffer + body), trend strength (ADX),
    # and signal gate (min_strength). Grid kept small for tractable runtime.
    grid = {
        "min_hl_count": [2, 3],
        "breakout_atr_buffer": [0.30, 0.50],
        "adx_min_threshold": [22, 28],
        "min_strength": [0.60, 0.72],
        "long_only": [False, True],
    }
    keys = list(grid.keys())
    combos = list(product(*[grid[k] for k in keys]))
    print(f"\nGrid search: {len(combos)} combinations")

    results = []
    for idx, combo in enumerate(combos, 1):
        overrides = dict(zip(keys, combo))
        try:
            r = run_one(config, bars, overrides)
        except Exception as e:
            print(f"  [{idx}/{len(combos)}] FAIL: {e}")
            continue
        r_summary = {k: v for k, v in r.items() if k != "trades_df"}
        r_summary["score"] = score(r)
        results.append((r_summary, r["trades_df"]))
        if idx % 10 == 0 or idx == len(combos):
            print(
                f"  [{idx}/{len(combos)}] trades={r['trades']:>3} "
                f"wr={r['win_rate']:>5.1f}% pf={r['pf']:>4.2f} "
                f"ret={r['return_pct']:>+6.2f}% dd={r['max_dd_pct']:>5.2f}% "
                f"score={r_summary['score']:>7.2f}"
            )

    if not results:
        print("No successful runs.")
        return

    # Save full grid
    grid_df = pd.DataFrame([r[0] for r in results])
    grid_df = grid_df.sort_values("score", ascending=False)
    grid_df.to_csv(OUT_DIR / "dcb_tuning_grid.csv", index=False)

    print("\n" + "=" * 80)
    print("TOP 5 CONFIGS BY SCORE")
    print("=" * 80)
    for _, row in grid_df.head(5).iterrows():
        print(
            f"score={row['score']:>7.2f} | trades={int(row['trades']):>3} "
            f"wr={row['win_rate']:>5.1f}% pf={row['pf']:>4.2f} "
            f"ret={row['return_pct']:>+6.2f}% dd={row['max_dd_pct']:>5.2f}% "
            f"| {row['overrides']}"
        )

    # ── Re-run best config for full analysis ──────────────────────────────
    best_summary = grid_df.iloc[0]
    best_overrides = best_summary["overrides"]
    print("\n" + "=" * 80)
    print("BEST CONFIG — DETAILED BREAKDOWN")
    print("=" * 80)
    print(f"  Overrides: {best_overrides}")

    # Find the trades_df for the best config
    best_trades_df = None
    for summary, trades_df in results:
        if summary["overrides"] == best_overrides:
            best_trades_df = trades_df
            break

    if best_trades_df is None or best_trades_df.empty:
        print("No trades in best config.")
        return

    best_trades_df.to_csv(OUT_DIR / "dcb_best_trades.csv", index=False)

    # ── Daily breakdown ───────────────────────────────────────────────────
    daily = daily_breakdown(best_trades_df)
    daily.to_csv(OUT_DIR / "dcb_best_daily.csv", index=False)

    print(f"\n  Total trades: {best_summary['trades']}")
    print(f"  Win rate:     {best_summary['win_rate']:.1f}%")
    print(f"  Profit factor:{best_summary['pf']:.2f}")
    print(f"  Return:       {best_summary['return_pct']:+.2f}% (${best_summary['return_usd']:+.2f})")
    print(f"  Max DD:       {best_summary['max_dd_pct']:.2f}%")
    print(f"  Sharpe:       {best_summary['sharpe']:.2f}")
    print(f"  Expectancy:   ${best_summary['expectancy']:.2f}")
    print(f"  Avg win:      ${best_summary['avg_win']:.2f}")
    print(f"  Avg loss:     ${best_summary['avg_loss']:.2f}")

    # ── Print every day with trades ───────────────────────────────────────
    print("\n" + "=" * 80)
    print("PER-DAY WIN ANALYSIS")
    print("=" * 80)
    print(
        f"{'date':<12} {'trades':>6} {'wins':>5} {'losses':>7} "
        f"{'win%':>6} {'P&L':>10} {'best':>9} {'worst':>9}"
    )
    print("-" * 80)
    for _, row in daily.iterrows():
        marker = "[+]" if row["gross_pnl"] > 0 else ("[-]" if row["gross_pnl"] < 0 else "[0]")
        print(
            f"{marker} {str(row['date']):<8} {int(row['trades']):>6} "
            f"{int(row['wins']):>5} {int(row['losses']):>7} "
            f"{row['win_rate']:>5.1f}% ${row['gross_pnl']:>+9.2f} "
            f"${row['best_trade']:>+8.2f} ${row['worst_trade']:>+8.2f}"
        )

    # ── Summary stats on daily performance ────────────────────────────────
    winning_days = daily[daily["gross_pnl"] > 0]
    losing_days = daily[daily["gross_pnl"] < 0]
    flat_days = daily[daily["gross_pnl"] == 0]

    print("\n" + "=" * 80)
    print("DAILY AGGREGATES")
    print("=" * 80)
    print(f"  Trading days:   {len(daily)}")
    print(f"  Winning days:   {len(winning_days)} ({len(winning_days)/max(len(daily),1)*100:.1f}%)")
    print(f"  Losing days:    {len(losing_days)} ({len(losing_days)/max(len(daily),1)*100:.1f}%)")
    print(f"  Flat days:      {len(flat_days)}")
    if len(winning_days) > 0:
        print(f"  Avg winning day: ${winning_days['gross_pnl'].mean():+.2f}")
        print(f"  Best day:        ${winning_days['gross_pnl'].max():+.2f} on {winning_days.loc[winning_days['gross_pnl'].idxmax(), 'date']}")
    if len(losing_days) > 0:
        print(f"  Avg losing day:  ${losing_days['gross_pnl'].mean():+.2f}")
        print(f"  Worst day:       ${losing_days['gross_pnl'].min():+.2f} on {losing_days.loc[losing_days['gross_pnl'].idxmin(), 'date']}")

    # Top 10 most profitable days
    print("\n" + "=" * 80)
    print("TOP 10 PROFITABLE DAYS")
    print("=" * 80)
    top_days = daily.sort_values("gross_pnl", ascending=False).head(10)
    print(f"{'date':<12} {'trades':>6} {'wins':>5} {'win%':>6} {'P&L':>10}")
    print("-" * 45)
    for _, row in top_days.iterrows():
        print(
            f"{str(row['date']):<12} {int(row['trades']):>6} "
            f"{int(row['wins']):>5} {row['win_rate']:>5.1f}% "
            f"${row['gross_pnl']:>+9.2f}"
        )

    print("\nArtifacts saved:")
    print(f"  {OUT_DIR / 'dcb_tuning_grid.csv'}")
    print(f"  {OUT_DIR / 'dcb_best_trades.csv'}")
    print(f"  {OUT_DIR / 'dcb_best_daily.csv'}")


if __name__ == "__main__":
    main()
