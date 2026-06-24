"""
Wednesday Drift Strategy — mid-week JPY-weakness / risk-on carry drift on AUDJPY.

Research basis (scripts/research_newinstruments_calendar.py, 2026-06-24, 2.5y
Dukascopy 1m→5m, cost charged): equity-index Tuesday-overnight method applied to
JPY crosses surfaced a clean Wednesday up-day. A long AUDJPY held over the
Wednesday session (Tue cash-close → Wed cash-close) is +ve & significant
(t=2.11 daily), EURJPY confirms the direction (JPY weakness Wednesday, not pure
AUDJPY mining):
    AUDJPY: PF 1.57 all / 1.38 IS / 2.46 OOS,  maxDD −5.1%,  EVERY year >1.2
            (2024 1.21 / 2025 1.97 / 2026 1.92), cost-robust ≥1.46 at 4bps.
    EURJPY: same direction but weaker (PF 1.41, OOS 1.15) — excluded.
  index_overnight-class quality (every-year, OOS≥IS, tight DD) on a more
  diversifying driver (carry/JPY/risk, uncorrelated to gold AND equities).
  ⚠️ Mechanism fuzzier than oil-EIA (mid-week risk-on / AUD-data flows?); a
  SEASONAL/calendar anomaly shipped by user decision (monday_drift precedent),
  NOT a structural edge. AUDJPY only (EURJPY too weak).

Mechanics (one trade per week):
  1. First completed Tuesday bar at/after entry_hour_utc:entry_minute_utc
     (default 19:45 UTC ≈ session close 20:00) — captures the Wednesday session.
  2. BUY at market (long-only: the drift IS the edge).
  3. SL = stop_atr_multiplier × ATR(14, daily) below entry — a WIDE catastrophe
     guard only (research ran with no stop at −5% DD). NO take-profit.
  4. Exit is the risk layer's per-strategy time stop
     (trailing_stop.strategy_overrides.wednesday_drift.time_stop_minutes ≈ 1440
     → flat ~Wed 20:00 UTC at session close). BE/lock disabled.

Stateless except a one-trade-per-week latch.
"""

from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from .base_strategy import BaseStrategy
from .index_overnight_strategy import resample_daily, daily_atr


class WednesdayDriftStrategy(BaseStrategy):
    """Mid-week AUDJPY risk-on drift hold. Validated for AUDJPY only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.entry_dow = int(config.get('entry_dow', 1))          # 1 = Tuesday (enter for the Wed move)
        self.entry_hour_utc = int(config.get('entry_hour_utc', 19))
        self.entry_minute_utc = int(config.get('entry_minute_utc', 45))
        self.atr_period = int(config.get('atr_period', 14))
        self.stop_atr_multiplier = float(config.get('stop_atr_multiplier', 1.5))
        self.signal_strength = float(config.get('signal_strength', 0.60))
        # Hard symbol gate: validated on AUDJPY ONLY (EURJPY too weak). Prefix
        # match covers broker suffixes (AUDJPYs).
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['AUDJPY'])
        )
        self._last_signal_week = None   # one-trade-per-week latch (ISO year, week)

    def get_name(self) -> str:
        return "wednesday_drift"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.enabled or len(bars) < 10:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on AUDJPY only

        ts = bars.index[-1]
        if not hasattr(ts, 'hour'):      # live RangeIndex frames carry a column
            if 'timestamp' in bars.columns:
                ts = pd.Timestamp(bars['timestamp'].iloc[-1])
                bars = bars.set_index(pd.DatetimeIndex(pd.to_datetime(bars['timestamp'])))
            else:
                return None

        if ts.dayofweek != self.entry_dow:           # Tuesdays only (enters for the Wed move)
            return None
        in_window = (int(ts.hour) == self.entry_hour_utc
                     and int(ts.minute) >= self.entry_minute_utc)
        if not in_window:
            return None

        iso = ts.isocalendar()
        week = (int(iso[0]), int(iso[1]))
        if self._last_signal_week == week:
            return None

        daily = resample_daily(bars)
        if len(daily) and daily.index[-1].date() == ts.date():
            daily = daily.iloc[:-1]
        atr_d = daily_atr(daily, self.atr_period)
        if atr_d is None:
            self._log_no_signal(
                f"only {len(daily)} daily bars < ATR({self.atr_period}) history")
            return None
        if atr_d <= 0:
            return None

        close = float(bars.close.iloc[-1])
        stop = close - self.stop_atr_multiplier * atr_d

        self._last_signal_week = week
        return self._create_signal(
            side=OrderSide.BUY,           # long-only: the mid-week JPY-weakness drift IS the edge
            strength=self.signal_strength,
            regime=MarketRegime.TREND,
            entry_price=close,
            stop_loss=stop,
            take_profit=None,             # time-stop exit; a TP caps the drift
            metadata={
                'strategy': 'wednesday_drift',
                'stop_price': stop,       # RiskProcessor honors this verbatim
                'daily_atr': atr_d,
            },
        )
