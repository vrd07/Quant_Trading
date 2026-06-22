"""
Stochastic Pullback Continuation (2R/3R) Strategy — XAUUSD 15m.

Implements the ACY "How to Trade Gold Using Stochastics (2R/3R)" method
(acy.com/.../how-to-trade-gold-using-stochastics-2r-3r-j-o-100124). The edge is
NOT a stochastic reversal — it is a TREND-CONTINUATION pullback:

  1. TREND filter:  EMA(`trend_ema`) slope + price at least `min_ema_dist_atr`
                    * ATR away from the EMA on the trend side (real separation,
                    not chop around the mean — the loser cluster, see
                    analyze_stoch_losers.py).
  2. PULLBACK:      Stochastic(14,3) %K "cools off" into the 20-30 zone (long) /
                    70-80 zone (short) within the last `arm_window` bars — a
                    momentum reset, not an extreme reversal call.
  3. ENTRY:         price BREAKS OUT of the recent `range_bars`-bar consolidation
                    in the trend direction (close clears the range hi/lo) with
                    %K back above %D (long) / below %D (short).
  4. STOP:          STRUCTURAL — just behind the consolidation range (range low
                    for long / range high for short), + `buffer_pts`.
  5. TARGET:        fixed `rr`-R of that structural stop distance (RR2.0 = the edge).

Research basis (scripts/research_stoch_pullback.py, 2026-06-22, walk-forward
2026 IS + 2025 OOS, strict fills, structural stop):
  XAUUSD 15m RR2.0 — 2026 PF 1.31 / 2025 OOS 1.19 (risk-bypassed). The London+NY
  session filter (UTC 07-20) is additive: it ~halves drawdown (2026 −26% → −14%)
  while holding PF ~1.27/1.28 both years, so it is baked in here.

  ⚠️ ACCOUNT-SIZE DEPENDENT. Under --enforce-risk the $5k 5% trailing-DD kill
  switch ($250 = ~3 min-lot losses, since min_lot 0.02 on gold's wide structural
  stops already risks ~$78/trade) halts the 2026 run after 10 trades → PF 0.44.
  The SAME signals survive enforcement at $25k+ (2026 PF 1.13, DD −2.9%). Shipped
  by explicit user decision as a diversifier stream with the in-code XAUUSD gate
  as containment — same posture as london_breakout / monday_drift / squeeze_breakout,
  NOT because it cleared the $5k promotion gate. Loosely correlated with kalman /
  squeeze_breakout (same instrument).

Hard symbol gate: validated on XAUUSD ONLY. Stateless except a cooldown latch on
the last signal time.
"""

from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from ..data.indicators import Indicators
from .base_strategy import BaseStrategy


