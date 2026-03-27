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
        # Risk logic removed (handled by RiskProcessor)
        self.atr_period = config.get('atr_period', 14)
        self.atr_multiplier = config.get('atr_multiplier', 2.5)
        self.min_volume_ratio = config.get('min_volume_ratio', 1.0)
        self.only_in_regime = MarketRegime[config.get('only_in_regime', 'RANGE')]
        # VWAP reversion is a short-duration thesis: if price hasn't returned to VWAP
        # within this window, the mean-reversion assumption was wrong. Default 45 min.
        self.max_hold_minutes = config.get('max_hold_minutes', 45)

        # RSI confirmation thresholds for entries (tighter — genuinely extreme)
        self.rsi_oversold_entry = config.get('rsi_oversold_entry', 35)   # BUY below this
        self.rsi_overbought_entry = config.get('rsi_overbought_entry', 65)  # SELL above this

        # CCI confirmation thresholds
        self.cci_oversold_entry = config.get('cci_oversold_entry', -100)   # BUY below this
        self.cci_overbought_entry = config.get('cci_overbought_entry', 100)  # SELL above this
        self.cci_period = config.get('cci_period', 20)
        
        # ML Meta-labeling Filter (Optional)
        self.ml_dynamic_zscore = config.get('ml_dynamic_zscore', False)

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
            if getattr(self, '_vwap_logged_warmup', False) is False:
                self._log_no_signal("vwap_deviation | No signal: Insufficient data")
                self._vwap_logged_warmup = True
            return None
        self._vwap_logged_warmup = False

        # --- LATENCY FIX ---
        # Only need the trailing window. 800 bars ≈ one full active session on 1m.
        bars = bars.tail(800)

        # ── ICT Kill Zone guard ───────────────────────────────────────────────
        # VWAP mean reversion requires a ranging session.
        # London open (07–10 UTC) and NY open (12–15 UTC) are institutional kill zones
        # where price trends aggressively — mean reversion has no edge there.
        try:
            bar_hour = bars.index[-1].hour
            if any(start <= bar_hour < end for start, end in ((7, 10), (12, 15))):
                self._log_no_signal(f"Kill zone active (hour={bar_hour} UTC) — no mean reversion")
                return None
        except AttributeError:
            pass  # index not datetime — skip check

        # ── HTF regime check (1H resampled from 1m bars) ─────────────────────
        # The same bars resampled to 1H give a noise-free regime read.
        # If the 1H regime is TREND, mean reversion on 1m has no edge.
        try:
            h1_bars = (
                bars
                .resample('1h')
                .agg({'open': 'first', 'high': 'max', 'low': 'min',
                      'close': 'last', 'volume': 'sum'})
                .dropna(subset=['open', 'close'])
            )
            if len(h1_bars) >= 20:
                h1_regime = self.regime_filter.classify(h1_bars)
                if h1_regime == MarketRegime.TREND:
                    self._log_no_signal(f"H1 regime is TREND — skipping mean reversion entry")
                    return None
        except Exception:
            pass  # resampling not possible (e.g. integer index) — fall through to 1m check

        # ── 1m regime check (final gate) ─────────────────────────────────────
        regime = self.regime_filter.classify(bars)
        if regime != self.only_in_regime:
            self._log_no_signal(f"Regime is {regime.value}, need {self.only_in_regime.value}")
            return None

        # ── HTF directional bias check ────────────────────────────────────────
        # Only take LONG signals when price is in the lower half of the recent 4H range
        # (ICT: buy discount, sell premium). Buying above the 4H midpoint into a
        # VWAP deviation = buying premium, which contradicts mean reversion thesis.
        try:
            h4_bars = (
                bars
                .resample('4h')
                .agg({'open': 'first', 'high': 'max', 'low': 'min',
                      'close': 'last', 'volume': 'sum'})
                .dropna(subset=['open', 'close'])
            )
            if len(h4_bars) >= 2:
                h4_high = h4_bars['high'].iloc[-1]
                h4_low  = h4_bars['low'].iloc[-1]
                h4_mid  = (h4_high + h4_low) / 2
                current_close_raw = bars['close'].iloc[-1]
                # Block longs above H4 midpoint (premium) and shorts below it (discount)
                _bias_ok = True
                if current_close_raw > h4_mid:
                    _bias_ok = False  # will block BUY signal below
                self._h4_bias_ok    = _bias_ok
                self._h4_bias_above = current_close_raw > h4_mid
        except Exception:
            self._h4_bias_ok    = True
            self._h4_bias_above = None

        # Dynamic ML-driven VWAP Standard Deviation Overrides
        effective_multiplier = self.atr_multiplier
        if self.ml_dynamic_zscore:
             ml_multiplier = self.config.get('diagnostics', {}).get('vwap_dynamic_mult', None)
             if ml_multiplier is not None and ml_multiplier > 0:
                  effective_multiplier = ml_multiplier

        # Calculate indicators
        vwap, upper_band, lower_band = Indicators.vwap_deviation(
            bars,
            atr_multiplier=effective_multiplier
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
            if getattr(self, '_vwap_logged_calc_error', False) is False:
                self._log_no_signal("No signal: VWAP, ATR, RSI or CCI calculation failed")
                self._vwap_logged_calc_error = True
            return None
        self._vwap_logged_calc_error = False

        # Volume check (when data is available)
        if 'volume' in bars.columns:
            current_volume = bars['volume'].iloc[-1]
            avg_volume = bars['volume'].rolling(20).mean().iloc[-1]
            if avg_volume > 0 and current_volume < avg_volume * self.min_volume_ratio:
                self._log_no_signal("Volume too low")
                return None

        # --- Oversold BUY signal ---
        if current_close < current_lower:

            # H4 bias: only buy in discount (below H4 midpoint)
            if getattr(self, '_h4_bias_above', None) is True:
                self._log_no_signal("H4 premium zone — no BUY in VWAP discount")
                return None

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

            deviation_pct = (current_vwap - current_close) / current_vwap * 100
            rsi_extreme = max(0, (self.rsi_oversold_entry - current_rsi) / self.rsi_oversold_entry)
            cci_extreme = max(0, (-current_cci - 100) / 100)
            strength = min(deviation_pct / 2 + rsi_extreme * 0.2 + cci_extreme * 0.2, 1.0)

            return self._create_signal(
                side=OrderSide.BUY,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'entry_reason': 'oversold_rsi_cci_below_vwap_band',
                    'max_hold_minutes': self.max_hold_minutes,
                }
            )

        # --- Overbought SELL signal ---
        if current_close > current_upper:

            # H4 bias: only sell in premium (above H4 midpoint)
            if getattr(self, '_h4_bias_above', None) is False:
                self._log_no_signal("H4 discount zone — no SELL in VWAP premium")
                return None

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

            deviation_pct = (current_close - current_vwap) / current_vwap * 100
            rsi_extreme = max(0, (current_rsi - self.rsi_overbought_entry) / (100 - self.rsi_overbought_entry))
            cci_extreme = max(0, (current_cci - 100) / 100)
            strength = min(deviation_pct / 2 + rsi_extreme * 0.2 + cci_extreme * 0.2, 1.0)

            return self._create_signal(
                side=OrderSide.SELL,
                strength=strength,
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'entry_reason': 'overbought_rsi_cci_above_vwap_band',
                    'max_hold_minutes': self.max_hold_minutes,
                }
            )

        # Price within bands
        self._log_no_signal(
            f"Price {current_close:.2f} within VWAP bands "
            f"[{current_lower:.2f} – {current_upper:.2f}]")
        return None
