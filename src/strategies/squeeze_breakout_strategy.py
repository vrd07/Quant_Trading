"""
Squeeze Breakout Strategy — volatility-coil → expansion breakout (XAUUSD 15m).

Research basis (scripts/research_squeeze_breakout.py + validate_squeeze_*.py,
2026-06-21, walk-forward 2026 IS + 2025 OOS, 5m→15m XAUUSD, costs charged):
  Inverts the RANGE-fade logic: wait for the market to COIL (low vol + flat
  Kalman line) then trade the EXPANSION breakout out of the coil's Donchian
  channel.

  Best cell SL33pts / RR2.0:  2026 PF 1.27 (+$3,120) → 2025 OOS PF 1.05 (+$660).
  RR is decisive — RR1.0 loses both years (breakouts need room); only RR2.0 is
  net-positive both years with no sign-flip. Cost-robust: strict 0.50/side fills
  barely dent it (the wide stop + low frequency dodge the usual breakout-chase
  slippage trap). The session filter was REFUTED (London/NY each win one year,
  lose the other) → trade ALL HOURS, no session gate inside the strategy.

  ⚠️ Standalone PF is MARGINAL (OOS 1.05, just under the 1.10 durability bar)
  and the full-span profile is quarter-dependent (2025Q3 PF 0.39). Shipped by
  explicit user decision as a diversifier-style stream, NOT because it cleared
  the promotion gate — same posture as london_breakout / monday_drift. It is on
  the SAME instrument as kalman (XAUUSD); the two are loosely correlated
  (~+0.13/+0.20) because the breakout rides what kalman's range-mode fades.

Mechanics:
  1. COIL: ATR(14) <= 20th pctile of the last 100 bars AND the Kalman line is
     flat (|kal - kal.shift(slope_bars)| <= flat_atr_mult * ATR).
  2. Was the market coiling at any point in the last `coil_lookback` bars
     (excluding the current bar)?
  3. BREAK: ATR expanding (atr > atr.shift(1)) AND close clears the coil's
     Donchian(20) high (BUY) or low (SELL) — enter with the break.
  4. SL = sl_atr_multiplier * ATR (~33pts = 3.0x median 2026 15m ATR).
     TP = SL * rr  (rr = 2.0 — the RR that makes the edge).
  5. cooldown_bars between entries (one fresh coil per breakout).

Hard symbol gate: validated on XAUUSD ONLY (same caveat as the rest of the
gold-15m research). Stateless except a cooldown latch on the last signal time.
"""

from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from ..data.indicators import Indicators
from .base_strategy import BaseStrategy


