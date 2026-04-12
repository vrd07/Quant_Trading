"""
VWAP Strategy - Intraday mean reversion around session-anchored VWAP.

Research basis (2025-2026):
- Zarattini & Aziz (SSRN 4631351): reversion from deviation bands has edge;
  pure VWAP crossover does not. StdDev bands show 67% win rate, $0.95/$ expectancy.
- Bialkowski et al. (JBF): dynamic volume-adjusted VWAP reduces tracking error vs static.
- XAUUSD jump study (ScienceDirect 2025): 34% of intraday gold jumps = macro news;
  downside jumps average -1.78% — news blackout critical.
- GLD ETF predictability (PMC): 5th half-hour and penultimate half-hour have genuine
  predictive edge; GVZ R² = 3.24% vs SPY's 0.03%.
- Consensus: 2.0σ band for gold, 1.5σ for EURUSD; session-anchored VWAP from London
  open (07:00 UTC) is the empirically correct reference — not rolling VWAP over
  multi-session history.
- Failure condition: two consecutive closes beyond band without rejection = trend, not
  reversion — skip entry.

Entry Logic:
- Session-anchored VWAP (London open 07:00 UTC, or NY re-anchor 12:00 UTC)
- StdDev bands: ±band_std_mult × rolling_std (configurable; default 2.0 for XAUUSD)
- Price outside band + RSI extreme + CCI extreme + no two-bar trend signal
- H1 regime must NOT be TREND
- H4 directional bias (ICT premium/discount)
- No entry in ICT Kill Zones (London/NY open hours)

Exit Logic:
- Take profit: VWAP ± 0.5σ (partial convergence — not waiting for full VWAP tag)
- Stop loss: stop_atr_multiplier × ATR from entry
- Time stop: max_hold_minutes (45 min default; reversion thesis expires)
"""

from typing import Optional, Tuple
import pandas as pd

from .base_strategy import BaseStrategy
from .regime_filter import RegimeFilter
from ..core.types import Symbol, Signal
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


# Session anchor hours in UTC — ordered by priority (most recent wins)
_SESSION_ANCHORS_UTC = (12, 7, 1)  # NY open, London open, Asian open


def _compute_session_vwap(
    bars: pd.DataFrame,
    std_mult: float,
    partial_std_mult: float,
) -> Tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
    """
    Compute session-anchored VWAP with StdDev bands.

    Finds the most recent session open (NY 12:00, London 07:00, or Asian 01:00 UTC)
    in the bar index and anchors VWAP from that point.  Falls back to the full bar
    window if no session boundary is found.

    Returns:
        (vwap, upper_band, lower_band, partial_tp_band)
        partial_tp_band is the 0.5σ convergence level used as TP target.
        All are None if volume is zero or index is not datetime.
    """
    try:
        bar_hours = bars.index.hour
    except AttributeError:
        return None, None, None, None

    # Find the most recent session anchor in the bar series
    anchor_pos = None
    for anchor_hour in _SESSION_ANCHORS_UTC:
        matches = (bar_hours == anchor_hour).nonzero()[0]
        if len(matches):
            anchor_pos = matches[-1]
            break

    session_bars = bars.iloc[anchor_pos:] if anchor_pos is not None else bars

    has_volume = (
        'volume' in session_bars.columns
        and session_bars['volume'].sum() > 0
    )

    typical = (session_bars['high'] + session_bars['low'] + session_bars['close']) / 3

    if has_volume:
        cum_vol = session_bars['volume'].cumsum()
        vwap_vals = (typical * session_bars['volume']).cumsum() / cum_vol
    else:
        # No volume data: equal-weight mean (still session-anchored)
        vwap_vals = typical.expanding().mean()

    # Rolling std dev of typical price from session open (window=20 bars)
    std_window = min(20, len(session_bars))
    rolling_std = typical.rolling(std_window, min_periods=5).std().fillna(
        typical.std()  # fallback for very short sessions
    )

    upper = vwap_vals + std_mult * rolling_std
    lower = vwap_vals - std_mult * rolling_std
    partial_upper = vwap_vals + partial_std_mult * rolling_std  # TP level for SELL
    partial_lower = vwap_vals - partial_std_mult * rolling_std  # TP level for BUY

    # Reindex to full bar index (NaN before session anchor — correct behaviour)
    return (
        vwap_vals.reindex(bars.index),
        upper.reindex(bars.index),
        lower.reindex(bars.index),
        partial_lower.reindex(bars.index),  # returned as single series for BUY TP
    )


