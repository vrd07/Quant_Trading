#!/usr/bin/env python3
"""
DCB frequency tuner — find params that maximize trade count while staying profitable.
Targets levers that gate trade frequency: EMA period, channel slope, swing period,
cooldown, strength threshold, session hours.
"""

import sys, os, logging
os.environ["PYTHONUNBUFFERED"] = "1"
logging.disable(logging.WARNING)

from pathlib import Path
from decimal import Decimal
from itertools import product
import pandas as pd
import yaml

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.descending_channel_breakout_strategy import DescendingChannelBreakoutStrategy
from src.core.types import Symbol

DATA_FILE = project_root / "data" / "historical" / "XAUUSD_5m_real.csv"
CONFIG_FILE = project_root / "config" / "config_live_10000.yaml"

EXPANDED_SESSIONS = [3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 22, 23]

def load_bars():
    return pd.read_csv(DATA_FILE, parse_dates=["timestamp"], index_col="timestamp")

def make_symbol(config):
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

def run_one(config, bars, overrides):
    symbol = make_symbol(config)
    cfg = dict(config.get("strategies", {}).get("descending_channel_breakout", {}))
    cfg["enabled"] = True
    cfg.update(overrides)
    strategy = DescendingChannelBreakoutStrategy(symbol, cfg)
    capital = Decimal(str(config.get("account", {}).get("initial_balance", 10000)))
    engine = BacktestEngine(
        strategy=strategy, initial_capital=capital, risk_config=config,
        commission_per_trade=Decimal("0"), slippage_model="realistic",
        bypass_risk_limits=True,
    )
    r = engine.run(bars=bars)
    return {
        "trades": r.total_trades, "win_rate": r.win_rate, "pf": r.profit_factor,
        "return_pct": r.total_return_pct, "return_usd": r.total_return,
        "max_dd_pct": r.max_drawdown_pct, "sharpe": r.sharpe_ratio,
        "expectancy": r.expectancy, "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "trades_list": r.trades,
    }


def daily_breakdown(trades):
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    df["pnl"] = df["pnl"].astype(float)
    daily = df.groupby("date").agg(
        trades=("pnl", "size"), wins=("pnl", lambda s: (s > 0).sum()),
        losses=("pnl", lambda s: (s < 0).sum()), gross_pnl=("pnl", "sum"),
        best_trade=("pnl", "max"), worst_trade=("pnl", "min"),
    ).reset_index()
    daily["win_rate"] = (daily["wins"] / daily["trades"] * 100).round(1)
    return daily.sort_values("date")