class SqueezeBreakoutStrategy(BaseStrategy):
    """Volatility-squeeze → breakout. Validated for XAUUSD 15m only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.atr_period = int(config.get('atr_period', 14))
        self.pct_window = int(config.get('pct_window', 100))
        self.pct = float(config.get('pct', 0.20))           # ATR <= 20th pctile = squeeze
        self.donch = int(config.get('donch', 20))
        self.slope_bars = int(config.get('slope_bars', 3))
        self.flat_atr_mult = float(config.get('flat_atr_mult', 0.5))
        self.coil_lookback = int(config.get('coil_lookback', 6))
        self.cooldown_bars = int(config.get('cooldown_bars', 8))
        self.kalman_q = float(config.get('kalman_q', 0.00001))
        self.kalman_r = float(config.get('kalman_r', 0.01))
        # SL geometry. `sl_points` = a FIXED price-point stop (default 33 = the
        # research geometry). This is DELIBERATELY the default, not an ATR
        # multiple: a 3.0x ATR stop floats wider in high-vol stretches and
        # destroys the breakout edge (validated — fixed 33pt = 2026 PF 1.20
        # through the engine; 3xATR = PF 0.99). Set `sl_points: null` in config
        # to fall back to `sl_atr_multiplier` x ATR (inferior; for research only).
        # TP = SL x rr (RR2.0 is the edge — RR1.0 loses both years).
        self.sl_atr_multiplier = float(config.get('sl_atr_multiplier', 3.0))
        _slp = config.get('sl_points', 33.0)
        self.sl_points = None if _slp is None else float(_slp)
        self.rr = float(config.get('rr', 2.0))
        self.timeframe_minutes = int(config.get('timeframe_minutes', 15))
        # Hard symbol gate: validated on XAUUSD only. Prefix match so the
        # broker's suffixed ticker (XAUUSDs) also passes.
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['XAUUSD'])
        )
        self._last_signal_ts = None   # cooldown latch

    def get_name(self) -> str:
        return "squeeze_breakout"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        min_bars = self.pct_window + self.donch + 5
        if not self.enabled or len(bars) < min_bars:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on XAUUSD only — never trade other symbols

        close = bars['close']
        high = bars['high']
        low = bars['low']

        atr = Indicators.atr(bars, period=self.atr_period)
        kal = Indicators.kalman_filter(close, q=self.kalman_q, r=self.kalman_r)

        atr_now = float(atr.iloc[-1])
        if atr_now <= 0 or pd.isna(atr_now):
            return None

        # --- COIL detection (only the last bar matters for a live decision) ---
        q_pct = atr.rolling(self.pct_window).quantile(self.pct)
        squeeze = atr <= q_pct
        flat = (kal - kal.shift(self.slope_bars)).abs() <= self.flat_atr_mult * atr
        coiling = squeeze & flat
        # Was the market coiling at any point in the last coil_lookback bars,
        # excluding the current bar?
        recent_window = coiling.shift(1).iloc[-self.coil_lookback:]
        if not bool(recent_window.fillna(False).any()):
            self._log_no_signal("no recent coil")
            return None

        # --- BREAK detection on the current bar ---
        donch_hi = float(high.iloc[-(self.donch + 1):-1].max())
        donch_lo = float(low.iloc[-(self.donch + 1):-1].min())
        atr_expand = atr_now > float(atr.iloc[-2])
        if not atr_expand:
            self._log_no_signal("ATR not expanding")
            return None

        c = float(close.iloc[-1])
        if c > donch_hi:
            side = OrderSide.BUY
            pen = c - donch_hi
        elif c < donch_lo:
            side = OrderSide.SELL
            pen = donch_lo - c
        else:
            self._log_no_signal("no Donchian break")
            return None

        # --- Cooldown latch (one entry per fresh coil) ---
        ts = self._bar_timestamp(bars)
        if ts is not None and self._last_signal_ts is not None:
            elapsed_min = (ts - self._last_signal_ts).total_seconds() / 60.0
            if elapsed_min < self.cooldown_bars * self.timeframe_minutes:
                return None

        # --- Sizing geometry: SL = sl_points (fixed) or k*ATR; TP = SL*RR ---
        sl_dist = float(self.sl_points) if self.sl_points is not None \
            else self.sl_atr_multiplier * atr_now
        tp_dist = sl_dist * self.rr
        if side == OrderSide.BUY:
            stop = c - sl_dist
            target = c + tp_dist
        else:
            stop = c + sl_dist
            target = c - tp_dist

        strength = float(min(max(pen, 0.0) / atr_now, 1.0))
        self._last_signal_ts = ts

        return self._create_signal(
            side=side,
            strength=strength,
            regime=MarketRegime.TREND,
            entry_price=c,
            stop_loss=stop,
            take_profit=target,
            metadata={
                'strategy': 'squeeze_breakout',
                'mode': 'breakout',
                'stop_price': stop,        # RiskProcessor honors this verbatim
                'take_profit_price': target,
                # The fixed SL/RR2.0 geometry IS the edge — keep the execution
                # layer's BudgetSL from shrinking the stop to the dollar budget.
                'preserve_structural_sl': True,
                'atr': atr_now,
                'donch_high': donch_hi,
                'donch_low': donch_lo,
                'penetration': pen,
                'rr': self.rr,
            },
        )

    @staticmethod
    def _bar_timestamp(bars: pd.DataFrame) -> Optional[pd.Timestamp]:
        """Last bar's timestamp from a DatetimeIndex or a `timestamp` column."""
        idx = bars.index[-1]
        if hasattr(idx, 'hour'):
            return pd.Timestamp(idx)
        if 'timestamp' in bars.columns and len(bars) > 0:
            try:
                return pd.Timestamp(bars['timestamp'].iloc[-1])
            except Exception:
                return None
        return None
