# Kalman v2 — wiring in `LocalTrendKalman` (two-state + elapsed-time handling)

**Generated:** 2026-09-22 · **Code:** `src/indicators/kalman.py`, `src/data/indicators.py`, `src/strategies/kalman_regime_strategy.py` · **Tests:** `tests/unit/test_local_trend_kalman_dt.py`
**Data:** XAUUSD 5m → 15m, 31,884 bars, 2025-01-29 → 2026-06-09, 354 trading days · **Config:** `config_live_50000.yaml` ($50k, realistic slippage, no `--enforce-risk`)

> **Question:** the live strategy's "Kalman" is the scalar random-walk filter. `LocalTrendKalman` — two-state, ATR-scaled, with an actual velocity state — has been sitting unused in `src/indicators/kalman.py` since it was written. Does wiring it in help?

---

## 0. First, what the live filter actually is

`Indicators.kalman_filter` → `KalmanFilter(q=1e-5, r=0.01)`, the 1-state random-walk level smoother. For constant `q`, `r` its gain converges to a fixed point:

```
K* = (−q + √(q² + 4qr)) / 2r = 0.031126729201737     (analytic == iterated, to 1e-15)
equivalent EMA span = 63.25 bars  (15m → 15.8 h)
gain within 1% of K* after 85 bars
max |KF − ewm(alpha=K*, adjust=False)| after bar 1000 = 4.5e-12
```

`min_bars` is 130 and the backtest window is 1000, so **the filter is at steady state on every bar the strategy ever evaluates.** It is an EMA(63). No adaptive gain, no uncertainty, no direction — and only the *ratio* `q/r` matters, so the two configured parameters are one. The grid sweeps neither.

The trend gate's "Kalman slope" is therefore a 2-bar finite difference of a fixed-span EMA.

## 1. The change

| file | change |
|---|---|
| `src/indicators/kalman.py` | `LocalTrendKalman.filter(prices, atr, dt=None)` + `bar_widths(index, bar)`. `dt=None` is the original path, **bitwise identical** |
| `src/data/indicators.py` | `local_trend_kalman(..., bar=)` derives `dt` from the index |
| `src/strategies/kalman_regime_strategy.py` | `local_trend_kalman_enabled` (**default OFF**, `htf_buy_filter_enabled` convention); ATR hoisted above the filter; trend gate reads the **velocity state** instead of the finite difference |

### 1a. ⚠️ The obvious `dt` implementation is wrong, and a test caught it

Putting the elapsed gap into `F` as well as `Q` makes the filter predict **193 bars of accumulated drift** across a weekend. That move never happens — nothing traded — so the innovation is enormous and, with `P` inflated to match, the filter charges it to velocity. Measured on an **unbroken ramp**, the trend estimate flipped across the gap:

```
velocity, bar 299 → 300 :   +0.572  →  −0.566        # gap in F and Q  (WRONG)
velocity, bar 299 → 300 :   +0.572  →  +0.572        # gap in Q only   (shipped)
```

It would have inverted the trend gate every Monday. Time enters in two roles and only one is calendar time:

- **`F` advances one bar.** Trading time. No drift accrues while the market is shut.
- **`Q` grows with the calendar gap.** Uncertainty does accrue while the market is shut.

Pinned by `test_the_gap_does_not_invert_the_trend_across_a_weekend`. On the real frame, 365 of 31,884 bars carry `dt ≠ 1`; 354 of those are gaps over 1h; max 317 bars (79.2 h).

⚠️ **The gap handling does not speed up post-gap adaptation.** On a constructed weekend-then-reversal, velocity turns negative after 9 bars with `dt` on vs 8 with it off — the inflated `Q` raises the *level* gain too, which absorbs part of the innovation. What `dt` buys is an honest posterior and no Monday inversion, not faster turns.

## 2. Results — five arms

| arm | level | slope | N | PF | Sharpe | Net $ | MaxDD |
|---|---|---|---:|---:|---:|---:|---:|
| **A** baseline (live) | EMA63 | 2-bar diff | 3789 | **1.299** | 0.28 | +204,166 | −13.9% |
| **D** | LTK (ps 1e-3) | 2-bar diff | 1462 | 1.090 | 0.10 | +26,574 | −37.1% |
| **B** | LTK (ps 1e-3) | velocity, `dt`=1 | 1176 | 1.206 | 0.18 | +44,594 | −22.7% |
| **C** | LTK (ps 1e-3) | velocity, `dt` idx | 1130 | 1.224 | 0.18 | +45,930 | −18.7% |
| **E** | LTK (bw-matched) | velocity, `dt` idx | 2730 | **1.299** | 0.25 | +140,308 | −24.0% |

