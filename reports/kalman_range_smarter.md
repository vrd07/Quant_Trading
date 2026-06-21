# Kalman RANGE — Structural Confirmation, Walk-Forward

**Generated:** 2026-06-21 · **Script:** `scripts/validate_kalman_range.py`
Layers L1 true-range-bound / L2 exhaustion-divergence / L3 volume-shelf / L4 time-stop(8 bars). SELL gate on, BUY gate off (live). SL33/RR1/lot0.04/cap$295, $50k. RANGE-mode PF isolated; 2025 is OOS.

> RANGE was the bleed (baseline PF 0.83). Keep a layer only if it lifts RANGE PF on BOTH years — otherwise it's fit to one regime. ⚠️ L3 uses gold tick volume (unreliable); L3 checks proximity to a volume *shelf*, not the POC centre (a fade enters at the extreme, away from the POC).

## 2025 (OOS)

| Variant | All N | All PF | All Net$ | All DD% | RANGE N | RANGE PF | RANGE Net$ |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 563 | 1.19 | +6,613 | -4.5% | 80 | 1.04 | +233 |
| baseline + L4 time-stop | 628 | 1.10 | +3,563 | -4.7% | 109 | 0.90 | -279 |
| + L1-3 structural | 533 | 1.13 | +4,394 | -3.7% | 7 | 1.33 | +130 |
| + L1-3 + L4 time-stop | 535 | 1.11 | +3,752 | -3.8% | 7 | 1.23 | +19 |

## 2026 (in-sample)

| Variant | All N | All PF | All Net$ | All DD% | RANGE N | RANGE PF | RANGE Net$ |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 610 | 1.08 | +3,243 | -6.7% | 126 | 0.83 | -1,565 |
| baseline + L4 time-stop | 669 | 1.14 | +5,301 | -5.6% | 145 | 0.95 | -286 |
| + L1-3 structural | 558 | 1.13 | +4,514 | -6.2% | 7 | 0.75 | -135 |
| + L1-3 + L4 time-stop | 558 | 1.13 | +4,373 | -6.2% | 7 | 0.35 | -275 |

## Verdict

RANGE-mode PF (full stack L1-4): 2026 0.83→0.35, 2025 OOS 1.04→1.23.

- **L1-3 over-restrict:** the ANDed filters cut RANGE to **7 trades (2026) / 7 (2025)** — from 126/80. Any PF off ~7 trades is small-sample noise, not edge. Three confirmations on a ~100-trade sub-mode leave nothing to trade.
- **L4 time-stop alone is the only layer that does real work** — and it is regime-dependent: RANGE PF 2026 0.83→0.95 (cuts the bleed, as designed) but 2025 OOS 1.04→0.90 (hurts). It helps the chop year and harms the trend year — a regime bet, not a durable fix.

➖ **Over-restrictive / regime-dependent — no layer survives the both-years bar.** L1-3 nuke RANGE to ~7 trades (noise); L4 alone is a regime bet (helps chop, hurts trend). The robust call is the simplest one the report already made: **down-weight or DROP RANGE entirely** (size→0, the situation-map's 2nd fix) rather than bolt four filters onto a ~100-trade losing sub-mode. Subtraction beats addition here — same lesson as the BUY gate and the allocator.

> Even a clean pass only repairs a minority sub-mode of an entry that fails walk-forward overall (`project_kalman_v2_retune_no_edge`). Best risk-adjusted use of RANGE may still be to size it to zero (the situation-map's 2nd fix).