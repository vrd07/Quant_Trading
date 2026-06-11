"""
London Breakout Strategy — Asia-range breakout at London open.

Research basis (scripts/research_fx_majors.py + research_usdjpy_lbo.py,
2026-06-11, 2.5y Dukascopy 1m→15m data, costs charged):
  USDJPY is the only FX major where this survives — PF 1.23 IS / 1.44+ OOS
  untuned, positive every year 2024/2025/2026, ~140 trades/yr. The same
  setup LOSES on GBPUSD and AUDUSD (PF 0.55-0.88) — do not enable it there.
  Parameter plateau is wide (stop 0.35-0.75x range, exits 15:00-18:00 all
  PF≥1.27 OOS), so nothing here is a tuned magic number.

Mechanics (one trade per day):
  1. Asia range = high/low of 00:00-06:59 UTC bars (Tokyo session).
  2. During 07:00-09:59 UTC, the first 15m CLOSE beyond the range enters
     with the break.
  3. SL = stop_range_fraction x range behind entry (structural: a re-entry
     that deep into the range invalidates the breakout).
  4. NO take-profit — the edge dies if capped at 1x range (OOS PF 1.44 →
     1.04). Exit is the risk layer's time stop (trailing_stop.
     time_stop_minutes ≈ 360 = flat in the NY afternoon) or the SL.

Stateless except a one-trade-per-day latch (same pattern as cooldown_bars
in other strategies).
"""

from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from .base_strategy import BaseStrategy


def detect_asia_range(day_bars: pd.DataFrame, asia_end_hour: int) -> Optional[tuple]:
    """Pure: (high, low) of today's bars before asia_end_hour, or None."""
    hours = day_bars.index.hour
    asia = day_bars[hours < asia_end_hour]
    if len(asia) == 0:
        return None
    return float(asia.high.max()), float(asia.low.min()), len(asia)


class LondonBreakoutStrategy(BaseStrategy):
    """Asia-range breakout at London open. Validated for USDJPY only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.asia_end_hour = int(config.get('asia_end_hour', 7))      # range = 00:00..end-1
        self.entry_start_hour = int(config.get('entry_start_hour', 7))
        self.entry_end_hour = int(config.get('entry_end_hour', 10))   # exclusive
        self.stop_range_fraction = float(config.get('stop_range_fraction', 0.5))
        self.min_asia_bars = int(config.get('min_asia_bars', 12))     # ≥3h of 15m bars
        # Skip days with a tiny Asia range (price units). Tight ranges mean
        # tight stops, and the strict fill model's bar-extremum stop fills
        # turn a 6-pip stop into a multi-R loss — cost/overshoot must stay a
        # bounded fraction of stop distance for the edge to survive.
        self.min_asia_range = float(config.get('min_asia_range', 0.0))
        self.signal_strength = float(config.get('signal_strength', 0.65))
        # Hard symbol gate: validated on USDJPY ONLY (loses on GBPUSD/AUDUSD).
        # Prefix match so the broker's suffixed ticker (USDJPYs) also passes.
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['USDJPY'])
        )
        self._last_signal_date = None   # one-trade-per-day latch

    def get_name(self) -> str:
        return "london_breakout"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.enabled or len(bars) < self.min_asia_bars + 1:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on USDJPY only — never trade other symbols

        ts = bars.index[-1]
        if not hasattr(ts, 'hour'):       # live RangeIndex frames carry a column
            if 'timestamp' in bars.columns:
                ts = pd.Timestamp(bars['timestamp'].iloc[-1])
                bars = bars.set_index(pd.DatetimeIndex(pd.to_datetime(bars['timestamp'])))
            else:
                return None

        hour = int(ts.hour)
        if not (self.entry_start_hour <= hour < self.entry_end_hour):
            return None

        today = ts.date()
        if self._last_signal_date == today:
            return None

        day_bars = bars[bars.index.date == today]
        rng = detect_asia_range(day_bars, self.asia_end_hour)
        if rng is None:
            return None
        asia_high, asia_low, n_asia = rng
        if n_asia < self.min_asia_bars:
            self._log_no_signal(f"asia range has {n_asia} bars < {self.min_asia_bars}")
            return None
        range_width = asia_high - asia_low
        if range_width <= 0:
            return None
        if range_width < self.min_asia_range:
            self._log_no_signal(f"asia range {range_width:.4f} < min {self.min_asia_range}")
            return None

        close = float(bars.close.iloc[-1])
        if close > asia_high:
            side = OrderSide.BUY
            stop = close - self.stop_range_fraction * range_width
        elif close < asia_low:
            side = OrderSide.SELL
            stop = close + self.stop_range_fraction * range_width
        else:
            return None

        self._last_signal_date = today
        return self._create_signal(
            side=side,
            strength=self.signal_strength,
            regime=MarketRegime.TREND,
            entry_price=close,
            stop_loss=stop,
            take_profit=None,            # time-stop exit; never cap with a TP
            metadata={
                'strategy': 'london_breakout',
                'stop_price': stop,       # RiskProcessor honors this verbatim
                'asia_high': asia_high,
                'asia_low': asia_low,
                'asia_range': range_width,
            },
        )