class StochPullbackStrategy(BaseStrategy):
    """Stochastic trend-continuation pullback. Validated for XAUUSD 15m only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.trend_ema = int(config.get('trend_ema', 50))
        self.stoch_period = int(config.get('stoch_period', 14))
        self.pull_lo = float(config.get('pull_lo', 20.0))   # don't chase below this
        self.pull_hi = float(config.get('pull_hi', 30.0))   # cool-off zone ceiling
        self.arm_window = int(config.get('arm_window', 10))  # pullback memory (bars)
        self.range_bars = int(config.get('range_bars', 5))   # consolidation lookback
        self.buffer_pts = float(config.get('buffer_pts', 0.10))
        self.min_stop_pts = float(config.get('min_stop_pts', 2.0))
        # Trend-extension filter (validated 2026-06-22, scripts/analyze_stoch_losers.py):
        # the bleed was entries taken while price sat ON the EMA — a "continuation"
        # pullback with no actual trend separation (|ema_dist| 0.3-1.0 ATR: WR ~20%,
        # -$781). Require price to be at least min_ema_dist_atr * ATR away from the
        # EMA in the trade direction = a genuinely established/extended trend, not
        # chop around the mean. Walk-forward lifts PF on BOTH years (2026 1.27->1.39,
        # 2025 1.31->1.37) and cuts DD; >1.25 starts to overfit IS. Set 0 to disable.
        self.min_ema_dist_atr = float(config.get('min_ema_dist_atr', 1.0))
        self.atr_period = int(config.get('atr_period', 14))
        self.rr = float(config.get('rr', 2.0))               # TP = rr * structural SL
        self.cooldown_bars = int(config.get('cooldown_bars', 5))
        # Embedded session filter (UTC entry-hour gate). London open → NY = the
        # high-liquidity gold window; research shows it ~halves drawdown. Set
        # session_start_hour: 0 / session_end_hour: 24 to trade all hours.
        self.session_start_hour = int(config.get('session_start_hour', 7))
        self.session_end_hour = int(config.get('session_end_hour', 21))
        self.timeframe_minutes = int(config.get('timeframe_minutes', 15))
        # Hard symbol gate: validated on XAUUSD only. Prefix match so the
        # broker's suffixed ticker (XAUUSDs) also passes.
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['XAUUSD'])
        )
        self._last_signal_ts = None   # cooldown latch

    def get_name(self) -> str:
        return "stoch_pullback"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        min_bars = self.trend_ema + self.arm_window + self.range_bars + 10
        if not self.enabled or len(bars) < min_bars:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on XAUUSD only — never trade other symbols

        # Session gate (UTC entry hour). Cheap; do it before the indicator math.
        ts = self._bar_timestamp(bars)
        if ts is not None:
            if not (self.session_start_hour <= ts.hour < self.session_end_hour):
                self._log_no_signal("outside session window")
                return None

        close = bars['close']
        high = bars['high']
        low = bars['low']

        ema = Indicators.ema(bars, period=self.trend_ema)
        k, d = Indicators.stochastic(bars, period=self.stoch_period)
        atr = Indicators.atr(bars, period=self.atr_period)

        c = float(close.iloc[-1])
        ema_now = float(ema.iloc[-1])
        ema_prev = float(ema.iloc[-6])
        k_now = float(k.iloc[-1])
        d_now = float(d.iloc[-1])
        atr_now = float(atr.iloc[-1])
        if pd.isna(ema_now) or pd.isna(k_now) or pd.isna(d_now) or pd.isna(atr_now) or atr_now <= 0:
            return None

        # Trend-extension gate: price must be a real distance from the EMA in the
        # trend direction, else it's chop around the mean (the loser cluster).
        ext = self.min_ema_dist_atr * atr_now
        up = c > ema_now + ext and ema_now > ema_prev       # established uptrend
        dn = c < ema_now - ext and ema_now < ema_prev       # established downtrend
        if not (up or dn):
            self._log_no_signal("no established/extended trend")
            return None

        # PULLBACK armed: %K dipped into the cool-off zone within the prior
        # arm_window bars (excluding the current breakout bar).
        prior_k = k.iloc[-(self.arm_window + 1):-1]
        long_armed = bool((prior_k <= self.pull_hi).any())
        short_armed = bool((prior_k >= (100.0 - self.pull_hi)).any())

        # CONSOLIDATION range = prior range_bars bars (exclude current bar).
        range_hi = float(high.iloc[-(self.range_bars + 1):-1].max())
        range_lo = float(low.iloc[-(self.range_bars + 1):-1].min())

        mom_up = k_now > d_now
        mom_dn = k_now < d_now

        if (up and long_armed and mom_up and c > range_hi
                and k_now > self.pull_lo):
            side = OrderSide.BUY
            stop = range_lo - self.buffer_pts
            dist = c - stop
            strength = max(0.0, min(1.0, 1.0 - k_now / 100.0))
        elif (dn and short_armed and mom_dn and c < range_lo
                and k_now < (100.0 - self.pull_lo)):
            side = OrderSide.SELL
            stop = range_hi + self.buffer_pts
            dist = stop - c
            strength = max(0.0, min(1.0, k_now / 100.0))
        else:
            self._log_no_signal("no pullback-continuation breakout")
            return None

        if dist < self.min_stop_pts:
            self._log_no_signal("structural stop too tight")
            return None

        # Cooldown latch (one entry per fresh breakout).
        if ts is not None and self._last_signal_ts is not None:
            elapsed_min = (ts - self._last_signal_ts).total_seconds() / 60.0
            if elapsed_min < self.cooldown_bars * self.timeframe_minutes:
                return None

        tp_dist = dist * self.rr
        if side == OrderSide.BUY:
            target = c + tp_dist
        else:
            target = c - tp_dist

        self._last_signal_ts = ts

        return self._create_signal(
            side=side,
            strength=strength,
            regime=MarketRegime.TREND,
            entry_price=c,
            stop_loss=stop,
            take_profit=target,
            metadata={
                'strategy': 'stoch_pullback',
                'mode': 'pullback_continuation',
                'stop_price': stop,           # RiskProcessor honors this verbatim
                'take_profit_price': target,
                # The STRUCTURAL stop + fixed RR is the edge — keep the execution
                # layer's BudgetSL from shrinking the stop to the dollar budget.
                'preserve_structural_sl': True,
                'stoch_k': k_now,
                'stoch_d': d_now,
                'ema': ema_now,
                'range_high': range_hi,
                'range_low': range_lo,
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
