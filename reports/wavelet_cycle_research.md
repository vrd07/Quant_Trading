# Wavelet-Fourier Hybrid Cycle -- Walk-Forward Research

Generated 2026-08-09 00:08. Strict fills, cost 0.2/side, lot 0.04, cap $295, capital $50,000.

Splits: train 2022-01→2024-01 | validate 2024-01→2024-07 | test 2024-07→2025-01 (frozen).

## XAUUSD

| params | split | trades | PF | net $ | Sharpe | maxDD | win% |
|---|---|---:|---:|---:|---:|---:|---:|
| entry_dev_atr=0.5,rr=1.5,min_prominence=4.0 | train | 46 | 1.07 | 30 | 0.51 | 0.4% | 43% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=4.0 | validate | 10 | 0.36 | -84 | -9.31 | 0.2% | 20% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=5.0 | train | 17 | 0.94 | -11 | -0.43 | 0.3% | 41% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=5.0 | validate | 4 | 0.48 | -26 | 0.00 | 0.1% | 25% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=4.0 | train | 46 | 0.99 | -5 | -0.07 | 0.4% | 35% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=4.0 | validate | 10 | 0.21 | -116 | -16.64 | 0.3% | 10% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=5.0 | train | 17 | 1.25 | 47 | 1.49 | 0.2% | 41% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=5.0 | validate | 4 | 0.00 | -65 | 0.00 | 0.1% | 0% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=4.0 | train | 46 | 1.10 | 56 | 0.70 | 0.5% | 28% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=4.0 | validate | 10 | 0.32 | -100 | -10.78 | 0.3% | 10% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=5.0 | train | 17 | 1.13 | 29 | 0.73 | 0.2% | 29% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=5.0 | validate | 4 | 0.00 | -65 | 0.00 | 0.1% | 0% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=4.0 | train | 46 | 1.07 | 30 | 0.51 | 0.4% | 43% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=4.0 | validate | 10 | 0.36 | -84 | -9.31 | 0.2% | 20% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=5.0 | train | 17 | 0.94 | -11 | -0.43 | 0.3% | 41% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=5.0 | validate | 4 | 0.48 | -26 | 0.00 | 0.1% | 25% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=4.0 | train | 46 | 0.99 | -5 | -0.07 | 0.4% | 35% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=4.0 | validate | 10 | 0.21 | -116 | -16.64 | 0.3% | 10% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=5.0 | train | 17 | 1.25 | 47 | 1.49 | 0.2% | 41% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=5.0 | validate | 4 | 0.00 | -65 | 0.00 | 0.1% | 0% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=4.0 | train | 46 | 1.10 | 56 | 0.70 | 0.5% | 28% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=4.0 | validate | 10 | 0.32 | -100 | -10.78 | 0.3% | 10% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=5.0 | train | 17 | 1.13 | 29 | 0.73 | 0.2% | 29% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=5.0 | validate | 4 | 0.00 | -65 | 0.00 | 0.1% | 0% |

### Frozen OOS test (parameters untouched)

No cell was positive on both train and validate. **Nothing is carried to the test slice.**

## BTCUSD

| params | split | trades | PF | net $ | Sharpe | maxDD | win% |
|---|---|---:|---:|---:|---:|---:|---:|
| entry_dev_atr=0.5,rr=1.5,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=1.5,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=2.0,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=0.5,rr=3.0,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=1.5,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=2.0,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=4.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=4.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=5.0 | train | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |
| entry_dev_atr=1.0,rr=3.0,min_prominence=5.0 | validate | 0 | 0.00 | 0 | 0.00 | 0.0% | 0% |

### Frozen OOS test (parameters untouched)

No cell was positive on both train and validate. **Nothing is carried to the test slice.**


## Verdict

**NO EDGE. Nothing reached the frozen test slice on either asset, so no
performance number here is quotable and the strategy does not ship enabled.**

### Did anything pass?

No. The carry rule is "positive PF on BOTH train and validate", and no cell met
it on either asset, so the `test` slice (2024-07 → 2025-01) was never touched.
It remains genuinely unused and can still serve as an honest out-of-sample slice
for a future attempt. The surrogate control never had to be adjudicated, because
nothing survived long enough to be compared against it.

- **XAUUSD** — train PF spans 0.94–1.25 on 17–46 trades; every validate cell
  lands between 0.00 and 0.48 on 4–10 trades. Train never clears 1.25 and
  validate is uniformly negative. That is not a marginal result being unlucky
  out of sample; it is a signal with no expectancy on either slice.
- **BTCUSD** — 0 trades in all 12 cells.

### Is the BTC zero a plumbing null?

No, and this was checked rather than assumed. Over 1,600 validate bars the
regime detector classifies normally (TREND 795 / UNCERTAIN 751 / MEAN_REVERT 54),
so the pipeline runs end to end; the cycle gate simply never opens —
`low_prominence` on 1,600 of 1,600 bars, not one `ok`. BTC 1h has no cycle that
clears prominence 4.0 under the spec's sym8 / level-3 / 168-bar preset. The plan
anticipated exactly this reading: a `low_prominence`-dominated distribution is a
finding, not a bug, and the gates were not weakened to manufacture trades.

For gold the same diagnostic gives `low_prominence` 2,534 vs `ok` 66 (2.5% of
bars) on the validate slice — thin, but non-zero, which is why gold produced
trades at all.

### Relationship to the prior Fourier work

This **agrees with `reports/fourier_research.md`** (2026-07-15) rather than
overturning it. That pass rejected rolling-FFT extrapolation, dominant-cycle
phase entries, and spectral entropy as a gate on this instrument, with the root
cause given as: the price spectrum is ~1/f² random walk, so window-fit cycle
phases do not persist out of window.

The one door it left open was "spectral features as ONE input among several in a
regime classifier — never a standalone signal or solo gate." This spec is that
configuration, built properly: causal DWT, delay-compensated structure, phase
tracking, and a regime gate, with three independent confirmations required. It
still does not produce an edge. The honest conclusion is that the earlier
negative result was not an artifact of the earlier implementation being naive.

Two measurements from this build sharpen why:

1. On real 15m gold the engine considers a cycle tradeable on **2.8%** of bars
   (`min_prominence` 4.0). The instrument does not spend much time in a state
   this method can act on.
2. The median detected period is **44 bars against a 48-bar cap** — the detector
   is usually locking onto the low-frequency end of its own search band, which
   is what a power-law spectrum looks like when you ask it for a cycle.

### Caveats on the numbers above

- The shared harness applies one `sl_pts`/`rr` to every trade, so §7.2's
  per-trade ATR-scaled stop is collapsed to its median. Fine for ranking cells
  against each other; not a live expectancy estimate.
- Trade counts (4–46) are far below any threshold at which PF is a stable
  statistic. Treat the gold column as "no evidence of edge", not as a precisely
  measured negative.
- `min_prominence` was researched at 4.0/5.0, not the plan's 2.0/3.0, because
  2.0 and 3.0 fail the §8.1 hallucination gate (at 2.0, pink noise reads
  tradeable on 61% of bars against a 30% ceiling). Cells below 4.0 would score
  better simply by taking more hallucinated cycles.

### What would change the verdict

Nothing in this plan. A future attempt would need a different premise, not a
retuned grid — re-running this search with a wider grid until a cell passes is
the exact failure mode `project_rsi_reversal_m1` records. The `test` slice is
still clean; spend it only on a genuinely new hypothesis.
