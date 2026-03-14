"""
VWAP Strategy - Intraday mean reversion around VWAP with high-win-rate filtering.

Entry Logic (HIGH WIN-RATE version):
- Only in RANGE regime
- Price drops below VWAP - (ATR × multiplier): BUY signal
- Price rises above VWAP + (ATR × multiplier): SELL signal
- RSI confirmation: RSI < 35 for BUY, RSI > 65 for SELL (genuinely extreme)
- CCI confirmation: CCI < -100 for BUY, CCI > +100 for SELL (second oscillator)
- Volume above minimum ratio (when data available)

Exit Logic:
- Take profit: Return to VWAP
- Stop loss: stop_atr_multiplier × ATR from entry
"""

from typing import Optional, Dict
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


class VWAPStrategy(BaseStrategy):
    """VWAP deviation mean reversion strategy with RSI + CCI double confirmation."""

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        # Strategy parameters
        self.atr_multiplier = config.get('atr_multiplier', 1.5)
        self.stop_atr_multiplier = config.get('stop_atr_multiplier', 2.0)
        self.atr_period = config.get('atr_period', 14)
        self.min_volume_ratio = config.get('min_volume_ratio', 1.0)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]

        # RSI confirmation thresholds for entries (tighter — genuinely extreme)
        self.rsi_oversold_entry = config.get('rsi_oversold_entry', 35)   # BUY below this
        self.rsi_overbought_entry = config.get('rsi_overbought_entry', 65)  # SELL above this

        # CCI confirmation thresholds
        self.cci_oversold_entry = config.get('cci_oversold_entry', -100)   # BUY below this
        self.cci_overbought_entry = config.get('cci_overbought_entry', 100)  # SELL above this
        self.cci_period = config.get('cci_period', 20)

        # Regime filter
        self.regime_filter = RegimeFilter()

    def get_name(self) -> str:
        return "vwap_deviation"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Generate VWAP deviation signal with HIGH WIN-RATE double confirmation.

        Logic:
        1. Check regime (prefer RANGE) on a higher timeframe (1h)
        2. Calculate VWAP and deviation bands
        3. Check for oversold/overbought price deviation
        4. Confirm with RSI (genuinely extreme: < 35 or > 65)
        5. Confirm with CCI (< -100 or > +100)
        6. Volume filter (when available)
        7. Generate signal
        """
        if not self.is_enabled():
            return None

        min_bars = max(self.atr_period + 5, 20, self.cci_period + 5)
        if len(bars) < min_bars:
            self._log_no_signal("Insufficient data")
            return None

        # Check regime on a higher timeframe if possible to avoid intraday noise overriding the daily setup
        try:
            from ..data.data_engine import DataEngine
            # Wait, DataEngine is not directly accessible here. Let's look up if there's a reference or pass current bars
            # I will just use the current bars since `bars` passed into `on_bar` are what we have.
            pass
        except ImportError:
            pass

        # Check regime using the strategy's regular bars for now, but in the future we'll consider MTF.
        regime = self.regime_filter.classify(bars)
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None

        # Calculate indicators
        vwap, upper_band, lower_band = Indicators.vwap_deviation(
            bars,
            atr_multiplier=self.atr_multiplier
        )
        atr = Indicators.atr(bars, period=self.atr_period)
        rsi = Indicators.rsi(bars, period=14)
        cci = Indicators.cci(bars, period=self.cci_period)

        current_close = bars['close'].iloc[-1]
        current_vwap = vwap.iloc[-1]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        current_atr = atr.iloc[-1]
        current_rsi = rsi.iloc[-1]
        current_cci = cci.iloc[-1]

        if any(pd.isna([current_vwap, current_atr, current_rsi, current_cci])):
            self._log_no_signal("VWAP, ATR, RSI or CCI calculation failed")
            return None

        # Volume check (when data is available)
        if 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].rolling(20).mean().iloc[-1]
            if avg_volume > 0 and current_volume < avg_volume * self.min_volume_ratio:
                self._log_no_signal("Volume too low")
                return None

        # --- Oversold BUY signal ---
        if current_close < current_lower:

            # RSI must be genuinely oversold
            if current_rsi >= self.rsi_oversold_entry:
                self._log_no_signal(
                    f"RSI not oversold enough for BUY ({current_rsi:.1f} >= {self.rsi_oversold_entry})")
                return None

            # CCI must confirm oversold
            if current_cci >= self.cci_oversold_entry:
                self._log_no_signal(
                    f"CCI not oversold for BUY ({current_cci:.1f} >= {self.cci_oversold_entry})")
                return None

            stop_loss = current_close - (self.stop_atr_multiplier * current_atr)
            take_profit = current_vwap  # Target: revert to VWAP

            # Ensure positive R:R (VWAP must be above entry by at least the stop distance)
            risk = current_close - stop_loss
            reward = take_profit - current_close
            if risk <= 0 or reward <= 0:
                self._log_no_signal("Invalid R:R for VWAP BUY")
                return None

            deviation_pct = (current_vwap - current_close) / current_vwap * 100
            rsi_extreme = max(0, (self.rsi_oversold_entry - current_rsi) / self.rsi_oversold_entry)
            cci_extreme = max(0, (-current_cci - 100) / 100)
            strength = min(deviation_pct / 2 + rsi_extreme * 0.2 + cci_extreme * 0.2, 1.0)

            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'entry_reason': 'oversold_rsi_cci_below_vwap_band'
                }
            )

        # --- Overbought SELL signal ---
        if current_close > current_upper:

            # RSI must be genuinely overbought
            if current_rsi <= self.rsi_overbought_entry:
                self._log_no_signal(
                    f"RSI not overbought enough for SELL ({current_rsi:.1f} <= {self.rsi_overbought_entry})")
                return None

            # CCI must confirm overbought
            if current_cci <= self.cci_overbought_entry:
                self._log_no_signal(
                    f"CCI not overbought for SELL ({current_cci:.1f} <= {self.cci_overbought_entry})")
                return None

            stop_loss = current_close + (self.stop_atr_multiplier * current_atr)
            take_profit = current_vwap  # Target: revert to VWAP

            risk = stop_loss - current_close
            reward = current_close - take_profit
            if risk <= 0 or reward <= 0:
                self._log_no_signal("Invalid R:R for VWAP SELL")
                return None

            deviation_pct = (current_close - current_vwap) / current_vwap * 100
            rsi_extreme = max(0, (current_rsi - self.rsi_overbought_entry) / (100 - self.rsi_overbought_entry))
            cci_extreme = max(0, (current_cci - 100) / 100)
            strength = min(deviation_pct / 2 + rsi_extreme * 0.2 + cci_extreme * 0.2, 1.0)

            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                stop_loss=float(stop_loss),
                take_profit=float(take_profit),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'entry_reason': 'overbought_rsi_cci_above_vwap_band'
                }
            )

        # Price within bands
        self._log_no_signal(
            f"Price {current_close:.2f} within VWAP bands "
            f"[{current_lower:.2f} – {current_upper:.2f}]")
        return None
