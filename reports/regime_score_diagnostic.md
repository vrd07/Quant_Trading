# Continuous Regime-Score — Diagnostic (2026 kalman tape)

**Generated:** 2026-06-21 · **Script:** `scripts/research_regime_score.py`
`regime_score = |slope| / (|slope| + rolling_std(slope))`, slope over 3 bars, std window 20. 2026 only. Diagnostic = does the score sort RANGE winners from losers? (slot/sizing effects N/A here.)

## RANGE trades by regime_score

| Score bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| <0.15 (strong range) | 0 | 0.0% | 0.00 | +0 |
| 0.15-0.30 | 0 | 0.0% | 0.00 | +0 |
| 0.30-0.50 | 0 | 0.0% | 0.00 | +0 |
| 0.50-0.70 | 16 | 50.0% | 0.99 | -6 |
| >0.70 (trend-like) | 110 | 45.5% | 0.81 | -1,558 |

## TREND trades by regime_score (sanity: high score should be better)

| Score bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| <0.15 (strong range) | 0 | 0.0% | 0.00 | +0 |
| 0.15-0.30 | 3 | 33.3% | 0.24 | -416 |
| 0.30-0.50 | 17 | 23.5% | 0.29 | -1,303 |
| 0.50-0.70 | 91 | 44.0% | 0.76 | -1,683 |
| >0.70 (trend-like) | 371 | 59.0% | 1.40 | +8,320 |

## The hypothesis, tested

- **Strong-range (score < 0.15): N0, PF 0.00, net +0.** Claim was PF > 1.0 here.
- Uncertain mid-band (0.30-0.70, any mode): N124, PF 0.70, net -2,992. Claim: this is noise to skip.
- Extremes only (<0.20 or >0.80): N199, PF 1.39, net +4,318.

## Verdict

**1. The specific RANGE claim is REFUTED — but informatively.** There is NO low-score range cohort: all 126 RANGE trades score >0.50, and 110 of them score >0.70 ('trend-like'). The score NEVER labels them strong range, so there is no >1.0 range cohort to keep — RANGE is just dead, not mis-sorted.

**2. Why: the formula self-normalizes (a real flaw).** `|slope|/(|slope|+std(slope))` divides by the slope's OWN rolling std. In a quiet range that std collapses, so even a tiny slope yields a HIGH score → quiet ranges get mislabeled 'trend'. To detect range you must normalize by an ABSOLUTE scale (e.g. `|slope|/ATR`), not by the slope's own variability.

**3. The broader insight IS validated — as a TREND-quality filter.** Extremes (<0.20 or >0.80) PF 1.39 vs uncertain-middle (0.30-0.70) PF 0.70: 'trade extremes, skip the middle' is real. But it pays off through the TREND side — high-score TREND (>0.70) is **PF 1.40, net +8,320** while low-conviction trend (0.30-0.70) bleeds.

## Verdict

➖ **Don't build it as a continuous range/trend classifier (the range half is empty).** But the diagnostic reframes it into something useful: a **TREND-quality gate** — require regime_score (ATR-normalized, fixed) > ~0.70 for TREND trades and drop the rest. In-sample that isolates the PF 1.40 trend cohort and discards both the losing low-conviction trends AND all of RANGE. That is the actionable version of your 'trade only at extremes' idea.

⚠️ **Caveats before any build:** (a) in-sample 2026 only (you said ignore 2025, but a regime_score>0.70 trend gate is exactly the kind of fitted threshold that died OOS for the BUY gate and RANGE layers — it MUST be walk-forwarded before live); (b) the existing rv-regime already keeps mostly-trend, so the marginal gain is from dropping low-conviction trends, which overlaps the decay-floor allocator's job; (c) needs the ATR-normalized score + full re-sim (real selection + sizing), not post-hoc buckets.