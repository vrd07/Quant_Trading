#!/usr/bin/env python3
"""Research harness: Opening-Range Breakout on 15m XAUUSD.

Goal: a daily-frequency 15m breakout with real edge (PF >= 1.4, ~1 trade/day),
the honest replacement for the dead Donchian breakout.
"""
import sys, yaml, time, logging
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

symbol = Symbol(
    ticker='XAUUSD', pip_value=Decimal('0.01'), min_lot=Decimal('0.01'),
    max_lot=Decimal('0.10'), lot_step=Decimal('0.01'), value_per_lot=Decimal('100'),
    min_stops_distance=Decimal('1.0'), leverage=Decimal('30')
)

bars5 = pd.read_csv('data/historical/XAUUSD_5m_real.csv',
                    parse_dates=['timestamp'], index_col='timestamp')
o = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}
bars15 = bars5.resample('15min', label='left', closed='left').agg(o).dropna()
span_days = (bars5.index[-1] - bars5.index[0]).days

# in-sample / out-of-sample split (70/30) for an honest read
split = bars15.index[0] + (bars15.index[-1] - bars15.index[0]) * 0.70
is_bars = bars15[bars15.index <= split]
oos_bars = bars15[bars15.index > split]
print(f"15m bars: {len(bars15)}  IS={len(is_bars)} OOS={len(oos_bars)}  span={span_days}d\n")

def run(label, ov, data=bars15, tag=""):
    cfg = {'enabled': True, **ov}
    s = OpeningRangeBreakoutStrategy(symbol, cfg)
    e = BacktestEngine(strategy=s, initial_capital=Decimal('5000'),
                       risk_config=config, slippage_model='realistic')
    days = max((data.index[-1] - data.index[0]).days, 1)
    r = e.run(bars=data)
    tpd = r.total_trades / days
    print(f'{label:<30}{tag:<6} T={r.total_trades:>4} ({tpd:>4.2f}/d) '
          f'WR={r.win_rate:>5.1f}% PF={r.profit_factor:>4.2f} ${r.total_return:>9.2f}',
          flush=True)
    return r

print(f"{'config':<36} {'trades':>13} {'WR':>7} {'PF':>7} {'return':>11}")
print("-" * 92)

BASE = dict(sessions=[[7, 0], [13, 0]], or_minutes=30,
            entry_window_minutes=180, rr_ratio=2.0)

# 1. Baseline, no optional filters
run('orb_base', BASE)

# 2. Session variants
run('orb_london_only', {**BASE, 'sessions': [[7, 0]]})
run('orb_ny_only',     {**BASE, 'sessions': [[13, 0]]})
run('orb_3sess',       {**BASE, 'sessions': [[7, 0], [13, 0], [2, 0]]})  # +Asia

# 3. OR window length
run('orb_or15',  {**BASE, 'or_minutes': 15})
run('orb_or45',  {**BASE, 'or_minutes': 45})
run('orb_or60',  {**BASE, 'or_minutes': 60})

# 4. RR sweep
run('orb_rr1.5', {**BASE, 'rr_ratio': 1.5})
run('orb_rr2.5', {**BASE, 'rr_ratio': 2.5})
run('orb_rr3.0', {**BASE, 'rr_ratio': 3.0})

# 5. Filters
run('orb_htf50',     {**BASE, 'htf_trend_enabled': True, 'htf_ema_period': 50})
run('orb_conv',      {**BASE, 'conviction_enabled': True, 'close_position_pct': 0.4})
run('orb_htf+conv',  {**BASE, 'htf_trend_enabled': True, 'conviction_enabled': True, 'close_position_pct': 0.4})

# 6. OR-height band tighten
run('orb_orband',    {**BASE, 'min_or_atr': 0.8, 'max_or_atr': 3.0})

print("\n--- IS / OOS split on the better candidates ---")
for label, ov in [
    ('base', BASE),
    ('htf50', {**BASE, 'htf_trend_enabled': True}),
    ('rr1.5', {**BASE, 'rr_ratio': 1.5}),
    ('htf+rr1.5', {**BASE, 'htf_trend_enabled': True, 'rr_ratio': 1.5}),
]:
    run(label, ov, data=is_bars, tag="[IS]")
    run(label, ov, data=oos_bars, tag="[OOS]")

print("\nDone!")

print("\n--- FADE hypothesis: invert the break ---")
FADE = dict(sessions=[[7, 0], [13, 0]], or_minutes=30,
            entry_window_minutes=180, fade_mode=True, fade_target_atr=1.0)
run('fade_base', FADE)
run('fade_london', {**FADE, 'sessions': [[7, 0]]})
run('fade_tgt1.5', {**FADE, 'fade_target_atr': 1.5})
run('fade_orband', {**FADE, 'min_or_atr': 0.8, 'max_or_atr': 3.0})
run('fade_base', FADE, data=is_bars, tag="[IS]")
run('fade_base', FADE, data=oos_bars, tag="[OOS]")
run('fade_tgt1.5', {**FADE, 'fade_target_atr': 1.5}, data=is_bars, tag="[IS]")
run('fade_tgt1.5', {**FADE, 'fade_target_atr': 1.5}, data=oos_bars, tag="[OOS]")
