"""
Monday Drift Strategy — weekly anti-USD drift harvest on GBPUSD/AUDUSD.

Research basis (scripts/research_monday_drift.py, 2026-06-11, 2.5y Dukascopy
1m→5m data, 2p costs charged):
  Long-only Monday hold, entered after 00:00 UTC Monday (NOT at the Sunday
  open — the apparent edge there is a BID-spread artifact), exited by time
  stop ~21:00 UTC Monday. Gated on daily close > SMA(50): the effect is the
  anti-USD drift of 2025-26, near-zero in 2024, and WILL reverse in a
  USD-strength regime — the SMA gate is the kill-switch that kept 2024
  non-negative on both pairs.
    GBPUSD: PF 1.95 all / 1.54 IS / 4.17 OOS, every year >= +32p
    AUDUSD: PF 1.59 all / 1.21 IS / 2.60 OOS, every year >= flat
  EURUSD shows the same drift but too weak to pay costs (PF 1.09) — excluded.
  ⚠️ This is a REGIME trade shipped by user decision (LBO precedent), not a
  structural edge; it did not face the strict-fill gate.

Mechanics (one trade per week per symbol):
  1. First completed bar of Monday 00:00-00:59 UTC (after weekend rollover,
     spreads normalized).
  2. Gate: previous completed daily close > SMA(50) of completed daily
     closes (daily frame resampled from the strategy's own intraday bars).
  3. BUY at market. SL = stop_atr_multiplier x ATR(14, daily) below entry
     (plateau 0.75-1.0; 1.0 ships — 8-9%% stop-out rate, time exit is the
     real exit). NO take-profit.
  4. Exit is the risk layer's per-strategy time stop (trailing_stop.
     strategy_overrides.monday_drift.time_stop_minutes ≈ 1230 → flat before
     the 21:00 UTC rollover). BE/lock moves are disabled — tightening into
     intraday noise destroys a drift hold.

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


def uptrend_gate_and_atr(daily: pd.DataFrame, sma_period: int,
                         atr_period: int) -> Optional[Tuple[bool, float]]:
    """Pure: (last close > SMA(sma_period), ATR(atr_period)) on completed
    daily bars; None if history is too short (e.g. starved preload)."""
    if len(daily) < max(sma_period, atr_period + 1):
        return None
    closes = daily.close
    sma = float(closes.iloc[-sma_period:].mean())
    prev_close = closes.shift(1)
    tr = pd.concat([daily.high - daily.low,
                    (daily.high - prev_close).abs(),
                    (daily.low - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.iloc[-atr_period:].mean())
    return float(closes.iloc[-1]) > sma, atr


class MondayDriftStrategy(BaseStrategy):
    """Monday anti-USD drift hold. Validated for GBPUSD/AUDUSD only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.entry_hour_utc = int(config.get('entry_hour_utc', 0))
        self.sma_period = int(config.get('sma_period', 50))
        self.atr_period = int(config.get('atr_period', 14))
        self.stop_atr_multiplier = float(config.get('stop_atr_multiplier', 1.0))
        self.signal_strength = float(config.get('signal_strength', 0.60))
        # Hard symbol gate: validated on GBPUSD/AUDUSD ONLY (EURUSD too weak,
        # USDJPY inverted). Prefix match covers broker suffixes (GBPUSDs).
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['GBPUSD', 'AUDUSD'])
        )
        self._last_signal_week = None   # one-trade-per-week latch (ISO year, week)

    def get_name(self) -> str:
        return "monday_drift"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.enabled or len(bars) < 10:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on GBPUSD/AUDUSD only

        ts = bars.index[-1]
        if not hasattr(ts, 'hour'):      # live RangeIndex frames carry a column
            if 'timestamp' in bars.columns:
                ts = pd.Timestamp(bars['timestamp'].iloc[-1])
                bars = bars.set_index(pd.DatetimeIndex(pd.to_datetime(bars['timestamp'])))
            else:
                return None

        if ts.dayofweek != 0:            # Mondays only
            return None
        if int(ts.hour) != self.entry_hour_utc:
            return None                  # entry window = one hour; miss → skip week

        iso = ts.isocalendar()
        week = (int(iso[0]), int(iso[1]))
        if self._last_signal_week == week:
            return None

        daily = resample_daily(bars)
        # Gate and ATR use COMPLETED days only — drop today's partial bar.
        if len(daily) and daily.index[-1].date() == ts.date():
            daily = daily.iloc[:-1]
        gate = uptrend_gate_and_atr(daily, self.sma_period, self.atr_period)
        if gate is None:
            self._log_no_signal(
                f"only {len(daily)} daily bars < SMA({self.sma_period}) history")
            return None
        uptrend, atr_d = gate
        if not uptrend:
            self._log_no_signal("regime gate: close <= daily SMA — USD not weak, stand aside")
            return None
        if atr_d <= 0:
            return None

        close = float(bars.close.iloc[-1])
        stop = close - self.stop_atr_multiplier * atr_d

        self._last_signal_week = week
        return self._create_signal(
            side=OrderSide.BUY,           # long-only: the effect IS the anti-USD drift
            strength=self.signal_strength,
            regime=MarketRegime.TREND,
            entry_price=close,
            stop_loss=stop,
            take_profit=None,             # time-stop exit; a TP caps the drift
            metadata={
                'strategy': 'monday_drift',
                'stop_price': stop,       # RiskProcessor honors this verbatim
                'daily_atr': atr_d,
                'sma_period': self.sma_period,
            },
        )
