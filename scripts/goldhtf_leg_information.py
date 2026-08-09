#!/usr/bin/env python3
"""Signal-level information test for one leg of the GoldHTF entry chain.

Asks whether a leg carries information WITHOUT any exit machinery: at every bar
where the other legs are already true, split on the leg and compare forward
returns. Immune to the geometry and exit effects that can fool a P&L comparison,
and blind to whether the information survives costs -- which is why it is a
cross-check and never overrides the ablation result.
"""

import numpy as np


def forward_returns(close, atr, bars, dirs, horizon):
    """ATR-normalized forward return over `horizon` bars, signed by direction.

    Points are not comparable across this span -- gold ran ~1800 to ~4100, so a
    raw-point average weights 2026 several times 2022.
    """
    close = np.asarray(close, dtype=float)
    atr = np.asarray(atr, dtype=float)
    bars = np.asarray(bars, dtype=int)
    dirs = np.asarray(dirs, dtype=float)

    out = np.full(bars.shape, np.nan, dtype=float)
    end = bars + horizon
    ok = (end < close.size) & (bars >= 0)
    a = atr[bars]
    ok &= np.isfinite(a) & (a > 0)
    out[ok] = (close[end[ok]] - close[bars[ok]]) / a[ok] * dirs[ok]
    return out


def drift_adjust(fwd, dirs, baseline):
    """Subtract the direction-signed unconditional drift.

    Without this a long-biased subset looks predictive purely because gold rose;
    that is the error that made 3 of 4 cells fake in the forward-returns work.
    """
    return np.asarray(fwd, dtype=float) - np.asarray(dirs, dtype=float) * baseline


def day_blocked_ci(values, days, n_boot=2000, seed=7, alpha=0.05):
    """Percentile CI for the mean, resampling whole DAYS with replacement.

    Forward windows on consecutive M5 bars overlap almost completely, so the
    effective sample size is closer to the number of days than the number of bars.
    """
    values = np.asarray(values, dtype=float)
    days = np.asarray(days)
    keep = np.isfinite(values)
    values, days = values[keep], days[keep]
    if values.size == 0:
        return (float("nan"), float("nan"))

    uniq = np.unique(days)
    by_day = {d: values[days == d] for d in uniq}
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        pick = rng.choice(uniq, size=uniq.size, replace=True)
        means[b] = np.concatenate([by_day[d] for d in pick]).mean()
    return (float(np.percentile(means, 100 * alpha / 2)),
            float(np.percentile(means, 100 * (1 - alpha / 2))))
