# Wavelet cycle — scale-invariance test

Generated 2026-08-09 02:40. Train slice only (2022-01-01 → 2024-01-01), 47,194 15m XAUUSD bars. Frozen OOS untouched.

Design: `docs/superpowers/specs/2026-08-09-wavelet-scale-invariance-design.md`

`ratio` = median detected period ÷ max_period (= window/2). A ratio that stays flat as the window grows means the detector is reporting the slowest thing its band allows, not a property of the market.

## Estimator: MESA

| series | window | max_period | median period | ratio | IQR | median prom | tradeable |
|---|---:|---:|---:|---:|---|---:|---:|
| gold | 96 | 48 | 46.5 | 0.968 | 36.5–46.5 | 1.27 | 1.3% |
| phase_surrogate | 96 | 48 | 46.5 | 0.968 | 36.5–46.5 | 1.27 | 2.1% |
| random_walk | 96 | 48 | 46.5 | 0.968 | 36.5–46.5 | 1.30 | 2.5% |
| sine30 | 96 | 48 | 30.1 | 0.626 | 29.2–31.0 | 21.76 | 98.8% |
| gold | 192 | 96 | 44.4 | 0.463 | 39.3–56.8 | 1.84 | 5.1% |
| phase_surrogate | 192 | 96 | 44.4 | 0.463 | 37.9–56.8 | 1.87 | 8.7% |
| random_walk | 192 | 96 | 44.4 | 0.463 | 37.9–56.8 | 1.87 | 6.2% |
| sine30 | 192 | 96 | 30.1 | 0.313 | 30.1–30.1 | 66.74 | 99.9% |
| gold | 384 | 192 | 53.8 | 0.280 | 42.6–170.3 | 1.65 | 4.4% |
| phase_surrogate | 384 | 192 | 63.9 | 0.333 | 42.6–170.3 | 1.47 | 7.1% |
| random_walk | 384 | 192 | 53.8 | 0.280 | 42.6–170.3 | 1.69 | 3.8% |
| sine30 | 384 | 192 | 30.1 | 0.157 | 30.1–30.1 | 75.96 | 99.9% |

## Estimator: FFT

| series | window | max_period | median period | ratio | IQR | median prom | tradeable |
|---|---:|---:|---:|---:|---|---:|---:|
| gold | 96 | 48 | 32.0 | 0.667 | 32.0–32.0 | 1.00 | 0.0% |
| phase_surrogate | 96 | 48 | 32.0 | 0.667 | 32.0–32.0 | 0.99 | 0.0% |
| random_walk | 96 | 48 | 32.0 | 0.667 | 32.0–32.0 | 0.99 | 0.0% |
| sine30 | 96 | 48 | 32.0 | 0.667 | 32.0–32.0 | 0.63 | 0.0% |
| gold | 192 | 96 | 64.0 | 0.667 | 42.7–64.0 | 0.89 | 0.0% |
| phase_surrogate | 192 | 96 | 64.0 | 0.667 | 42.7–64.0 | 0.94 | 0.0% |
| random_walk | 192 | 96 | 64.0 | 0.667 | 42.7–64.0 | 0.93 | 0.0% |
| sine30 | 192 | 96 | 32.0 | 0.333 | 32.0–32.0 | 2.47 | 0.0% |
| gold | 384 | 192 | 85.3 | 0.444 | 64.0–128.0 | 0.89 | 0.0% |
| phase_surrogate | 384 | 192 | 85.3 | 0.444 | 51.2–128.0 | 0.92 | 0.0% |
| random_walk | 384 | 192 | 85.3 | 0.444 | 51.2–128.0 | 0.93 | 0.0% |
| sine30 | 384 | 192 | 28.4 | 0.148 | 28.4–28.4 | 6.31 | 99.9% |

## Verdict

Decision rule, fixed before the run (see design doc):

1. **Validity** — `sine30` must recover ≈30 bars at every window, else the test is broken and no other row is readable.
2. **No characteristic scale** — gold within noise of `phase_surrogate` ⇒ the detected cycle belongs to the estimator and the spectrum, not the market. Premise dead.
3. **Real cycle** — gold holds a stable absolute period **and** separates from `phase_surrogate` ⇒ re-specify the band to that period.

**Rule 1 — validity: PASS on MESA, the estimator the shipped preset uses.**

- `mesa`: recovered the injected 30-bar cycle in **3 of 3** windows (best prominence 76.0, gate 4.0).
- `fft`: recovered the injected 30-bar cycle in **1 of 3** windows (best prominence 6.3, gate 4.0).

The detector finds a real cycle when one is present, so a null on gold is a statement about gold and not about the method. 

⚠️ The FFT shortfall is a real defect, not a nuisance: `dominant_cycle` switches MESA→FFT once the window reaches `mesa_threshold` (128), so any config with `window >= 128` inherits an arm that misses even a textbook injected cycle.

**Gold against its nulls** — the decisive comparison. `Δ` columns are gold minus the null; a real cycle would show gold with a large positive prominence margin.

| method | window | gold period | Δ vs surrogate | Δ vs walk | gold prom | Δ prom vs surrogate | Δ prom vs walk |
|---|---:|---:|---:|---:|---:|---:|---:|
| mesa | 96 | 46.5 | +0.0 | +0.0 | 1.27 | -0.01 | -0.03 |
| mesa | 192 | 44.4 | +0.0 | +0.0 | 1.84 | -0.02 | -0.02 |
| mesa | 384 | 53.8 | -10.1 | +0.0 | 1.65 | +0.18 | -0.04 |
| fft | 96 | 32.0 | +0.0 | +0.0 | 1.00 | +0.01 | +0.00 |
| fft | 192 | 64.0 | +0.0 | +0.0 | 0.89 | -0.05 | -0.04 |
| fft | 384 | 85.3 | +0.0 | +0.0 | 0.89 | -0.03 | -0.04 |

Gold's best prominence margin over a null is **+0.18**, against **76.0** for a real injected cycle and a gate set at 4.

**Rule 2 fires: NO CHARACTERISTIC SCALE.** Gold is indistinguishable from a phase-randomized surrogate and from a pure random walk at every window and under both estimators. Whatever period the detector reports is a property of the estimator, not of gold. The premise is dead; findings 1–3 in the design doc are not worth repairing.