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
    if atr.size == 0:
        return out

    end = bars + horizon
    # `bars < atr.size` must be checked BEFORE indexing atr[bars] -- atr can be
    # shorter than close (e.g. warmup-trimmed), and an out-of-range bar index
    # must degrade to NaN, not raise IndexError.
    in_bounds = (bars >= 0) & (end < close.size) & (bars < atr.size)
    safe_bars = np.where(in_bounds, bars, 0)
    a = atr[safe_bars]
    ok = in_bounds & np.isfinite(a) & (a > 0)
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


def paired_day_difference_ci(a_values, a_days, b_values, b_days,
                              n_boot=2000, seed=7, alpha=0.05):
    """Percentile CI for mean(a) - mean(b), resampling whole DAYS once for BOTH samples.

    Drawing each day once and taking that day's rows from both samples preserves
    within-day pairing; bootstrapping the two samples independently would destroy it
    and understate the uncertainty on the difference.

    Days present in only one sample contribute to that sample alone on the draws where
    they appear. A draw in which either side ends up empty contributes NaN, and NaN
    draws are dropped before taking percentiles.
    """
    a_values = np.asarray(a_values, dtype=float)
    a_days = np.asarray(a_days)
    b_values = np.asarray(b_values, dtype=float)
    b_days = np.asarray(b_days)

    a_keep = np.isfinite(a_values)
    a_values, a_days = a_values[a_keep], a_days[a_keep]
    b_keep = np.isfinite(b_values)
    b_values, b_days = b_values[b_keep], b_days[b_keep]

    if a_values.size == 0 or b_values.size == 0:
        return (float("nan"), float("nan"))

    union = np.unique(np.concatenate([a_days, b_days]))
    a_by_day = {d: a_values[a_days == d] for d in np.unique(a_days)}
    b_by_day = {d: b_values[b_days == d] for d in np.unique(b_days)}

    rng = np.random.default_rng(seed)
    diffs = np.full(n_boot, np.nan, dtype=float)
    for b in range(n_boot):
        pick = rng.choice(union, size=union.size, replace=True)
        a_parts = [a_by_day[d] for d in pick if d in a_by_day]
        b_parts = [b_by_day[d] for d in pick if d in b_by_day]
        if not a_parts or not b_parts:
            continue
        diffs[b] = np.concatenate(a_parts).mean() - np.concatenate(b_parts).mean()

    valid = diffs[np.isfinite(diffs)]
    if valid.size == 0:
        return (float("nan"), float("nan"))
    return (float(np.percentile(valid, 100 * alpha / 2)),
            float(np.percentile(valid, 100 * (1 - alpha / 2))))
