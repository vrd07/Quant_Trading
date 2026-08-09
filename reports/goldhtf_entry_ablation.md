# GoldHTF legacy entry chain — leave-one-out ablation

XAUUSD 5m 2026-01-01..2026-03-31, $1,000, min lot 0.02, strict $0.2/side, legacy path isolated (`--no-dfvg`), 10 control trials, seed 11.
A2 stop calibrated to **4.91 x H1 ATR** (A0 median structural stop 100.2 pts).

Thresholds pre-committed in the design doc: load-bearing needs dz <= -0.5 AND dpct <= -10.0pp against A0.

| cell | leg removed | trades | PF | null mean | z | pct | dz | dpct | censored | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| A0 | baseline | 27 | 1.83 | 1.197 | +0.73 | 80.0% | +0.00 | +0.0 | 0.0% | reference |
| A1 | -H4 trend | 27 | 4.64 | 5.992 | -0.10 | 90.0% | -0.83 | +10.0 | 10.0% | decoration |
| A2 | -H1 zone | 15 | 0.78 | 2.249 | -0.57 | 40.0% | -1.30 | -40.0 | 0.0% | load-bearing |
| A3 | -M15 structure | 28 | 2.06 | 2.234 | -0.13 | 50.0% | -0.86 | -30.0 | 0.0% | load-bearing |
| A4 | -M5 pattern | 30 | 1.92 | 1.892 | +0.02 | 60.0% | -0.71 | -20.0 | 0.0% | load-bearing |
| A5 | -M5 pattern+confirm | 23 | 3.54 | 1.919 | +1.00 | 70.0% | +0.27 | -10.0 | 0.0% | decoration |
| A6 | +RANGING gate | 25 | 2.05 | 1.685 | +0.22 | 90.0% | -0.51 | +10.0 | 0.0% | decoration |

**WARNING: null PF censoring is material (max 10.0%); read the percentile column, not z.**

## GUARD 1 TRIPPED — A0 z = +0.73 < 1.0

The full chain is not distinguishable from its own null, so differences between its legs are noise being ranked. **Leg-level verdicts above are not to be acted on.** Verdict: decoration end to end.

