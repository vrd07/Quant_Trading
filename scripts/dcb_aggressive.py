#!/usr/bin/env python3
"""Test aggressive DCB configs targeting daily trade frequency."""
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

with open(CONFIG_FILE) as f:
    config = yaml.safe_load(f)

bars = pd.read_csv(DATA_FILE, parse_dates=["timestamp"], index_col="timestamp")
trading_days = len(bars.index.normalize().unique())
print(f"Bars: {len(bars)} | {bars.index.min()} → {bars.index.max()} | ~{trading_days} trading days")

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

configs_to_test = {
    "A_micro_channels": {
        "channel_lookback": 40,
        "swing_period": 4,
        "min_hl_count": 1,
        "cooldown_bars": 2,
        "channel_slope_max": -0.0002,
        "breakout_atr_buffer": 0.15,
        "min_body_atr_ratio": 0.20,
        "min_strength": 0.40,
        "adx_min_threshold": 18,
        "require_correction_phase": False,
        "session_hours": list(range(24)),
    },
    "B_micro_with_gate": {
        "channel_lookback": 40,
        "swing_period": 4,
        "min_hl_count": 1,
        "cooldown_bars": 2,
        "channel_slope_max": -0.0002,
        "breakout_atr_buffer": 0.15,
        "min_body_atr_ratio": 0.20,
        "min_strength": 0.40,
        "adx_min_threshold": 18,
        "require_correction_phase": True,
        "long_ema_period": 50,
        "session_hours": list(range(24)),
    },
    "C_medium_relaxed": {
        "channel_lookback": 60,
        "swing_period": 5,
        "min_hl_count": 1,
        "cooldown_bars": 3,
        "channel_slope_max": -0.0003,
        "breakout_atr_buffer": 0.20,
        "min_body_atr_ratio": 0.25,
        "min_strength": 0.45,
        "adx_min_threshold": 20,
        "require_correction_phase": False,
        "session_hours": list(range(24)),
    },
    "D_medium_ema50_gate": {
        "channel_lookback": 60,
        "swing_period": 5,
        "min_hl_count": 1,
        "cooldown_bars": 3,
        "channel_slope_max": -0.0003,
        "breakout_atr_buffer": 0.20,
        "min_body_atr_ratio": 0.25,
        "min_strength": 0.45,
        "adx_min_threshold": 20,
        "require_correction_phase": True,
        "long_ema_period": 50,
        "session_hours": list(range(24)),
    },
    "E_ultra_aggressive": {
        "channel_lookback": 30,
        "swing_period": 3,
        "min_hl_count": 1,
        "cooldown_bars": 1,
        "channel_slope_max": -0.0001,
        "breakout_atr_buffer": 0.10,
        "min_body_atr_ratio": 0.15,
        "min_strength": 0.35,
        "adx_min_threshold": 15,
        "require_correction_phase": False,
        "session_hours": list(range(24)),
        "ema_trend_period": 20,
    },
}

capital = Decimal(str(config.get("account", {}).get("initial_balance", 10000)))

results = []
for name, overrides in configs_to_test.items():
    print(f"\n>>> {name} ...")
    cfg = dict(config.get("strategies", {}).get("descending_channel_breakout", {}))
    cfg["enabled"] = True
    cfg.update(overrides)

    strategy = DescendingChannelBreakoutStrategy(symbol, cfg)
    engine = BacktestEngine(
        strategy=strategy, initial_capital=capital, risk_config=config,
        commission_per_trade=Decimal("0"), slippage_model="realistic",
        bypass_risk_limits=True,
    )
    r = engine.run(bars=bars)

    trades_per_day = r.total_trades / max(trading_days, 1)
    print(
        f"  trades={r.total_trades} ({trades_per_day:.1f}/day) "
        f"wr={r.win_rate:.1f}% pf={r.profit_factor:.2f} "
        f"ret={r.total_return_pct:+.2f}% dd={r.max_drawdown_pct:.2f}% "
        f"exp=${r.expectancy:.2f}"
    )
    results.append({
        "name": name, "trades": r.total_trades, "per_day": trades_per_day,
        "win_rate": r.win_rate, "pf": r.profit_factor,
        "return_pct": r.total_return_pct, "return_usd": r.total_return,
        "max_dd_pct": r.max_drawdown_pct, "expectancy": r.expectancy,
        "avg_win": r.avg_win, "avg_loss": r.avg_loss,
        "trades_list": r.trades, "overrides": overrides,
    })

print("\n" + "=" * 90)
print("COMPARISON TABLE")
print("=" * 90)
print(f"{'Config':<25} {'Trades':>7} {'/day':>5} {'WR%':>6} {'PF':>6} {'Ret%':>8} {'DD%':>7} {'Exp$':>7}")
print("-" * 90)
for r in results:
    print(
        f"{r['name']:<25} {r['trades']:>7} {r['per_day']:>5.1f} "
        f"{r['win_rate']:>5.1f}% {r['pf']:>5.2f} {r['return_pct']:>+7.2f}% "
        f"{r['max_dd_pct']:>6.2f}% ${r['expectancy']:>+6.2f}"
    )

# Pick best profitable config with highest trades/day
profitable = [r for r in results if r["pf"] >= 1.0]
if profitable:
    best = max(profitable, key=lambda r: r["trades"])
    print(f"\n>>> BEST: {best['name']} — {best['trades']} trades ({best['per_day']:.1f}/day), PF {best['pf']:.2f}")

    # Daily breakdown
    if best["trades_list"]:
        df = pd.DataFrame(best["trades_list"])
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date
        df["pnl"] = df["pnl"].astype(float)
        daily = df.groupby("date").agg(
            trades=("pnl", "size"), wins=("pnl", lambda s: (s > 0).sum()),
            losses=("pnl", lambda s: (s < 0).sum()), gross_pnl=("pnl", "sum"),
            best_trade=("pnl", "max"), worst_trade=("pnl", "min"),
        ).reset_index()
        daily["win_rate"] = (daily["wins"] / daily["trades"] * 100).round(1)

        print(f"\n  Days with trades: {len(daily)} / {trading_days} ({len(daily)/trading_days*100:.0f}%)")
        print(f"  Avg trades/active day: {daily['trades'].mean():.1f}")
        multi = daily[daily["trades"] >= 2]
        print(f"  Days with 2+ trades: {len(multi)}")

        wd = daily[daily["gross_pnl"] > 0]
        ld = daily[daily["gross_pnl"] < 0]
        print(f"  Win days: {len(wd)} | Lose days: {len(ld)}")
        if len(wd) > 0:
            print(f"  Avg win day: ${wd['gross_pnl'].mean():+.2f}")
        if len(ld) > 0:
            print(f"  Avg lose day: ${ld['gross_pnl'].mean():+.2f}")

        print(f"\n  Config overrides:")
        for k, v in best["overrides"].items():
            print(f"    {k}: {v}")

        pd.DataFrame(best["trades_list"]).to_csv(
            project_root / "data" / "backtests" / "dcb_aggressive_best_trades.csv", index=False
        )
        daily.to_csv(
            project_root / "data" / "backtests" / "dcb_aggressive_best_daily.csv", index=False
        )
else:
    print("\nNo profitable config found — all configs have PF < 1.0")
    worst = min(results, key=lambda r: abs(1.0 - r["pf"]))
    print(f"Closest to breakeven: {worst['name']} PF={worst['pf']:.2f}")
