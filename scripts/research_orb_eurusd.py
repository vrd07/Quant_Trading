#!/usr/bin/env python3
"""ORB research on EURUSD 15m. CAVEAT: only ~7 weeks of data — exploratory only."""
import sys, yaml, logging
logging.disable(logging.WARNING)
from decimal import Decimal
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.backtest.backtest_engine import BacktestEngine
from src.strategies.opening_range_breakout_strategy import OpeningRangeBreakoutStrategy
from src.core.types import Symbol

with open('config/config_live_5000.yaml') as f:
    config = yaml.safe_load(f)

sc = config['symbols']['EURUSD']
symbol = Symbol(
    ticker='EURUSD',
    pip_value=Decimal(str(sc['pip_value'])), min_lot=Decimal(str(sc['min_lot'])),
    max_lot=Decimal('0.10'), lot_step=Decimal(str(sc['lot_step'])),
    value_per_lot=Decimal(str(sc['value_per_lot'])),
    min_stops_distance=Decimal(str(sc['min_stops_distance'])),
    leverage=Decimal(str(sc['leverage'])),
)

bars5 = pd.read_csv('data/historical/EURUSD_5m_real.csv',
                    parse_dates=['timestamp'], index_col='timestamp')
o = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
bars15 = bars5.resample('15min', label='left', closed='left').agg(o).dropna()
days = max((bars15.index[-1] - bars15.index[0]).days, 1)
print(f"EURUSD 15m bars: {len(bars15)}  {bars15.index[0].date()} -> {bars15.index[-1].date()} ({days}d)\n")

def run(label, ov):
    s = OpeningRangeBreakoutStrategy(symbol, {'enabled': True, **ov})
    e = BacktestEngine(strategy=s, initial_capital=Decimal('5000'),
                       risk_config=config, slippage_model='realistic')
    r = e.run(bars=bars15)
    print(f'{label:<26} T={r.total_trades:>4} ({r.total_trades/days:>4.2f}/d) '
          f'WR={r.win_rate:>5.1f}% PF={r.profit_factor:>4.2f} ${r.total_return:>8.2f}', flush=True)
    return r

print(f"{'config':<26} {'trades':>13} {'WR':>7} {'PF':>7} {'return':>10}")
print("-" * 80)

# EURUSD sessions (UTC): London 07:00, NY/US 12:00-13:00
BASE = dict(sessions=[[7, 0], [12, 0]], or_minutes=30, entry_window_minutes=180,
            rr_ratio=2.0, min_or_atr=0.5, max_or_atr=4.0)
run('orb_base', BASE)
run('orb_london', {**BASE, 'sessions': [[7, 0]]})
run('orb_us12', {**BASE, 'sessions': [[12, 0]]})
run('orb_or15', {**BASE, 'or_minutes': 15})
run('orb_or60', {**BASE, 'or_minutes': 60})
run('orb_rr1.5', {**BASE, 'rr_ratio': 1.5})
run('orb_rr3', {**BASE, 'rr_ratio': 3.0})
run('orb_htf50', {**BASE, 'htf_trend_enabled': True, 'htf_ema_period': 50})
run('orb_conv', {**BASE, 'conviction_enabled': True, 'close_position_pct': 0.4})
run('orb_htf+conv', {**BASE, 'htf_trend_enabled': True, 'conviction_enabled': True, 'close_position_pct': 0.4})
print("\nDone!")
