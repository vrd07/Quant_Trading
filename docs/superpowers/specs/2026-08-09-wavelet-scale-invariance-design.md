# Wavelet cycle — scale-invariance test

2026-08-09. Follow-up to `reports/wavelet_cycle_research.md` (NO EDGE) and
strategy #16, which ships `enabled: false` at weight 0.00.

This is a **falsification test of the premise**, not another parameter search.
It measures the detector; it simulates no trades and produces no PnL number.

## Why this, and why now

A diagnostic run of the shipped research replay on XAUUSD train + validate
(`entry_dev_atr=0.5, rr=2.0, min_prominence=4.0`) produced four findings. The
first is new; the middle two are conceded in the report; the fourth motivates
this test.

1. **Half the published grid was a duplicate.** `entry_dev_atr` is read in
   exactly one place — the `MEAN_REVERT` branch of `WaveletCycleStrategy._side`.
   That branch produced **0 signals** across train and validate. All 64 train
   and 14 validate signals came from the TREND branch, where the parameter is
   never touched, so every `entry_dev_atr=0.5` row in the report is identical to
   its `1.0` twin. The 12-cell grid was 6 distinct cells run twice.
   **The mean-reversion half of the strategy has never been tested**; what was
   tested is a trend-pullback rule using cycle phase as a filter.
2. **The simulated strategy is not the designed strategy.** Real stop distances
   span 0.90–16.77 points (**18.6×**); the shared harness collapses them to one
   median (3.99pt) for every trade. Spec §7.2's volatility-scaled stop is the
   feature under test and the test discards it.
3. **The sample cannot decide anything.** Signals fire on **0.14% of bars** — 64
   in two years. The validate gate that produced the verdict ran on 14 signals /
   10 trades, where PF cannot separate 1.2 from 0.4.
4. **The detector clusters just under its own ceiling.** Median detected period
   is **44.4 bars against a `max_period` of 48** on both splits, range
   [26.2, 46.5]. That ceiling is not a tuning choice: `max_period = window/2`
   (96/2), the longest period resolvable from the window.

Finding 4 has a testable consequence. A power-law (1/f²) spectrum has **no
characteristic scale**, so if the "dominant cycle" is an artifact of the search
band, doubling the window should double the detected period, tracking `window/2`
forever. If instead a real cycle exists, its period is a property of the market
and must stay put as the window grows.

This test is worth running before findings 1–3 are repaired, because if the
answer is "no characteristic scale" then fixing them is polishing a non-signal.

## Method

**Series** — 15m bars, **train slice only** (2022-01-01 → 2024-01-01). The frozen
OOS slice (2024-07 → 2025-01) is not read.

| Series | Construction | Role |
|---|---|---|
| `gold` | real XAUUSD closes | subject |
| `phase_surrogate` | phase-randomized gold returns | identical power spectrum and autocorrelation, phase structure destroyed — the precise control for "is this just the spectrum?" |
| `random_walk` | cumulative iid Gaussian, vol matched to gold returns | null with no structure at all |
| `sine30` | 30-bar sine + noise at realistic SNR | **positive control** |

**Sweep.** `window` ∈ {96, 192, 384}, giving `max_period` = 48 / 96 / 192.
Held fixed: `min_period=8`, `wavelet=db4`, `level=2`, `top_k=2`.

**Estimator is pinned, not left on `auto`.** `spectral.dominant_cycle` selects by
sample size — `method == "auto" and x.size < mesa_threshold` with a threshold of
128 — so W=96 runs MESA while W=192/384 run FFT. Left alone, the sweep would
switch algorithms mid-experiment and any change in detected period would be
uninterpretable. The sweep therefore runs **twice**: forced MESA
(`mesa_threshold=10**9`) and forced FFT (`mesa_threshold=0`).

Note that MESA order is `n/9`, so it grows with the window by construction. This
is inherent to the estimator and is reported rather than corrected.

**Measurement.** Evaluate every 10th bar (a distribution is wanted, not a time
series). For each (series, window, method) record the median detected period and
the ratio `median_period ÷ (window/2)`. Measure over **all** bars, not only
gate-passing ones: prominence 4.0 admits 0.14% of bars and would bias the
distribution toward whatever the gate likes.

## Decision rule — fixed before the first run

1. **Validity gate.** `sine30` must recover ≈30 bars at every window. If it does
   not, the test is broken; stop and fix it, and read no other row.
2. **No characteristic scale.** If gold's `period ÷ (window/2)` is roughly
   constant across windows **and** within noise of `phase_surrogate`, then the
   detected cycle is a property of the search band and the spectrum, not of the
   market. **The premise is dead. Stop, and do not repair findings 1–3.**
3. **Real cycle located.** If gold settles on a stable absolute period that does
   not scale with the window, and separates from `phase_surrogate`, the search
   band is mis-specified. Re-specify it to that period, and only then repair
   findings 1–3 and re-measure.

Outcome 2 is the expected one. It is a real answer and ends the line of work;
that is the point of running it.

## Scope

- Read-only against `src/cycles/`. No strategy, risk, or execution code changes.
- No trading simulation, no PnL, no parameter selection on outcomes.
- One script, `scripts/research_wavelet_scale.py`, and one report,
  `reports/wavelet_scale_invariance.md`.
- `wavelet_cycle` stays `enabled: false` at weight 0.00 regardless of outcome.
  Nothing here can enable a strategy; spec §8.3 still governs that.

## What this test cannot say

It measures whether a stable cycle *period* exists. A "yes" would not establish
that trading it is profitable — that still needs findings 1–3 repaired and a
clean OOS run. A "no" is the stronger result, because it removes the mechanism
the whole engine is built on.
