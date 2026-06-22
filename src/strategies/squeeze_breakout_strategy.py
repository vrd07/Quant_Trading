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
  3. BREAK: ATR expanding by >= atr_expansion_ratio (default 1.05x, not a mere
     uptick) AND close clears the coil's Donchian(20) high (BUY) or low (SELL)
     by >= min_penetration_atr * ATR (default 0.1) — enter with the break.
     (Loser-profile filters, 2026-06-22: shallow breaks and weak expansions were
     the bleed; both gates lift IS+OOS PF — see analyze_squeeze_losers.py.)
  4. HTF-trend gate: only take the break if it's ALIGNED with the slow EMA
     (htf_ema_period, default 400 on 15m ~ EMA100 1H) — BUY above, SELL below.
     Counter-trend breaks were the whipsaw bleed; aligning roughly halves DD and
     lifts PF on IS+OOS (research_squeeze_htf_gate.py). Set 0 to disable.
  5. SL = sl_atr_multiplier * ATR (~33pts = 3.0x median 2026 15m ATR).
     TP = SL * rr  (rr = 2.0 — the RR that makes the edge).
  6. cooldown_bars between entries (one fresh coil per breakout).

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
        # Loser-profile filters (validated 2026-06-22, scripts/analyze_squeeze_losers.py):
        # the bleed was "fakeout" breaks — the close barely cleared the Donchian edge
        # and/or vol barely woke up. Two cheap gates fix it on BOTH IS+OOS:
        #   * penetration must clear the channel by >= min_penetration_atr * ATR
        #     (shallow breaks <0.1 ATR: WR 22%, net -$1,246 — fade straight back),
        #   * ATR must expand by >= atr_expansion_ratio vs the prior bar, not merely
        #     tick up (jump 1.02-1.05x: net -$1,125; >1.10x: +$3,096).
        # Combined: full-span PF 1.15->~1.37 IS / 1.05->1.36 OOS (clears the 1.10 bar).
        self.min_penetration_atr = float(config.get('min_penetration_atr', 0.1))
        self.atr_expansion_ratio = float(config.get('atr_expansion_ratio', 1.05))
        # HTF-trend gate (validated 2026-06-22, scripts/research_squeeze_htf_gate.py):
        # only take a breakout ALIGNED with the higher-timeframe trend — BUY when
        # close is above the slow EMA, SELL when below. The residual drawdown was a
        # counter-trend whipsaw (coil->break->reverse); aligning to trend dodges it.
        # EMA400 on 15m bars ~ EMA100 on 1H (~100h of trend). Walk-forward this
        # roughly HALVES DD and lifts PF on BOTH IS+OOS (2026 1.38->1.91, 2025
        # 1.42->1.67) while netting MORE $ on FEWER, higher-quality trades. Computed
        # on the 15m close itself — no extra data feed. Set htf_ema_period: 0 to
        # disable. Side-only (no slope term — slope overfits IS, see research).
        self.htf_ema_period = int(config.get('htf_ema_period', 400))
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
        # Need enough history for the slow HTF-EMA to be well-formed, else its
        # seed dominates and the trend gate misfires on the early window.
        min_bars = max(self.pct_window + self.donch + 5, self.htf_ema_period)
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
        # Require a GENUINE vol surge, not a mere uptick — weak expansion is a fakeout.
        atr_prev = float(atr.iloc[-2])
        if atr_prev <= 0 or atr_now < self.atr_expansion_ratio * atr_prev:
            self._log_no_signal("ATR not expanding enough")
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

        # Reject shallow "fakeout" breaks — the close must clear the channel edge
        # by a real margin, else it mean-reverts straight back through the stop.
        if pen < self.min_penetration_atr * atr_now:
            self._log_no_signal("break too shallow")
            return None

        # HTF-trend gate: only take breaks aligned with the slow EMA (continuation).
        # Counter-trend breaks are the whipsaw bleed — skip them.
        if self.htf_ema_period > 0:
            htf = float(close.ewm(span=self.htf_ema_period, adjust=False).mean().iloc[-1])
            if (side == OrderSide.BUY and c <= htf) or (side == OrderSide.SELL and c >= htf):
                self._log_no_signal("against HTF trend")
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
