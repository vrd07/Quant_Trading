"""Regime detection layer (spec §5.2), evaluated before the cycle pipeline.

Hardcoding "gold mean-reverts, bitcoin trends" is the failure this exists to
prevent: gold trends violently through macro events and bitcoin ranges for
months. The regime decides which alpha logic is even allowed to fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from src.core.constants import MarketRegime
from src.data.indicators import Indicators


class CycleRegime(str, Enum):
    TREND = "TREND"
    MEAN_REVERT = "MEAN_REVERT"
    UNCERTAIN = "UNCERTAIN"


_MARKET_REGIME = {
    CycleRegime.TREND: MarketRegime.TREND,
    CycleRegime.MEAN_REVERT: MarketRegime.RANGE,
    CycleRegime.UNCERTAIN: MarketRegime.UNKNOWN,
}


@dataclass(frozen=True)
class RegimeState:
    regime: CycleRegime
    adx: float
    hurst: float
    vol_percentile: float   # 0..1, ATR(14) rank over the vol lookback
    vol_of_vol: float       # StdDev(ATR, 10) / ATR -- spiking = transition imminent
    size_multiplier: float

    def to_market_regime(self) -> MarketRegime:
        return _MARKET_REGIME[self.regime]


class RegimeDetector:
    def __init__(self, *, adx_period: int = 14, hurst_period: int = 100,
                 atr_period: int = 14, vol_lookback: int = 1920,
                 adx_trend: float = 25.0, adx_range: float = 20.0,
                 hurst_trend: float = 0.5, vol_range_max: float = 0.40,
                 uncertain_size_mult: float = 0.2,
                 stop_atr_base: float = 2.0, stop_atr_high_vol: float = 2.5,
                 stop_atr_low_vol: float = 1.5,
                 high_vol_pct: float = 0.70, low_vol_pct: float = 0.30) -> None:
        # vol_lookback default 1920 = 20 days of 15m bars (spec: "20-day lookback").
        self.adx_period = adx_period
        self.hurst_period = hurst_period
        self.atr_period = atr_period
        self.vol_lookback = vol_lookback
        self.adx_trend = adx_trend
        self.adx_range = adx_range
        self.hurst_trend = hurst_trend
        self.vol_range_max = vol_range_max
        self.uncertain_size_mult = uncertain_size_mult
        self.stop_atr_base = stop_atr_base
        self.stop_atr_high_vol = stop_atr_high_vol
        self.stop_atr_low_vol = stop_atr_low_vol
        self.high_vol_pct = high_vol_pct
        self.low_vol_pct = low_vol_pct

    def _classify_from(self, adx: float, hurst: float, vol_pct: float) -> CycleRegime:
        if adx > self.adx_trend and hurst > self.hurst_trend:
            return CycleRegime.TREND
        if adx < self.adx_range and hurst < self.hurst_trend and vol_pct < self.vol_range_max:
            return CycleRegime.MEAN_REVERT
        return CycleRegime.UNCERTAIN

    def _uncertain(self) -> RegimeState:
        return RegimeState(CycleRegime.UNCERTAIN, float("nan"), float("nan"),
                           float("nan"), float("nan"), self.uncertain_size_mult)

    def classify(self, bars: pd.DataFrame) -> RegimeState:
        """Classify the LAST bar of `bars`. Insufficient history => UNCERTAIN."""
        need = max(self.adx_period * 3, self.hurst_period, self.atr_period * 3)
        if len(bars) < need:
            return self._uncertain()

        adx_s = Indicators.adx(bars, period=self.adx_period)
        atr_s = Indicators.atr(bars, period=self.atr_period)
        hurst_s = Indicators.hurst_exponent(bars, period=self.hurst_period)

        adx = float(adx_s.iloc[-1])
        atr = float(atr_s.iloc[-1])
        hurst = float(hurst_s.iloc[-1])
        if not all(np.isfinite(v) for v in (adx, atr, hurst)):
            return self._uncertain()

        window = atr_s.iloc[-self.vol_lookback:].dropna()
        vol_pct = float((window <= atr).mean()) if len(window) >= 20 else 0.5
        vov_window = atr_s.iloc[-10:].dropna()
        vol_of_vol = float(vov_window.std() / atr) if len(vov_window) >= 5 and atr > 0 else 0.0

        regime = self._classify_from(adx, hurst, vol_pct)
        size_mult = self.uncertain_size_mult if regime is CycleRegime.UNCERTAIN else 1.0
        return RegimeState(regime, adx, hurst, vol_pct, vol_of_vol, size_mult)

    def stop_atr_multiplier(self, state: RegimeState) -> float:
        """Spec §7.2: 2.0x base, 2.5x above the 70th vol pctile, 1.5x below the 30th."""
        if not np.isfinite(state.vol_percentile):
            return self.stop_atr_base
        if state.vol_percentile > self.high_vol_pct:
            return self.stop_atr_high_vol
        if state.vol_percentile < self.low_vol_pct:
            return self.stop_atr_low_vol
        return self.stop_atr_base