✅ **The velocity state beats the finite-difference slope** — D → C, PF 1.090 → 1.224, on the same level. That half of the thesis holds.

🔴 **But the level swap is what moved the number** (A → D), and that is a bandwidth mismatch rather than a filter verdict: `kalman_confirm_bars=4`, `min_signal_strength`, the OU z-score and the strength term were all fitted around a 63-span EMA.

## 3. ⛔ There is no `process_scale` at which this is a like-for-like swap

Chosen by matching the **level series** against EMA63 — no P&L is computed, so this cannot be a search over returns.

| `process_scale` | RMS(level − EMA63) / ATR | sd(Δlevel) / sd(ΔEMA63) |
| ---: | ---: | ---: |
| 1e-8 | 2.808 | 3.612 |
| 1e-7 | 2.043 | 2.684 |
| **1.778e-7** | **2.025** *(best)* | 2.423 |
| 1e-6 | 2.172 | **2.165** *(min)* |
| 1e-5 | 2.448 | 2.363 |
| 1e-3 *(library default)* | 2.627 | 3.345 |

At **every** setting the two-state level's first differences are **≥ 2.2× noisier** than EMA63's, and the best available match is still **2.03 ATR** away. A local-linear-trend level extrapolates; an EMA does not. Arm E uses the best match and is the fairest comparison obtainable.

## 4. The bootstrap, which settles it

354 trading days, 4,000 day-block resamples, **one shared draw across all arms** (so common shocks align).

| arm | N | PF | 95% CI | ΔPF vs A | 95% CI of Δ | P(Δ>0) |
|---|---:|---:|:---|---:|:---|---:|
| **A** baseline | 3789 | 1.299 | **[1.089, 1.536]** | — | — | — |
| D | 1462 | 1.090 | [0.893, 1.351] | −0.210 | [−0.480, +0.100] | 0.08 |
| B | 1176 | 1.206 | [0.997, 1.462] | −0.093 | [−0.307, +0.140] | 0.21 |
| C | 1130 | 1.224 | [1.005, 1.502] | −0.075 | [−0.300, +0.180] | 0.27 |
| E | 2730 | 1.299 | [1.058, 1.575] | −0.000 | [−0.159, +0.165] | 0.50 |

🔴 **Every interval overlaps every other one.** No arm is distinguishable from the baseline; arm E is a literal coin flip (P = 0.50).

🔴 **And the row that matters is the first.** The live baseline's own PF 1.299 has a 95% lower bound of **1.089**, on 3,789 trades over 354 days — *before* any deflation for the configurations already spent on this strategy (`config_live.yaml`'s own comments record `kalman_confirm_bars` 3 values, `min_signal_strength_sell`, a 2-D SL×TP sweep, and a session filter selecting 7 of 24 hour-buckets on in-sample P&L; the repo has no deflated-Sharpe, PBO or trial log). Deflated, it sits at or below 1.0.

⚠️ **MaxDD is the least stable column here** — a single-path order statistic of one realisation — so the −13.9% vs −24.0% spread should not be read as a finding either.

## Verdict

**The wiring works and is correct; it changes nothing measurable.** Ship `local_trend_kalman_enabled` **default OFF**, same disposition as `htf_buy_filter_enabled`: a config-flag A/B that can be revisited without a code change.

⛔ **Do not sweep `ltk_process_scale` to beat PF 1.30.** §4 says this backtest cannot resolve a PF difference of ±0.2 in *either* direction, so a sweep would be selecting noise on a strategy whose Sharpe is 0.25. Four configurations were evaluated here and none was chosen on P&L.

**What §4 actually recommends, and it is not a filter change.** The binding constraint is that the evaluation cannot tell 1.09 from 1.30. Before any further entry-side work: a trial count, a deflated Sharpe, and walk-forward with the session filter re-derived inside each training window rather than once on the full span. Until then every retune is unfalsifiable — which is the same conclusion `kalman_buygate_walkforward.md` and `kalman_detrend_alpha_test.md` reached from the gate side and the beta side.

**Tests:** 959 passed; 1 pre-existing failure (`test_report.py::test_equity_curves_png_written_when_data_present`, matplotlib absent from the run venv, unrelated to this change).
