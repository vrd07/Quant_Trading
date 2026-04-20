#!/usr/bin/env python3
"""SMC OB parameter sweep — find config that maximises $/day on last-month data."""
import sys, logging, itertools, os
from pathlib import Path
from decimal import Decimal
import pandas as pd
import yaml

os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
logging.disable(logging.CRITICAL)

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.smc_ob_strategy import SMCOrderBlockStrategy
from src.core.types import Symbol

CONFIG_PATH = project_root / "config/config_live_10000.yaml"
DATA_PATH   = project_root / "data/historical/XAUUSD_5m_real.csv"
START, END  = "2026-02-27", "2026-03-27"

with open(CONFIG_PATH) as f:
    base_cfg = yaml.safe_load(f)

bars = pd.read_csv(DATA_PATH, parse_dates=["timestamp"], index_col="timestamp")
bars = bars[(bars.index >= START) & (bars.index <= END + " 23:59:59")]
print(f"Pre-filtered bars: {len(bars)} ({bars.index[0]} -> {bars.index[-1]})", flush=True)

def mk_symbol(cfg):
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

def run(params):
    cfg = {**base_cfg}
    cfg["strategies"] = {**base_cfg["strategies"]}
    smc = {
        "enabled": True, "timeframe": "5m",
        "swing_lookback": 5,
        "ob_touch_tolerance_atr": 1.0,
        "ob_max_age_bars": 120,
        "cooldown_bars": 3,
        "ema_trend_period": 50,
        "liquidity_premium_mult": 15.0, "min_liquidity_premium_mult": 8.0,
        "max_sl_atr": 4.0, "min_sl_atr": 0.05,
        "fvg_ob_proximity_atr": 2.0, "fvg_max_age_bars": 50,
        **params,
    }
    cfg["strategies"]["smc_ob"] = smc
    strat = SMCOrderBlockStrategy(mk_symbol(cfg), smc)
    engine = BacktestEngine(
        strategy=strat,
        initial_capital=Decimal("10000"),
        risk_config=cfg,
        slippage_model="realistic",
        bypass_risk_limits=True,
    )
    return engine.run(bars=bars, start_date=None, end_date=None)

grid = {
    "min_impulse_atr_mult":    [0.5, 0.8, 1.0],
    "adx_min_threshold":       [8, 12, 15],
    "require_fvg_confluence":  [True, False],
    "long_only":               [True, False],
    "use_ema_trend_filter":    [True, False],
}
keys = list(grid.keys())
combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
print(f"Sweeping {len(combos)} configs...", flush=True)

rows = []
for i, p in enumerate(combos, 1):
    try:
        r = run(p)
        tdf = pd.DataFrame(r.trades) if r.trades else pd.DataFrame()
        days = 0
        best_day = worst_day = avg_day = 0.0
        if not tdf.empty:
            tdf["date"] = pd.to_datetime(tdf["timestamp"]).dt.date
            daily = tdf.groupby("date")["pnl"].sum()
            days = len(daily)
            best_day = float(daily.max())
            worst_day = float(daily.min())
            avg_day = float(daily.mean())
        rows.append({**p,
                     "trades": r.total_trades, "win_rate": r.win_rate,
                     "pf": r.profit_factor, "ret": r.total_return,
                     "dd_pct": r.max_drawdown_pct,
                     "days": days, "avg_day": avg_day,
                     "best_day": best_day, "worst_day": worst_day,
                     "_trades": r.trades})
        print(f"[{i:3d}/{len(combos)}] imp={p['min_impulse_atr_mult']} adx={p['adx_min_threshold']:>2} "
              f"fvg={int(p['require_fvg_confluence'])} lo={int(p['long_only'])} ema={int(p['use_ema_trend_filter'])} "
              f"-> n={r.total_trades:>3} wr={r.win_rate:5.1f}% pf={r.profit_factor:5.2f} "
              f"ret=${r.total_return:>7.2f} avg/day=${avg_day:>6.2f}", flush=True)
    except Exception as e:
        print(f"[{i}] ERROR {p}: {e}", flush=True)

df = pd.DataFrame([{k: v for k, v in r.items() if k != "_trades"} for r in rows])

print("\n" + "=" * 110)
print("TOP 10 BY TOTAL RETURN")
print("=" * 110)
cols = ["min_impulse_atr_mult","adx_min_threshold","require_fvg_confluence","long_only","use_ema_trend_filter",
        "trades","win_rate","pf","ret","avg_day","best_day","worst_day","dd_pct"]
print(df.sort_values("ret", ascending=False)[cols].head(10).to_string(index=False), flush=True)

print("\n" + "=" * 110)
print("TOP 10 BY PROFIT FACTOR (min 10 trades)")
print("=" * 110)
filt = df[df.trades >= 10].sort_values("pf", ascending=False)
print(filt[cols].head(10).to_string(index=False), flush=True)

print("\n" + "=" * 110)
print("TOP 10 BY AVG $/DAY (min 10 trades)")
print("=" * 110)
print(filt.sort_values("avg_day", ascending=False)[cols].head(10).to_string(index=False), flush=True)

# Daily PnL breakdown for the top-return config
best_row = df.sort_values("ret", ascending=False).iloc[0]
best_rec = next(r for r in rows if all(r[k] == best_row[k] for k in keys))
print(f"\n--- Daily PnL for best-return config ---", flush=True)
print({k: best_rec[k] for k in keys}, flush=True)
if best_rec["_trades"]:
    tdf = pd.DataFrame(best_rec["_trades"])
    tdf["date"] = pd.to_datetime(tdf["timestamp"]).dt.date
    daily = tdf.groupby("date")["pnl"].sum().sort_values(ascending=False)
    print(daily.to_string(), flush=True)
    print(f"\nTrading days: {len(daily)}  avg=${daily.mean():.2f}  median=${daily.median():.2f}  "
          f"best=${daily.max():.2f}  worst=${daily.min():.2f}", flush=True)
    print(f"Days >= $400: {(daily >= 400).sum()}/{len(daily)}   "
          f"Days >= $100: {(daily >= 100).sum()}/{len(daily)}   "
          f"Positive days: {(daily > 0).sum()}/{len(daily)}", flush=True)

df.to_csv(project_root / "data/backtests/smc_ob_tune_grid.csv", index=False)
print(f"\nGrid saved -> data/backtests/smc_ob_tune_grid.csv", flush=True)
