# TREND-Quality Gate — Walk-Forward

**Generated:** 2026-06-21 · **Script:** `scripts/validate_trend_quality_gate.py`
Self-normalising score = |slope(3)| / (|slope| + rolling_std(20)) — the form the diagnostic showed ranks trend quality. Gate drops TREND signals below threshold; RANGE untouched. SL33/RR1/lot0.04/cap$295. 2025 is OOS. (Post-hoc tape filter + re-sim; minor cooldown caveat.)

## 2025 (OOS)

| Gate | Trades N | Trend N | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| baseline (off) | 563 | 2506 | 1.19 | +6,613 | -4.5% |
| score ≥ 0.65 | 528 | 1974 | 1.17 | +5,332 | -7.1% |
| score ≥ 0.70 | 508 | 1703 | 1.16 | +4,976 | -6.6% |
| score ≥ 0.75 | 470 | 1226 | 1.12 | +3,564 | -7.8% |
| score ≥ 0.80 | 381 | 658 | 1.16 | +3,732 | -4.8% |

## 2026 (in-sample)

| Gate | Trades N | Trend N | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| baseline (off) | 610 | 1150 | 1.08 | +3,243 | -6.7% |
| score ≥ 0.65 | 547 | 952 | 1.21 | +6,864 | -4.7% |
| score ≥ 0.70 | 524 | 824 | 1.23 | +7,196 | -4.8% |
| score ≥ 0.75 | 469 | 598 | 1.32 | +8,717 | -3.7% |
| score ≥ 0.80 | 358 | 316 | 1.20 | +4,279 | -3.7% |

## Verdict

- Baseline PF: 2026 1.08, 2025 OOS 1.19.
- Best-in-2026 threshold **score ≥ 0.75**: 2026 1.32 → 2025 OOS 1.12.
- Thresholds beating baseline on BOTH years: **NONE**.

➖ **In-sample only — do not enable.** The best 2026 threshold (1.32) does NOT carry to 2025 OOS (1.12 vs baseline 1.19). The trend-quality gate joins the BUY-gate / RANGE-layer graveyard: a fitted threshold that flatters 2026 and fails OOS. The diagnostic's PF-1.40 high-score cohort was in-sample structure, not a forward-stable rule. Keep the flag OFF.

> Net: if this fails OOS too, the conclusion is firm — kalman's regime problem is not solvable by any fixed entry threshold; only the dynamic decay-floor allocator adapts.