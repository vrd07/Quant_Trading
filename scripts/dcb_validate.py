#!/usr/bin/env python3
"""Single-config DCB validation run with daily breakdown."""
import sys, os, logging
os.environ["PYTHONUNBUFFERED"] = "1"
logging.disable(logging.WARNING)

from pathlib import Path
from decimal import Decimal
import pandas as pd
import yaml

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.descending_channel_breakout_strategy import DescendingChannelBreakoutStrategy
from src.core.types import Symbol

CONFIG_FILE = project_root / "config" / "config_live_10000.yaml"
DATA_FILE = project_root / "data" / "historical" / "XAUUSD_5m_real.csv"

OVERRIDES = {
    "long_ema_period": 200,
    "channel_slope_max": -0.001,
    "swing_period": 10,
    "min_strength": 0.55,
    "cooldown_bars": 8,
    "session_hours": [3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 18, 22, 23],
}

with open(CONFIG_FILE) as f:
    config = yaml.safe_load(f)

bars = pd.read_csv(DATA_FILE, parse_dates=["timestamp"], index_col="timestamp")
print(f"Bars: {len(bars)} | {bars.index.min()} → {bars.index.max()}")

sc = config.get("symbols", {}).get("XAUUSD", {})
symbol = Symbol(
    ticker="XAUUSD",
    pip_value=Decimal(str(sc.get("pip_value", 0.01))),
    min_lot=Decimal(str(sc.get("min_lot", 0.01))),
    max_lot=Decimal(str(sc.get("max_lot", 100))),
    lot_step=Decimal(str(sc.get("lot_step", 0.01))),
    value_per_lot=Decimal(str(sc.get("value_per_lot", 1))),
    min_stops_distance=Decimal(str(sc.get("min_stops_distance", 0))),
    leverage=Decimal(str(sc.get("leverage", 1))),
)

cfg = dict(config.get("strategies", {}).get("descending_channel_breakout", {}))
cfg["enabled"] = True
cfg.update(OVERRIDES)

strategy = DescendingChannelBreakoutStrategy(symbol, cfg)
capital = Decimal(str(config.get("account", {}).get("initial_balance", 10000)))

engine = BacktestEngine(
    strategy=strategy, initial_capital=capital, risk_config=config,
    commission_per_trade=Decimal("0"), slippage_model="realistic",
    bypass_risk_limits=True,
)
r = engine.run(bars=bars)

print("\n" + "=" * 70)
print("VALIDATED CONFIG — DESCENDING CHANNEL BREAKOUT (frequency-tuned)")
print("=" * 70)
print(f"  Trades:       {r.total_trades}")
print(f"  Win rate:     {r.win_rate:.1f}%")
print(f"  Profit factor:{r.profit_factor:.2f}")
print(f"  Return:       {r.total_return_pct:+.2f}% (${r.total_return:+.2f})")
print(f"  Max DD:       {r.max_drawdown_pct:.2f}%")
print(f"  Sharpe:       {r.sharpe_ratio:.2f}")
print(f"  Expectancy:   ${r.expectancy:.2f}")
print(f"  Avg win:      ${r.avg_win:.2f}")
print(f"  Avg loss:     ${r.avg_loss:.2f}")

print(f"\n  Config changes vs defaults:")
for k, v in OVERRIDES.items():
    print(f"    {k}: {v}")

# Daily breakdown
if r.trades:
    df = pd.DataFrame(r.trades)
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    df["pnl"] = df["pnl"].astype(float)
    daily = df.groupby("date").agg(
        trades=("pnl", "size"), wins=("pnl", lambda s: (s > 0).sum()),
        losses=("pnl", lambda s: (s < 0).sum()), gross_pnl=("pnl", "sum"),
        best_trade=("pnl", "max"), worst_trade=("pnl", "min"),
    ).reset_index()
    daily["win_rate"] = (daily["wins"] / daily["trades"] * 100).round(1)
    daily = daily.sort_values("date")

    print("\n" + "=" * 70)
    print("PER-DAY WIN ANALYSIS")
    print("=" * 70)
    print(f"{'date':<12} {'trades':>6} {'wins':>5} {'losses':>7} {'win%':>6} {'P&L':>10} {'best':>9} {'worst':>9}")
    print("-" * 70)
    for _, row in daily.iterrows():
        m = "[+]" if row["gross_pnl"] > 0 else ("[-]" if row["gross_pnl"] < 0 else "[0]")
        print(f"{m} {str(row['date']):<8} {int(row['trades']):>6} {int(row['wins']):>5} {int(row['losses']):>7} {row['win_rate']:>5.1f}% ${row['gross_pnl']:>+9.2f} ${row['best_trade']:>+8.2f} ${row['worst_trade']:>+8.2f}")

    wd = daily[daily["gross_pnl"] > 0]
    ld = daily[daily["gross_pnl"] < 0]
    print(f"\n  Trading days: {len(daily)} | Win: {len(wd)} ({len(wd)/max(len(daily),1)*100:.0f}%) | Lose: {len(ld)} ({len(ld)/max(len(daily),1)*100:.0f}%)")
    if len(wd) > 0:
        print(f"  Avg winning day: ${wd['gross_pnl'].mean():+.2f} | Best: ${wd['gross_pnl'].max():+.2f}")
    if len(ld) > 0:
        print(f"  Avg losing day:  ${ld['gross_pnl'].mean():+.2f} | Worst: ${ld['gross_pnl'].min():+.2f}")

    # Save
    out = project_root / "data" / "backtests"
    pd.DataFrame(r.trades).to_csv(out / "dcb_final_trades.csv", index=False)
    daily.to_csv(out / "dcb_final_daily.csv", index=False)
    print(f"\n  Saved: dcb_final_trades.csv, dcb_final_daily.csv")