class VWAPStrategy(BaseStrategy):
    """
    VWAP deviation mean reversion.

    Uses session-anchored VWAP (London/NY open) with StdDev bands.
    Empirically superior to rolling VWAP with ATR bands for intraday
    ranging conditions on XAUUSD and EURUSD.
    """

    def __init__(self, symbol: Symbol, config: dict):
        super().__init__(symbol, config)

        self.atr_period        = config.get('atr_period', 14)
        self.band_std_mult     = config.get('band_std_mult', 2.0)   # 2.0σ for gold, 1.5σ for EURUSD
        self.partial_std_mult  = config.get('partial_std_mult', 0.5)  # TP at 0.5σ convergence
        self.min_volume_ratio  = config.get('min_volume_ratio', 1.0)
        self.only_in_regime    = MarketRegime[config.get('only_in_regime', 'RANGE')]
        self.max_hold_minutes  = config.get('max_hold_minutes', 45)

        # RSI/CCI confirmation thresholds
        self.rsi_oversold_entry  = config.get('rsi_oversold_entry', 35)
        self.rsi_overbought_entry = config.get('rsi_overbought_entry', 65)
        self.cci_oversold_entry  = config.get('cci_oversold_entry', -100)
        self.cci_overbought_entry = config.get('cci_overbought_entry', 100)
        self.cci_period          = config.get('cci_period', 20)
        self.allowed_hours       = config.get('allowed_hours', None)  # e.g. [2, 11, 15, 16, 19]

        self.regime_filter = RegimeFilter()

        # H1/H4 resampling cache — avoids O(N) resample on every 1m tick.
        # H1 only changes every ~60 bars; H4 every ~240 bars.
        self._h1_last_len: int = 0
        self._h1_regime: MarketRegime = MarketRegime.UNKNOWN
        self._h4_last_len: int = 0
        self._h4_bias_above: Optional[bool] = None

    def get_name(self) -> str:
        return "vwap_deviation"

    # ── H1/H4 helpers (cached) ──────────────────────────────────────────────

    def _get_h1_regime(self, bars: pd.DataFrame) -> MarketRegime:
        if len(bars) >= self._h1_last_len + 60:
            try:
                h1 = (
                    bars.resample('1h')
                    .agg({'open': 'first', 'high': 'max',
                          'low': 'min', 'close': 'last', 'volume': 'sum'})
                    .dropna(subset=['open', 'close'])
                )
                if len(h1) >= 20:
                    self._h1_regime = self.regime_filter.classify(h1)
            except Exception:
                pass
            self._h1_last_len = len(bars)
        return self._h1_regime

    def _get_h4_bias(self, bars: pd.DataFrame, current_close: float) -> Optional[bool]:
        """Returns True if price is in H4 premium (above midpoint), False if discount."""
        if len(bars) >= self._h4_last_len + 240:
            try:
                h4 = (
                    bars.resample('4h')
                    .agg({'open': 'first', 'high': 'max',
                          'low': 'min', 'close': 'last', 'volume': 'sum'})
                    .dropna(subset=['open', 'close'])
                )
                if len(h4) >= 2:
                    mid = (h4['high'].iloc[-1] + h4['low'].iloc[-1]) / 2
                    self._h4_bias_above = current_close > mid
            except Exception:
                pass
            self._h4_last_len = len(bars)
        return self._h4_bias_above

    # ── Main signal logic ───────────────────────────────────────────────────

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled():
            return None

        min_bars = max(self.atr_period + 5, 25, self.cci_period + 5)
        if len(bars) < min_bars:
            if not getattr(self, '_logged_warmup', False):
                self._log_no_signal("Insufficient bars for warmup")
                self._logged_warmup = True
            return None
        self._logged_warmup = False

        bars = bars.tail(800)

        # ── ICT Kill Zone guard ───────────────────────────────────────────
        # London open (07–10 UTC) and NY open (12–15 UTC) are trend-driving
        # institutional kill zones. VWAP reversion has no edge here.
        bar_hour = self._get_bar_hour(bars)
        if bar_hour is not None and any(s <= bar_hour < e for s, e in ((7, 10), (12, 15))):
            self._log_no_signal(f"Kill zone (hour={bar_hour} UTC)")
            return None

        # Allowed-hour filter (data-driven session gate)
        if self.allowed_hours is not None and bar_hour is not None and bar_hour not in self.allowed_hours:
            self._log_no_signal(f"Outside allowed_hours (hour={bar_hour})")
            return None

        # ── Regime ────────────────────────────────────────────────────────
        # Regime filter removed — the 2σ band + RSI/CCI extremes already
        # ensure we only enter on extended moves. The RegimeFilter blocked
        # 84% of bars as TREND on XAUUSD 2025-2026, leaving zero trades.
        regime = self.ml_regime if self.ml_regime is not None else MarketRegime.RANGE

        # ── Session-anchored VWAP with StdDev bands ───────────────────────
        vwap, upper_band, lower_band, partial_lower = _compute_session_vwap(
            bars, self.band_std_mult, self.partial_std_mult
        )
        if vwap is None or vwap.iloc[-1] is None or pd.isna(vwap.iloc[-1]):
            self._log_no_signal("Session VWAP unavailable")
            return None

        atr = Indicators.atr(bars, period=self.atr_period)
        rsi = Indicators.rsi(bars, period=14)
        cci = Indicators.cci(bars, period=self.cci_period)

        current_close  = bars['close'].iloc[-1]
        current_vwap   = vwap.iloc[-1]
        current_upper  = upper_band.iloc[-1]
        current_lower  = lower_band.iloc[-1]
        current_atr    = atr.iloc[-1]
        current_rsi    = rsi.iloc[-1]
        current_cci    = cci.iloc[-1]

        if any(pd.isna(v) for v in (current_vwap, current_upper, current_lower,
                                     current_atr, current_rsi, current_cci)):
            self._log_no_signal("Indicator NaN")
            return None

        # ADX trend guard: don't mean-revert in a strong trend
        adx = Indicators.adx(bars, period=14)
        current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 25.0
        if current_adx > 30.0:
            self._log_no_signal(f"VWAP: ADX too high ({current_adx:.1f}) — strong trend")
            return None

        # ── H4 directional bias (ICT premium/discount) ────────────────────
        h4_bias = self._get_h4_bias(bars, current_close)

        # ── Volume filter ─────────────────────────────────────────────────
        if 'volume' in bars.columns:
            avg_vol = bars['volume'].rolling(20).mean().iloc[-1]
            if avg_vol > 0 and bars['volume'].iloc[-1] < avg_vol * self.min_volume_ratio:
                self._log_no_signal("Volume too low")
                return None

        # ── Band width (for normalised strength) ─────────────────────────
        band_half_width = max(current_vwap - current_lower, 1e-6)

        # ── BUY signal ────────────────────────────────────────────────────
        if current_close < current_lower:

            # H4 bias: only buy in discount zone (below H4 midpoint)
            if h4_bias is True:
                self._log_no_signal("H4 premium — no BUY")
                return None

            # Simons failure filter: two consecutive closes below band = trend down
            if (len(bars) >= 2
                    and not pd.isna(lower_band.iloc[-2])
                    and bars['close'].iloc[-2] < lower_band.iloc[-2]):
                self._log_no_signal("Two consecutive closes below band — trend, not reversion")
                return None

            if current_rsi >= self.rsi_oversold_entry:
                self._log_no_signal(f"RSI {current_rsi:.1f} not oversold")
                return None
            if current_cci >= self.cci_oversold_entry:
                self._log_no_signal(f"CCI {current_cci:.1f} not oversold")
                return None

            # Normalised strength: deviation from band as fraction of band half-width,
            # weighted with oscillator extremes. Bounded [0, 1].
            dev_norm     = min((current_lower - current_close) / band_half_width, 1.0)
            rsi_norm     = max(0.0, (self.rsi_oversold_entry - current_rsi) / self.rsi_oversold_entry)
            cci_norm     = max(0.0, (-current_cci - 100) / 200)
            strength     = dev_norm * 0.5 + rsi_norm * 0.25 + cci_norm * 0.25
            deviation_pct = (current_vwap - current_close) / current_vwap * 100

            # TP target: VWAP - 0.5σ (partial convergence, not waiting for full VWAP tag)
            partial_tp = float(partial_lower.iloc[-1]) if partial_lower is not None else float(current_vwap)

            return self._create_signal(
                side=OrderSide.BUY,
                strength=min(strength, 1.0),
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'partial_tp': partial_tp,
                    'entry_reason': 'oversold_below_session_vwap_band',
                    'max_hold_minutes': self.max_hold_minutes,
                }
            )

        # ── SELL signal ───────────────────────────────────────────────────
        if current_close > current_upper:

            # H4 bias: only sell in premium zone (above H4 midpoint)
            if h4_bias is False:
                self._log_no_signal("H4 discount — no SELL")
                return None

            # Simons failure filter: two consecutive closes above band = trend up
            if (len(bars) >= 2
                    and not pd.isna(upper_band.iloc[-2])
                    and bars['close'].iloc[-2] > upper_band.iloc[-2]):
                self._log_no_signal("Two consecutive closes above band — trend, not reversion")
                return None

            if current_rsi <= self.rsi_overbought_entry:
                self._log_no_signal(f"RSI {current_rsi:.1f} not overbought")
                return None
            if current_cci <= self.cci_overbought_entry:
                self._log_no_signal(f"CCI {current_cci:.1f} not overbought")
                return None

            dev_norm     = min((current_close - current_upper) / band_half_width, 1.0)
            rsi_norm     = max(0.0, (current_rsi - self.rsi_overbought_entry) / (100 - self.rsi_overbought_entry))
            cci_norm     = max(0.0, (current_cci - 100) / 200)
            strength     = dev_norm * 0.5 + rsi_norm * 0.25 + cci_norm * 0.25
            deviation_pct = (current_close - current_vwap) / current_vwap * 100

            partial_tp = float(upper_band.iloc[-1] - (self.partial_std_mult / self.band_std_mult) * band_half_width)

            return self._create_signal(
                side=OrderSide.SELL,
                strength=min(strength, 1.0),
                regime=regime,
                entry_price=float(current_close),
                metadata={
                    'strategy': 'vwap_deviation',
                    'vwap': float(current_vwap),
                    'deviation_pct': float(deviation_pct),
                    'rsi': float(current_rsi),
                    'cci': float(current_cci),
                    'atr': float(current_atr),
                    'partial_tp': partial_tp,
                    'entry_reason': 'overbought_above_session_vwap_band',
                    'max_hold_minutes': self.max_hold_minutes,
                }
            )

        self._log_no_signal(
            f"Close {current_close:.2f} within bands [{current_lower:.2f}–{current_upper:.2f}]")
        return None
