"""Real-time cycle tracking: generalized Goertzel + Ehlers roofing filter.

This is the replacement for IFFT projection (spec §4). Goertzel answers a
question you can actually act on -- "is the detected cycle still present right
now, and where in its phase are we" -- rather than the question IFFT answers,
which is "what would the next N bars look like if nothing ever changed".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CyclePhase:
    amplitude: float
    phase: float     # radians in (-pi, pi]
    position: float  # 0.0 = peak, 0.5 = trough, advancing with time


_ZERO = CyclePhase(amplitude=0.0, phase=0.0, position=0.0)

# Probe window, in multiples of the period being measured. Measured trade-off
# (a 32-bar cosine probed at period 9, and phase error against ground truth):
#
#   window   detrend   off-freq amp   max phase error
#   1 period linear    0.083          0.0716  <- detrend eats the signal itself
#   2 period mean      0.332          0.0000  <- too little frequency resolution
#   3 period mean      0.051          0.0000  <- chosen
#
# One period is too short to tell a slow neighbour's local slope apart from the
# target frequency, and removing that slope distorts the phase we came to read.
# Three periods resolves the frequency well enough that removing the mean is
# sufficient, which leaves the phase exact.
WINDOW_CYCLES = 3


def goertzel(x: np.ndarray, period: float) -> CyclePhase:
    """Amplitude and phase of `period` at the LAST sample of x.

    Generalized (non-integer bin) Goertzel over the trailing WINDOW_CYCLES
    periods of history. Needs `WINDOW_CYCLES * period` bars to return anything;
    callers must budget history accordingly.

    Caller contract: pass a window that is already finite. Any NaN anywhere in
    `x` returns a zero reading, so callers must trim filter warmup (e.g. the
    NaN head of `CausalDWT.transform`) before calling.
    """
    x = np.asarray(x, dtype=float)
    if period < 2.0:
        return _ZERO
    n = int(round(period * WINDOW_CYCLES))
    if x.size < n or n < 2:
        return _ZERO
    if not np.all(np.isfinite(x)):
        return _ZERO
    seg = x[-n:] - x[-n:].mean()
    idx = np.arange(n, dtype=float)

    w = 2.0 * math.pi / float(period)
    # Time origin at the LAST sample, so the returned phase is the phase now.
    # For seg = cos(w*t + phi) this evaluates to (n/2)*exp(j*phi), hence
    # angle(X) == phi and phase advances with time -- do not negate it.
    coeff = np.exp(-1j * w * (idx - (n - 1)))
    spectrum = complex(seg @ coeff)

    amplitude = 2.0 * abs(spectrum) / n
    phase = math.atan2(spectrum.imag, spectrum.real)
    phase = (phase + math.pi) % (2.0 * math.pi) - math.pi
    position = (phase % (2.0 * math.pi)) / (2.0 * math.pi)
    # Exactly on a peak the phase lands a hair below zero, and the modulo wraps
    # it to a full turn -- which reads as "one cycle late" instead of "on time"
    # and breaks the [0, 1) contract. A phase this close to the peak IS the peak.
    if position >= 1.0 - 1e-9:
        position = 0.0
    return CyclePhase(amplitude=amplitude, phase=phase, position=position)


class RoofingFilter:
    """Ehlers roofing filter: 2-pole high-pass, then a SuperSmoother low-pass.

    The high-pass removes the trend the DWT is already handling; the
    SuperSmoother removes the aliasing noise below ~2 bars. What survives is a
    band around the detected cycle -- a lagged but honest oscillator, as opposed
    to a zero-lag one that peeks.
    """

    def __init__(self, hp_period: float, ss_period: float) -> None:
        if hp_period <= 1.0 or ss_period <= 1.0:
            raise ValueError(f"periods must be > 1, got hp={hp_period} ss={ss_period}")
        self.hp_period = float(hp_period)
        self.ss_period = float(ss_period)

        angle = 0.707 * 2.0 * math.pi / self.hp_period
        alpha = (math.cos(angle) + math.sin(angle) - 1.0) / math.cos(angle)
        self._hp_b0 = (1.0 - alpha / 2.0) ** 2
        self._hp_a1 = 2.0 * (1.0 - alpha)
        self._hp_a2 = -((1.0 - alpha) ** 2)

        a1 = math.exp(-1.414 * math.pi / self.ss_period)
        b1 = 2.0 * a1 * math.cos(1.414 * math.pi / self.ss_period)
        self._ss_c2 = b1
        self._ss_c3 = -a1 * a1
        self._ss_c1 = 1.0 - b1 + a1 * a1

    def apply(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        n = x.size
        hp = np.zeros(n)
        out = np.zeros(n)
        for i in range(2, n):
            hp[i] = (self._hp_b0 * (x[i] - 2.0 * x[i - 1] + x[i - 2])
                     + self._hp_a1 * hp[i - 1] + self._hp_a2 * hp[i - 2])
            out[i] = (self._ss_c1 * (hp[i] + hp[i - 1]) / 2.0
                      + self._ss_c2 * out[i - 1] + self._ss_c3 * out[i - 2])
        return out
