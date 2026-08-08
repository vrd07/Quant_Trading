"""Adversarial inputs for spec §8.1 verification.

Lives in src/ rather than tests/ because the research script imports it too, and
`tests/unit` is not a package -- importing across that boundary works under
pytest and breaks under `python3 scripts/...`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def surrogate_series(close: pd.Series, seed: int) -> pd.Series:
    """Shuffle log-returns and re-integrate.

    Same marginal distribution and same realised volatility as the original;
    every scrap of temporal structure destroyed. A strategy that performs on
    this is fitting the return distribution, not the market.
    """
    rng = np.random.default_rng(seed)
    rets = np.diff(np.log(close.to_numpy(dtype=float)))
    rng.shuffle(rets)
    path = float(close.iloc[0]) * np.exp(np.concatenate([[0.0], np.cumsum(rets)]))
    return pd.Series(path, index=close.index)


def pink_noise(n: int, seed: int = 0) -> np.ndarray:
    """1/f noise -- the classic 'looks cyclical, isn't' adversarial input."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, d=1.0)
    freqs[0] = freqs[1]
    return np.fft.irfft(spec / np.sqrt(freqs), n=n)


def arma_series(n: int, seed: int = 0) -> np.ndarray:
    """ARMA(2,1) with complex poles: genuine short-memory autocorrelation and no
    cycle to find. Distinguishes 'detects structure' from 'detects periodicity'."""
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.zeros(n)
    for i in range(2, n):
        x[i] = 0.5 * x[i - 1] - 0.3 * x[i - 2] + e[i] + 0.4 * e[i - 1]
    return x