def main():
    with open(CONFIG_FILE) as f:
        config = yaml.safe_load(f)

    print("Loading data ...")
    bars = load_bars()
    print(f"  {len(bars)} bars | {bars.index.min()} → {bars.index.max()}")

    # Frequency-focused grid — levers that unlock more trades
    grid = {
        "long_ema_period": [100, 200],
        "channel_slope_max": [-0.0003, -0.001],
        "swing_period": [7, 10],
        "min_strength": [0.45, 0.55],
        "cooldown_bars": [4, 8],
    }
    keys = list(grid.keys())
    combos = list(product(*[grid[k] for k in keys]))
    print(f"\nGrid: {len(combos)} combos")

    results = []
    for idx, combo in enumerate(combos, 1):
        overrides = dict(zip(keys, combo))
        overrides["session_hours"] = EXPANDED_SESSIONS
        try:
            r = run_one(config, bars, overrides)
        except Exception as e:
            print(f"  [{idx}/{len(combos)}] FAIL: {e}")
            continue
        r["overrides"] = overrides
        results.append(r)
        if idx % 10 == 0 or idx == len(combos):
            print(
                f"  [{idx}/{len(combos)}] trades={r['trades']:>3} "
                f"wr={r['win_rate']:>5.1f}% pf={r['pf']:>5.2f} "
                f"ret={r['return_pct']:>+7.2f}% dd={r['max_dd_pct']:>5.2f}%"
            )

    if not results:
        print("No results.")
        return

    # Sort by: profitable first (PF > 1.0), then most trades
    profitable = [r for r in results if r["pf"] >= 1.0 and r["trades"] >= 10]
    profitable.sort(key=lambda r: (-r["trades"], -r["pf"]))

    if not profitable:
        print("\nNo profitable configs found. Showing highest-trade configs:")
        results.sort(key=lambda r: -r["trades"])
        profitable = results[:5]

    print("\n" + "=" * 90)
    print("TOP 10 PROFITABLE CONFIGS BY TRADE COUNT")
    print("=" * 90)
    for r in profitable[:10]:
        o = r["overrides"]
        print(
            f"  trades={r['trades']:>3} wr={r['win_rate']:>5.1f}% "
            f"pf={r['pf']:>5.2f} ret={r['return_pct']:>+7.2f}% "
            f"dd={r['max_dd_pct']:>5.2f}% exp=${r['expectancy']:>+6.2f} "
            f"| ema={o['long_ema_period']} slope={o['channel_slope_max']} "
            f"lb={o['channel_lookback']} sw={o['swing_period']} "
            f"str={o['min_strength']} cd={o['cooldown_bars']}"
        )

    # Pick: most trades with PF >= 1.2
    good = [r for r in profitable if r["pf"] >= 1.2]
    if good:
        best = max(good, key=lambda r: r["trades"])
    else:
        best = profitable[0] if profitable else results[0]

    print("\n" + "=" * 90)
    print("RECOMMENDED CONFIG (max trades with PF >= 1.2)")
    print("=" * 90)
    o = best["overrides"]
    print(f"  Trades:       {best['trades']}")
    print(f"  Win rate:     {best['win_rate']:.1f}%")
    print(f"  Profit factor:{best['pf']:.2f}")
    print(f"  Return:       {best['return_pct']:+.2f}% (${best['return_usd']:+.2f})")
    print(f"  Max DD:       {best['max_dd_pct']:.2f}%")
    print(f"  Expectancy:   ${best['expectancy']:.2f}")
    print(f"  Avg win:      ${best['avg_win']:.2f}")
    print(f"  Avg loss:     ${best['avg_loss']:.2f}")
    print(f"\n  Config overrides:")
    for k, v in o.items():
        if k != "session_hours":
            print(f"    {k}: {v}")
    print(f"    session_hours: {o['session_hours']}")

    # Daily breakdown for best
    daily = daily_breakdown(best["trades_list"])
    if not daily.empty:
        print("\n" + "=" * 90)
        print("PER-DAY WIN ANALYSIS (RECOMMENDED CONFIG)")
        print("=" * 90)
        print(
            f"{'date':<12} {'trades':>6} {'wins':>5} {'losses':>7} "
            f"{'win%':>6} {'P&L':>10} {'best':>9} {'worst':>9}"
        )
        print("-" * 80)
        for _, row in daily.iterrows():
            m = "[+]" if row["gross_pnl"] > 0 else ("[-]" if row["gross_pnl"] < 0 else "[0]")
            print(
                f"{m} {str(row['date']):<8} {int(row['trades']):>6} "
                f"{int(row['wins']):>5} {int(row['losses']):>7} "
                f"{row['win_rate']:>5.1f}% ${row['gross_pnl']:>+9.2f} "
                f"${row['best_trade']:>+8.2f} ${row['worst_trade']:>+8.2f}"
            )

        winning_days = daily[daily["gross_pnl"] > 0]
        losing_days = daily[daily["gross_pnl"] < 0]
        print(f"\n  Trading days: {len(daily)} | Win: {len(winning_days)} | Lose: {len(losing_days)}")
        if len(winning_days) > 0:
            print(f"  Avg winning day: ${winning_days['gross_pnl'].mean():+.2f} | Best: ${winning_days['gross_pnl'].max():+.2f}")
        if len(losing_days) > 0:
            print(f"  Avg losing day:  ${losing_days['gross_pnl'].mean():+.2f} | Worst: ${losing_days['gross_pnl'].min():+.2f}")

    # Save
    out = project_root / "data" / "backtests"
    grid_df = pd.DataFrame([{k: v for k, v in r.items() if k != "trades_list"} for r in results])
    grid_df.to_csv(out / "dcb_freq_grid.csv", index=False)
    if best["trades_list"]:
        pd.DataFrame(best["trades_list"]).to_csv(out / "dcb_freq_best_trades.csv", index=False)
    if not daily.empty:
        daily.to_csv(out / "dcb_freq_best_daily.csv", index=False)

    print(f"\nSaved: dcb_freq_grid.csv, dcb_freq_best_trades.csv, dcb_freq_best_daily.csv")


if __name__ == "__main__":
    main()
