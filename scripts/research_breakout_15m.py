#!/usr/bin/env python3
"""Research harness: breakout strategy baseline on 5m vs 15m.

Establishes the current edge (or lack of it) before any rewrite, and measures
trade frequency (the user wants "trades daily" on 15m).
"""
import sys, yaml, time, logging
logging.disable(logging.WARNING)
from decimal import Decimal
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.backtest.backtest_engine import BacktestEngine
from src.strategies.breakout_strategy import BreakoutStrategy
from src.core.types import Symbol

with open('config/config_live_5000.yaml') as f:
    config = yaml.safe_load(f)

symbol = Symbol(
    ticker='XAUUSD', pip_value=Decimal('0.01'), min_lot=Decimal('0.01'),
    max_lot=Decimal('0.10'), lot_step=Decimal('0.01'), value_per_lot=Decimal('100'),
    min_stops_distance=Decimal('1.0'), leverage=Decimal('30')
)

bars5 = pd.read_csv('data/historical/XAUUSD_5m_real.csv',
                    parse_dates=['timestamp'], index_col='timestamp')

def resample(df, rule):
    o = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
    return df.resample(rule, label='left', closed='left').agg(o).dropna()

bars15 = resample(bars5, '15min')

span_days = (bars5.index[-1] - bars5.index[0]).days
print(f"5m bars:  {len(bars5):>7}  ({bars5.index[0].date()} -> {bars5.index[-1].date()}, {span_days}d)")
print(f"15m bars: {len(bars15):>7}\n")

def run(label, bars, ov):
    cfg = {**config['strategies']['breakout'], **ov}
    s = BreakoutStrategy(symbol, cfg)
    e = BacktestEngine(strategy=s, initial_capital=Decimal('5000'),
                       risk_config=config, slippage_model='realistic')
    t0 = time.time()
    r = e.run(bars=bars)
    dt = time.time() - t0
    tpd = r.total_trades / max(span_days, 1)
    print(f'{label:<34} T={r.total_trades:>4} ({tpd:>4.2f}/day) '
          f'WR={r.win_rate:>5.1f}% PF={r.profit_factor:>4.2f} '
          f'${r.total_return:>9.2f}  ({dt:.0f}s)', flush=True)
    return r

print(f"{'config':<34} {'trades':>14} {'WR':>7} {'PF':>7} {'return':>11}")
print("-" * 92)

# Current live config (enabled flag forced true so it actually runs)
run('current_v3  @5m',  bars5,  {'enabled': True})
run('current_v3  @15m', bars15, {'enabled': True, 'timeframe': '15m'})
print()

ON = {'enabled': True, 'timeframe': '15m'}
LONDON_NY = [[3, 16], [21, 23]]          # full London + NY span
ALLDAY    = [[0, 23]]

# --- Frequency sweep on 15m: how does loosening for ~1 trade/day hit PF? ---
run('15m wide-session',       bars15, {**ON, 'allowed_sessions': LONDON_NY})
run('15m wide + cd1',         bars15, {**ON, 'allowed_sessions': LONDON_NY, 'cooldown_bars': 1})
run('15m wide + dc8 + cd1',   bars15, {**ON, 'allowed_sessions': LONDON_NY, 'cooldown_bars': 1, 'donchian_period': 8})
run('15m allday',             bars15, {**ON, 'allowed_sessions': ALLDAY})
run('15m allday + dc8 + cd1', bars15, {**ON, 'allowed_sessions': ALLDAY, 'cooldown_bars': 1, 'donchian_period': 8})
print()

# --- Strip filters one at a time on wide sessions (what's actually carrying edge?) ---
run('15m wide  no-bb',        bars15, {**ON, 'allowed_sessions': LONDON_NY, 'bb_squeeze_enabled': False})
run('15m wide  no-htf',       bars15, {**ON, 'allowed_sessions': LONDON_NY, 'htf_trend_enabled': False})
run('15m wide  no-macd',      bars15, {**ON, 'allowed_sessions': LONDON_NY, 'macd_confirmation': False})
run('15m wide  no-ema',       bars15, {**ON, 'allowed_sessions': LONDON_NY, 'ema_confirm_enabled': False})
run('15m wide  no-conv',      bars15, {**ON, 'allowed_sessions': LONDON_NY, 'close_position_pct': 1.0})
