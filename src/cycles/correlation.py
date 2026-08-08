"""Macro-correlation veto (spec §6.3) and multi-timeframe alignment (spec §6.4).

Correlation is computed on RETURNS, never on levels: two unrelated random walks
show |rho| > 0.9 in levels roughly half the time, so a level-based filter would
veto at random and look like it was working.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CorrelationVeto:
    correlated: bool
    value: float           # NaN when the proxy series was unavailable
    size_multiplier: float


@dataclass(frozen=True)
class MTFAlignment:
    bias: int              # +1 bullish, -1 bearish, 0 neutral
    size_multiplier: float
    allow_long: bool
    allow_short: bool


class CorrelationFilter:
    def __init__(self, *, window: int = 20, threshold: float = 0.7,
                 size_multiplier: float = 0.5, use_abs: bool = True) -> None:
        self.window = window
        self.threshold = threshold
        self.size_multiplier = size_multiplier
        self.use_abs = use_abs

    def evaluate(self, primary: pd.Series, proxy: pd.Series | None) -> CorrelationVeto:
        """Pass-through (full size, NaN value) when the proxy is missing or short."""
        if proxy is None or len(primary) < self.window + 2 or len(proxy) < self.window + 2:
            return CorrelationVeto(False, float("nan"), 1.0)
        joined = pd.concat([primary.rename("a"), proxy.rename("b")], axis=1).dropna()
        if len(joined) < self.window + 2:
            return CorrelationVeto(False, float("nan"), 1.0)
        rets = joined.tail(self.window + 1).diff().dropna()
        if len(rets) < 3 or rets["a"].std() == 0 or rets["b"].std() == 0:
            return CorrelationVeto(False, float("nan"), 1.0)
        rho = float(rets["a"].corr(rets["b"]))
        if not np.isfinite(rho):
            return CorrelationVeto(False, float("nan"), 1.0)
        breached = (abs(rho) if self.use_abs else rho) > self.threshold
        return CorrelationVeto(breached, rho, self.size_multiplier if breached else 1.0)


def _slope_bias(close: pd.Series | None, ema_period: int) -> int:
    if close is None or len(close) < ema_period + 2:
        return 0
    ema = close.ewm(span=ema_period, adjust=False).mean()
    last, prev, price = float(ema.iloc[-1]), float(ema.iloc[-2]), float(close.iloc[-1])
    if last > prev and price > last:
        return 1
    if last < prev and price < last:
        return -1
    return 0


def mtf_alignment(primary_close: pd.Series,
                  anchor1_close: pd.Series | None,
                  anchor2_close: pd.Series | None,
                  *, ema_period: int = 50,
                  conflict_size_multiplier: float = 0.5) -> MTFAlignment:
    """Spec §6.4: the slower anchor bans counter-trend entries; a disagreement
    between the two anchors halves size rather than blocking outright."""
    a1 = _slope_bias(anchor1_close, ema_period)
    a2 = _slope_bias(anchor2_close, ema_period)
    if a1 == 0 and a2 == 0:
        return MTFAlignment(0, 1.0, True, True)
    if a1 != 0 and a2 != 0 and a1 != a2:
        return MTFAlignment(0, conflict_size_multiplier, True, True)
    bias = a2 if a2 != 0 else a1
    return MTFAlignment(bias, 1.0, allow_long=(bias >= 0), allow_short=(bias <= 0))
