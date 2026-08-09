# GoldHTF legacy entry chain — leave-one-out ablation

XAUUSD 5m 2022-03-01..2026-07-31, $1,000, min lot 0.02, strict $0.2/side, legacy path isolated (`--no-dfvg`), 500 control trials, seed 11.
A2 stop calibrated to **4.18 x H1 ATR** (A0 median structural stop 24.6 pts).

Thresholds pre-committed in the design doc: load-bearing needs dz <= -0.5 AND dpct <= -10.0pp against A0.

| cell | leg removed | trades | PF | null mean | z | pct | dz | dpct | censored | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| A0 | baseline | 475 | 1.28 | 1.159 | +0.42 | 70.6% | +0.00 | +0.0 | 0.0% | reference |
| A1 | -H4 trend | 564 | 1.27 | 1.107 | +0.61 | 78.4% | +0.18 | +7.8 | 0.0% | decoration |
| A2 | -H1 zone | 536 | 1.01 | 1.199 | -0.61 | 29.0% | -1.04 | -41.6 | 0.0% | load-bearing |
| A3 | -M15 structure | 538 | 1.27 | 1.179 | +0.33 | 67.8% | -0.10 | -2.8 | 0.0% | decoration |
| A4 | -M5 pattern | 607 | 1.34 | 1.144 | +0.73 | 80.8% | +0.31 | +10.2 | 0.0% | decoration |
| A5 | -M5 pattern+confirm | 599 | 1.37 | 1.139 | +0.86 | 83.0% | +0.44 | +12.4 | 0.0% | decoration |
| A6 | +RANGING gate | 362 | 1.34 | 1.169 | +0.54 | 77.0% | +0.12 | +6.4 | 0.0% | decoration |

All cells: null censoring 0.0%, z is well-behaved. The verdict column above is voided by Guard 1 immediately below — see that section before reading it.

## GUARD 1 TRIPPED — A0 z = +0.42 < 1.0

The full chain is not distinguishable from its own null, so differences between its legs are noise being ranked. **Leg-level verdicts above are not to be acted on.** Verdict: decoration end to end.

Per the pre-committed stopping rule, the forward-return cross-check named in the design doc was NOT run: Guard 1 tripping routes past it entirely, so there is no cross-check result to report here.

## What this means

The chain as a whole beats only 70.6% of matched random-entry draws, z = +0.42. This is a second measurement, in a different configuration, that reaches the same conclusion as an earlier one: that earlier run measured 73% of 200 draws with both entry paths live (556 trades, 498 of them legacy), while this run isolates the legacy path via `--no-dfvg` (475 trades). Same data, same harness family, different configuration — not an independent reproduction, but two runs with different trial counts and different path configurations landing on the same verdict.

Because the whole is indistinguishable from noise, the per-leg columns rank differences that have no established signal to differentiate. They are recorded for completeness only.

The pre-committed decision for this outcome was "report it and stop." No wider grid, no variant hunt.

## Recorded but not actionable

A2 (remove the H1 zone) was the only cell that formally classified load-bearing: z −0.61, percentile 29.0%, PF 1.01, dz −1.04, dpct −41.6.

**A2 is confounded** and would need this caveat even if Guard 1 had not tripped. The zone supplies the stop as well as the entry filter, so A2 substituted a 4.18x H1-ATR stop calibrated to A0's median structural width. The median matched as designed (A0 24.6 pts vs A2 24.5 pts), but the DISTRIBUTIONS differ: mean 36.1 -> 38.8, sd 34.6 -> 43.1, p90 75.2 -> 86.8, max 314.5 -> 507.0. Structural stops track structure; ATR stops track volatility. Part of A2's degradation may be the wider tail rather than lost entry information.

A4 and A5 (removing the M5 candlestick pattern, and the pattern plus its close-confirm) both IMPROVED the score: dz +0.31 / +0.44, dpct +10.2 / +12.4pp. A5 fell just short of the >= 0.5 z threshold that would have classified the pattern leg as actively harmful.

These are stated as observations, explicitly labelled not-actionable under Guard 1.

## Method notes

Null censoring was 0.0% in every cell. A code review had flagged that `random_control` assigns a sentinel PF of 10.0 to zero-loss draws, which would inflate the null's standard deviation and bias z toward zero. At ~500 trades per draw it never fired, so z is well-behaved here and the concern does not apply to these numbers.

The pre-committed thresholds were fixed in the design document before any cell was run and were not adjusted afterwards.

Guard 2 did not trip: the largest trade-count ratio against baseline was A4 at 607/475 = 1.28x, well inside the 3x bar, so no cell is flagged qualitative.

## Limitations

- In-sample over the full span, no walk-forward. Defensible for "does this leg contribute" but not for "will this make money." If a leg survives, cutting the others is a change that needs its own out-of-sample check before it ships.
- Seven cells is seven tests on one dataset. The >= 0.5 z threshold is set with that in mind; guard 1 is the main protection against reading noise as structure.
- Harness fidelity: it evaluates once per M5 close where the EA evaluates every tick, so live takes more legacy trades at worse average prices than measured.
- Runtime: the control replay loops the full bar range per trial, ~10 minutes per cell at 500 trials, ~70 minutes total, more if A5 produces thousands of trades.
- numpy's Generator stream is not guaranteed stable across numpy versions, so the exact figures reproduce on this environment but may shift version-to-version.

