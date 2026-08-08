"""Dominant-cycle estimation: Blackman-Harris FFT and Burg MESA.

HARD RULE (spec §3.2): this module outputs a cycle LENGTH and a CONFIDENCE score.
It never outputs a future price path. There is deliberately no inverse transform
here -- IFFT projection assumes stationarity and perfect periodicity, which gold
and bitcoin do not have, and a prior research pass on this repo already killed it
(reports/fourier_research.md).

MESA (Burg's maximum-entropy AR spectrum) is preferred on short windows because
FFT resolution is 1/N: on Gold's configured 96-bar window the FFT bins near a
30-bar cycle are ~10 bars apart, which is too coarse to tune a bandpass with.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CycleEstimate:
    """period is None when no cycle in [min_period, max_period] could be resolved."""

    period: float | None
    prominence: float       # peak amplitude / mean amplitude, spec §4.3 gate is 2.0
    power_fraction: float   # peak power / total in-band power
    method: str             # "fft" | "mesa" | "none"


_EMPTY = CycleEstimate(period=None, prominence=0.0, power_fraction=0.0, method="none")


def blackman_harris(n: int) -> np.ndarray:
    """4-term Blackman-Harris window (-92 dB sidelobes) -- minimises the spectral
    leakage that would otherwise smear a trend into a fake low-frequency 'cycle'."""
    a = (0.35875, 0.48829, 0.14128, 0.01168)
    k = np.arange(n)
    return (a[0]
            - a[1] * np.cos(2 * np.pi * k / (n - 1))
            + a[2] * np.cos(4 * np.pi * k / (n - 1))
            - a[3] * np.cos(6 * np.pi * k / (n - 1)))


def _detrend(x: np.ndarray) -> np.ndarray:
    """Remove mean and linear slope. A drifting price is not a cycle; leaving the
    trend in produces a spurious peak at the window length every single time."""
    n = x.size
    t = np.arange(n, dtype=float)
    slope, intercept = np.polyfit(t, x, 1)
    return x - (slope * t + intercept)


def fft_spectrum(x: np.ndarray, segments: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """Returns (periods, amplitudes) for positive non-DC frequencies.

    segments > 1 applies Welch's method over 50%-OVERLAPPING windows (spec §3.2):
    the series is split into half-overlapping pieces whose spectra are averaged.
    That buys variance reduction at the cost of frequency resolution -- a good
    trade on a long window and a bad one on a short window, which is why the Gold
    path (96 bars) routes to MESA instead of raising `segments`.
    """
    if segments < 1:
        raise ValueError(f"segments must be >= 1, got {segments}")
    x = _detrend(np.asarray(x, dtype=float))
    n = x.size
    seg_len = n if segments == 1 else int(n / (1.0 + (segments - 1) * 0.5))
    if seg_len < 8:
        segments, seg_len = 1, n
    step = max(seg_len // 2, 1)
    w = blackman_harris(seg_len)

    acc = None
    used = 0
    for start in range(0, n - seg_len + 1, step):
        spec = np.abs(np.fft.rfft(x[start:start + seg_len] * w))
        acc = spec if acc is None else acc + spec
        used += 1
        if used >= segments:
            break

    freqs = np.fft.rfftfreq(seg_len, d=1.0)
    keep = freqs > 0
    return 1.0 / freqs[keep], (acc / used)[keep]


def burg_ar(x: np.ndarray, order: int) -> tuple[np.ndarray, float]:
    """Burg's method. Returns (a, variance) with a[0] = 1 and
    x[t] = -sum_{i>=1} a[i] x[t-i] + e[t]."""
    x = np.asarray(x, dtype=float)
    n = x.size
    if order >= n:
        raise ValueError(f"order {order} must be < len(x) {n}")
    f = x.copy()
    b = x.copy()
    a = np.array([1.0])
    energy = float(x @ x)
    var = energy / n if n else 0.0
    den = 2.0 * energy - f[0] ** 2 - b[-1] ** 2
    for m in range(order):
        if den <= 1e-300:
            break
        k = -2.0 * float(f[m + 1:] @ b[m:n - 1]) / den
        a = np.concatenate([a, [0.0]]) + k * np.concatenate([[0.0], a[::-1]])
        f_new = f[m + 1:] + k * b[m:n - 1]
        b_new = b[m:n - 1] + k * f[m + 1:]
        f[m + 1:], b[m + 1:] = f_new, b_new
        var *= 1.0 - k * k
        den = (1.0 - k * k) * den - f[m + 1] ** 2 - b[n - 1] ** 2
    return a, max(var, 1e-300)


def mesa_spectrum(x: np.ndarray, order: int | None = None,
                  n_freq: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Maximum-entropy (AR) power spectrum. Returns (periods, power)."""
    x = _detrend(np.asarray(x, dtype=float))
    n = x.size
    if order is None:
        order = max(4, min(20, int(round(n / 9.0))))
    a, var = burg_ar(x, order)
    freqs = np.linspace(1e-6, 0.5, n_freq)
    k = np.arange(a.size)
    denom = np.abs(np.exp(-2j * np.pi * np.outer(freqs, k)) @ a) ** 2
    return 1.0 / freqs, var / np.maximum(denom, 1e-300)


