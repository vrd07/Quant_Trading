"""Wavelet-Fourier hybrid cycle strategy (spec §5.3).

The engine in src/cycles does the mathematics; this class does nothing but map
its state onto a Signal. Keeping it that thin is deliberate: the interesting
failure modes all live in the engine, and the engine is testable without any of
the strategy scaffolding.

Every entry needs three independent confirmations -- structural (price vs the
delay-compensated DWT trend), cyclic (Goertzel phase), and regime. Two out of
three is not a trade.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import OrderSide
from ..core.types import Signal, Symbol
from ..cycles.correlation import CorrelationFilter, mtf_alignment
from ..cycles.cycle_tracker import CycleState, CycleTracker
from ..cycles.regime import CycleRegime, RegimeDetector, RegimeState
from ..data.indicators import Indicators
from .base_strategy import BaseStrategy

_TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
# Spec §2 higher-timeframe anchors, resampled from the primary bars.
_ANCHORS = {"gold": ("1h", "4h"), "btc": ("4h", "1D")}


class WaveletCycleStrategy(BaseStrategy):
    def __init__(self, symbol: Symbol, config: Dict[str, Any]) -> None:
        super().__init__(symbol, config)
        self.asset_preset = config.get("asset_preset", "gold")
        self.allowed_symbols = config.get("allowed_symbols", ["XAUUSD"])
        self.atr_period = int(config.get("atr_period", 14))
        self.entry_dev_atr = float(config.get("entry_dev_atr", 0.5))
        self.rr = float(config.get("rr", 2.0))
        self.min_strength = float(config.get("min_strength", 0.3))
        self.timeframe = config.get("timeframe", "15m")
        self.tf_minutes = _TF_MINUTES.get(self.timeframe, 15)
        self.anchor_ema = int(config.get("anchor_ema_period", 50))

        tracker_kwargs = {k: config[k] for k in
                          ("window", "min_prominence", "stability_tol", "stability_windows")
                          if k in config}
        self._tracker = CycleTracker.for_asset(self.asset_preset, **tracker_kwargs)
        self._regime = RegimeDetector(atr_period=self.atr_period)
        # Gold gates on |rho| vs the dollar; BTC gates on signed rho vs NQ (spec §6.3).
        self._corr = CorrelationFilter(
            window=int(config.get("correlation_window", 20)),
            threshold=float(config.get("correlation_threshold",
                                       0.7 if self.asset_preset == "gold" else 0.8)),
            use_abs=(self.asset_preset == "gold"),
        )
        self._proxy: pd.Series | None = None

    def get_name(self) -> str:
        return "wavelet_cycle"

    def set_proxy_series(self, series: "pd.Series | None") -> None:
        """Inject the macro proxy (inverted EURUSD for gold, NAS100 for BTC).

        Unset live, where the filter degrades to pass-through: a correlation
        veto that silently fires on a missing feed is worse than none at all.
        """
        self._proxy = series

    def _anchors(self, bars: pd.DataFrame):
        """Resample only. Whether an anchor is long enough to have an opinion is
        _slope_bias's call (it returns neutral below ema_period + 2); deciding it
        twice, in two places, just means one of them can drift out of step."""
        a1, a2 = _ANCHORS.get(self.asset_preset, ("1h", "4h"))
        def resample(rule):
            s = bars["close"].resample(rule, label="left", closed="left").last().dropna()
            return s if len(s) else None
        return resample(a1), resample(a2)

    def _symbol_allowed(self) -> bool:
        # Broker tickers carry suffixes (XAUUSD.p); prefix-match, do not compare equal.
        ticker = (self.symbol.ticker or "").upper()
        return any(ticker.startswith(a.upper()) for a in self.allowed_symbols)

    def _side(self, cyc: CycleState, reg: RegimeState, atr: float) -> Optional[OrderSide]:
        pos = cyc.phase.position
        dev = cyc.deviation
        if not (dev == dev):  # NaN guard
            return None
        if reg.regime is CycleRegime.TREND:
            if dev > 0 and 0.50 <= pos < 0.75:
                return OrderSide.BUY
            if dev < 0 and 0.00 <= pos < 0.25:
                return OrderSide.SELL
            return None
        if reg.regime is CycleRegime.MEAN_REVERT:
            threshold = self.entry_dev_atr * atr
            at_trough = 0.40 <= pos <= 0.60
            at_peak = pos >= 0.90 or pos <= 0.10
            if dev < -threshold and at_trough:
                return OrderSide.BUY
            if dev > threshold and at_peak:
                return OrderSide.SELL
        return None

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        if not self.is_enabled() or not self._symbol_allowed():
            return None
        if len(bars) < self.atr_period * 3:
            self._log_no_signal("insufficient_history")
            return None

        cyc = self._tracker.update(bars["close"])
        if not cyc.tradeable or cyc.phase is None or cyc.period is None:
            self._log_no_signal(f"cycle_{cyc.reason}")
            return None

        reg = self._regime.classify(bars)
        if reg.regime is CycleRegime.UNCERTAIN:
            # Spec offers "reduce 80% or flat". Flat: a position you do not
            # believe in is a slow way to lose, not a cheap way to stay involved.
            self._log_no_signal("regime_uncertain")
            return None

        atr = float(Indicators.atr(bars, period=self.atr_period).iloc[-1])
        if not (atr > 0):
            self._log_no_signal("atr_unavailable")
            return None

        side = self._side(cyc, reg, atr)
        if side is None:
            self._log_no_signal("no_confluence")
            return None

        anchor1, anchor2 = self._anchors(bars)
        mtf = mtf_alignment(bars["close"], anchor1, anchor2, ema_period=self.anchor_ema)
        if (side is OrderSide.BUY and not mtf.allow_long) or \
           (side is OrderSide.SELL and not mtf.allow_short):
            self._log_no_signal("htf_conflict")
            return None

        veto = self._corr.evaluate(bars["close"], self._proxy)

        entry = float(bars["close"].iloc[-1])
        stop_mult = self._regime.stop_atr_multiplier(reg)
        stop_dist = stop_mult * atr
        if side is OrderSide.BUY:
            stop = entry - stop_dist
            target = cyc.trend if reg.regime is CycleRegime.MEAN_REVERT else entry + self.rr * stop_dist
        else:
            stop = entry + stop_dist
            target = cyc.trend if reg.regime is CycleRegime.MEAN_REVERT else entry - self.rr * stop_dist

        # A mean-revert target on the wrong side of entry means the deviation and
        # the phase disagree; drop the trade rather than invert the geometry.
        if (side is OrderSide.BUY and target <= entry) or (side is OrderSide.SELL and target >= entry):
            self._log_no_signal("target_behind_entry")
            return None

        # Size multipliers compound: regime confidence x HTF agreement x macro veto.
        size_mult = reg.size_multiplier * mtf.size_multiplier * veto.size_multiplier
        strength = min(1.0, cyc.prominence / 4.0) * size_mult
        if strength < self.min_strength:
            self._log_no_signal("weak_strength")
            return None

        signal = self._create_signal(
            side=side,
            strength=float(strength),
            regime=reg.to_market_regime(),
            entry_price=entry,
            stop_loss=stop,
            take_profit=target,
            metadata={
                "cycle_period": float(cyc.period),
                "cycle_prominence": float(cyc.prominence),
                "cycle_position": float(cyc.phase.position),
                "cycle_reason": cyc.reason,
                "regime": reg.regime.value,
                "group_delay": float(cyc.group_delay),
                "deviation": float(cyc.deviation),
                "atr": atr,
                "stop_atr_multiplier": float(stop_mult),
                "size_multiplier": float(size_mult),
                "mtf_bias": int(mtf.bias),
                "macro_correlation": float(veto.value),
                "time_stop_minutes": int(math.ceil(1.5 * cyc.period * self.tf_minutes)),
                # Keeps the volatility-targeted ATR stop; without this the
                # execution engine's BudgetSL rewrites it off the $ budget and
                # the §7.2 stop geometry stops existing.
                "preserve_structural_sl": True,
            },
        )
        # Signal.timestamp defaults to now(); in replay that silently destroys
        # every look-ahead check. Always stamp from the bar.
        signal.timestamp = bars.index[-1].to_pydatetime()
        return signal
