"""Causal a-trous wavelet transform.

A standard decimated DWT with symmetric boundary extension silently mixes future
samples into the right edge, which invalidates a backtest without changing how the
output looks. We avoid the whole class of bug by never building a symmetric
extension: the multi-level cascade is collapsed into a single one-sided FIR whose
support is strictly [t-L, t]. Causality is then a structural property of the array,
not a library flag we have to trust.

Group delay is real and must be paid for, not wished away. Daubechies filters are
not linear-phase, so the delay is frequency-dependent; `group_delay(period)`
computes it exactly from the derivative of the phase response at that frequency.

Measured group delay:

    db4 L2 (15m gold): warmup=21 bars, delay@16=17.30 @32=17.84 @64=17.95
    sym8 L3 (1h btc): warmup=105 bars, delay@16=47.88 @32=48.28 @64=49.78

The spec's "~8-12 bars" estimate assumes a decimated DWT convention, where the
delay is counted in coefficients at the coarse scale. The a-trous cascade used
here keeps every scale at the sample rate and is correspondingly heavier: the
numbers above are the authoritative ones. In wall-clock terms the gold trend line
lags price by ~4.5 hours and the BTC one by ~2 days, so the Denoised Trend Line is
a slow structural reference and cannot be read as a timing signal on its own --
that is what the Goertzel phase tracker is for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Decomposition low-pass filters. Verified by property tests in
# tests/unit/test_causal_dwt.py (sum = sqrt(2), energy = 1, even-shift orthogonal)
# so a transcription typo cannot survive into a trend line.
WAVELET_FILTERS: dict[str, np.ndarray] = {
    "db4": np.array([
        -0.010597401784997278,
        0.032883011666982945,
        0.030841381835986965,
        -0.18703481171888114,
        -0.02798376941698385,
        0.6308807679295904,
        0.7148465705525415,
        0.23037781330885523,
    ]),
    "sym8": np.array([
        -0.0033824159510061256,
        -0.0005421323317911481,
        0.03169508781149298,
        0.007607487324917605,
        -0.1432942383508097,
        -0.061273359067658524,
        0.4813596512583722,
        0.7771857517005235,
        0.3644418948353314,
        -0.05194583810770904,
        -0.027219029917056003,
        0.049137179673607506,
        0.003808752013890615,
        -0.014952258337048231,
        -0.0003029205147213668,
        0.0018899503327594609,
    ]),
}


@dataclass(frozen=True)
class DWTResult:
    """trend = level-J approximation; detail = x - trend; both NaN before warmup."""

    trend: np.ndarray
    detail: np.ndarray
    warmup: int


class CausalDWT:
    """A-trous (undecimated) wavelet smoother, collapsed to one causal FIR."""

    def __init__(self, wavelet: str = "db4", level: int = 2) -> None:
        if wavelet not in WAVELET_FILTERS:
            raise ValueError(f"unknown wavelet {wavelet!r}; have {sorted(WAVELET_FILTERS)}")
        if level < 1:
            raise ValueError(f"level must be >= 1, got {level}")
        self.wavelet = wavelet
        self.level = level
        self._taps = self._build_cascade(WAVELET_FILTERS[wavelet], level)

    @staticmethod
    def _build_cascade(h: np.ndarray, level: int) -> np.ndarray:
        """Convolve the level-1..J a-trous filters into one impulse response.

        At level j the filter is h upsampled by 2**(j-1) ("with holes"), so the
        cascade's DC gain is 2**(J/2); we normalise to 1 so a constant price maps
        to itself and `detail` is a true residual.
        """
        cascade = np.array([1.0])
        for j in range(level):
            dilation = 2 ** j
            upsampled = np.zeros((len(h) - 1) * dilation + 1)
            upsampled[::dilation] = h
            cascade = np.convolve(cascade, upsampled)
        return cascade / cascade.sum()

    @property
    def taps(self) -> np.ndarray:
        return self._taps.copy()

    @property
    def warmup(self) -> int:
        """Bars of history consumed before the first valid output."""
        return len(self._taps) - 1

    @staticmethod
    def _group_delay_of(taps: np.ndarray, period: float) -> float:
        """tau(w) = Re( sum n*g[n]e^-jwn / sum g[n]e^-jwn ), the standard exact form."""
        if period <= 0:
            raise ValueError(f"period must be > 0, got {period}")
        w = 2.0 * np.pi / float(period)
        n = np.arange(len(taps))
        phasor = np.exp(-1j * w * n)
        denom = float(np.abs(taps @ phasor))
        if denom < 1e-15:
            return float(len(taps) - 1) / 2.0  # filter has a null here; fall back to centroid
        return float(np.real((n * taps) @ phasor / (taps @ phasor)))

    def group_delay(self, period: float) -> float:
        """Delay in bars that the trend line lags price at this cycle length."""
        return self._group_delay_of(self._taps, period)

    def transform(self, x: np.ndarray) -> DWTResult:
        x = np.asarray(x, dtype=float)
        trend = np.full(x.shape, np.nan)
        if x.size > self.warmup:
            # 'valid' consumes exactly warmup samples, so out[i] uses x[i-warmup..i]
            # and nothing later. This single line is the causality guarantee.
            trend[self.warmup:] = np.convolve(x, self._taps, mode="valid")
        return DWTResult(trend=trend, detail=x - trend, warmup=self.warmup)

    def transform_series(self, close: pd.Series) -> pd.DataFrame:
        out = self.transform(close.to_numpy(dtype=float))
        return pd.DataFrame({"trend": out.trend, "detail": out.detail}, index=close.index)