def dominant_cycle(x: np.ndarray, *, min_period: float, max_period: float,
                   method: str = "auto", mesa_threshold: int = 128,
                   top_k: int = 2, fft_segments: int = 2) -> CycleEstimate:
    """Strongest in-band cycle plus its confidence.

    Prominence is measured against the spectrum's own power-law background, not
    against the band mean. This is the difference between a gate that works and
    one that does not: price is approximately 1/f^2 noise, so its spectrum always
    slopes, and the tallest point of a sloping spectrum sits several times above
    the band mean no matter what. Measured against the band mean, a pure random
    walk scored prominence >= 2.0 on 40 of 40 trials. Fitting a straight line
    through log(power) vs log(period), dividing it out, and scoring the peak of
    the flattened spectrum against its own mean drops that to 22%, while a clean
    sine still scores 4 to 25.

    top_k (Gold 2, BTC 3 per the spec matrix) caps the score when several
    DISTINCT local maxima are comparable -- a spectrum with three equal peaks is
    not a cycle we can trade, however tall the peaks are. The maxima must be
    distinct: the bin adjacent to a peak is always nearly as tall as the peak,
    so ranking raw bins would cap every genuine cycle at exactly 2.0.
    """
    x = np.asarray(x, dtype=float)
    if x.size < 16 or not np.all(np.isfinite(x)):
        return _EMPTY
    chosen = "mesa" if (method == "mesa" or (method == "auto" and x.size < mesa_threshold)) else "fft"
    periods, mag = (mesa_spectrum(x) if chosen == "mesa"
                    else fft_spectrum(x, segments=fft_segments))
    band = (periods >= min_period) & (periods <= max_period)
    if not band.any():
        return _EMPTY
    p_band, m_band = periods[band], mag[band]
    if p_band.size < 5 or float(np.mean(m_band)) <= 0 or not np.all(m_band > 0):
        return _EMPTY

    # Two different questions, answered from two different spectra. WHERE the
    # cycle is, is a raw-spectrum question -- the raw peak is the physical one.
    # WHETHER it is a cycle at all is a whitened question. Selecting on the
    # whitened spectrum instead conflates them and mislocates the peak: with only
    # ~20 usable FFT bins a single tall line drags the log-log fit enough to hand
    # the argmax to a neighbouring bin (a clean 34-bar sine came back as 28.3).
    log_p, log_m = np.log(p_band), np.log(m_band)
    slope, intercept = np.polyfit(log_p, log_m, 1)
    ratio = m_band / np.exp(slope * log_p + intercept)

    peak_idx = int(np.argmax(m_band))
    base = float(np.mean(ratio))
    prominence = float(ratio[peak_idx]) / max(base, 1e-300)

    interior = np.arange(1, ratio.size - 1)
    maxima = interior[(ratio[1:-1] >= ratio[:-2]) & (ratio[1:-1] >= ratio[2:])]
    if maxima.size > 1:
        ranked = maxima[np.argsort(ratio[maxima])[::-1]]
        rivals = ranked[1:max(top_k, 1)]
        if rivals.size:
            rival_level = float(np.mean(ratio[rivals]))
            if rival_level > 0:
                prominence = min(prominence,
                                 float(ratio[peak_idx]) / rival_level * 2.0)

    total = float(np.sum(m_band))
    return CycleEstimate(
        period=float(p_band[peak_idx]),
        prominence=prominence,
        power_fraction=float(m_band[peak_idx]) / max(total, 1e-300),
        method=chosen,
    )
