"""
Index Overnight Strategy — midweek "Turnaround Tuesday" night drift on equity
indices (US30/NAS100 only).

Research basis (scripts/research_index_*.py, 2026-06-24, 2.5y Dukascopy 1m→5m,
2bps cost + 2bps/night financing charged):
  Equity indices earn their drift OVERNIGHT (cash close → next cash open), not
  intraday — the textbook "night effect" (NAS overnight Sharpe 1.73, intraday
  ~noise). But the everyday hold nets to PF ~1.15 once realistic CFD overnight
  FINANCING is charged. The drift localises to MIDWEEK: a Tuesday-entry hold
  (Tue cash-close → Wed cash-open) is +ve & significant on US30/NAS100/GER40
  independently (NAS t=2.17, US30 t=1.83, GER40 t=2.72), pays financing only
  ~1 night/week, and clears the bar:
    NAS100: PF 1.74 all / 1.42 IS / 3.31 OOS,  maxDD −3.6%,  every yr >1.3
    US30:   PF 1.68 all / 1.51 IS / 2.19 OOS,  maxDD −2.9%,  every yr >1.3
  Cost-robust to ≥1.36 PF at 8bps all-in. SMA regime gate HURTS (the effect is
  trend-agnostic, unlike monday_drift) → no gate; the −3% DD needs none.
  ⚠️ This is a calendar/SEASONAL anomaly shipped by user decision (monday_drift
  precedent), NOT a structural edge. GER40 was strongest but the broker does
  not offer it — US30/NAS100 only.

Mechanics (one trade per week per symbol):
  1. First completed Tuesday bar at/after entry_hour_utc:entry_minute_utc
     (default 19:45 UTC ≈ US cash close 20:00) — captures the overnight leg.
  2. BUY at market (long-only: the drift IS the edge).
  3. SL = stop_atr_multiplier × ATR(14, daily) below entry — a WIDE catastrophe
     guard only (research ran with no stop at −3% DD). NO take-profit.
  4. Exit is the risk layer's per-strategy time stop
     (trailing_stop.strategy_overrides.index_overnight.time_stop_minutes ≈ 1050
     → flat ~Wed 13:30 UTC at cash open). BE/lock disabled — tightening into
     overnight noise destroys a drift hold.

Stateless except a one-trade-per-week latch.
"""

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from .base_strategy import BaseStrategy


def resample_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """Pure: intraday bars → UTC daily OHLC (left/left, loader convention)."""
    return bars.resample('1D', label='left', closed='left').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    ).dropna()


def daily_atr(daily: pd.DataFrame, atr_period: int) -> Optional[float]:
    """Pure: ATR(atr_period) on completed daily bars; None if too short."""
    if len(daily) < atr_period + 1:
        return None
    prev_close = daily.close.shift(1)
    tr = pd.concat([daily.high - daily.low,
                    (daily.high - prev_close).abs(),
                    (daily.low - prev_close).abs()], axis=1).max(axis=1)
    return float(tr.iloc[-atr_period:].mean())


class IndexOvernightStrategy(BaseStrategy):
    """Tuesday-night index overnight drift. Validated for US30/NAS100 only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.entry_dow = int(config.get('entry_dow', 1))          # 1 = Tuesday
        self.entry_hour_utc = int(config.get('entry_hour_utc', 19))
        self.entry_minute_utc = int(config.get('entry_minute_utc', 45))
        self.atr_period = int(config.get('atr_period', 14))
        self.stop_atr_multiplier = float(config.get('stop_atr_multiplier', 1.5))
        self.signal_strength = float(config.get('signal_strength', 0.60))
        # Hard symbol gate: validated on US30/NAS100 ONLY (GER40 stronger but
        # not offered by the broker). Prefix match covers broker suffixes
        # (US30.cash / US30s / NAS100.cash etc.).
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['US30', 'NAS100'])
        )
        self._last_signal_week = None   # one-trade-per-week latch (ISO year, week)

    def get_name(self) -> str:
        return "index_overnight"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.enabled or len(bars) < 10:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on US30/NAS100 only

        ts = bars.index[-1]
        if not hasattr(ts, 'hour'):      # live RangeIndex frames carry a column
            if 'timestamp' in bars.columns:
                ts = pd.Timestamp(bars['timestamp'].iloc[-1])
                bars = bars.set_index(pd.DatetimeIndex(pd.to_datetime(bars['timestamp'])))
            else:
                return None

        if ts.dayofweek != self.entry_dow:           # Tuesdays only
            return None
        # Entry window opens at entry_hour:entry_minute (US cash close-ish) and
        # runs to the top of the next hour; first completed bar in it fires.
        in_window = (int(ts.hour) == self.entry_hour_utc
                     and int(ts.minute) >= self.entry_minute_utc)
        if not in_window:
            return None

        iso = ts.isocalendar()
        week = (int(iso[0]), int(iso[1]))
        if self._last_signal_week == week:
            return None

        daily = resample_daily(bars)
        # ATR uses COMPLETED days only — drop today's partial bar.
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
            side=OrderSide.BUY,           # long-only: the overnight drift IS the edge
            strength=self.signal_strength,
            regime=MarketRegime.TREND,
            entry_price=close,
            stop_loss=stop,
            take_profit=None,             # time-stop exit; a TP caps the drift
            metadata={
                'strategy': 'index_overnight',
                'stop_price': stop,       # RiskProcessor honors this verbatim
                'daily_atr': atr_d,
            },
        )
