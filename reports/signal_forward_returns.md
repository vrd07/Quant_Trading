# Signal forward-return analysis — XAUUSD

Range 2026-05-01..2026-07-15 · 15min · triple-barrier sl 1.0×ATR / tp 2.0×ATR / hold 16 bars / cost 0.4pt/side · IS/OOS split 70% at 2026-06-23 10:00:00+00:00

## Historical (reconstructed from tick history)

| kind                   | dir   |    n |  exp_R |    PF |  win% |    totR |     t |     IS |    OOS |    mae |    mfe |  medTk | verdict |
|------------------------|-------|------|--------|-------|-------|---------|-------|--------|--------|--------|--------|--------|---------|
| bearish_divergence     | short |  113 |   0.41 |  1.78 |   52% |    46.1 |  2.96 |   0.34 |   0.55 |  -0.71 |   1.28 | 10954.0 | CANDIDATE |
| sweep_high             | short |  133 |   0.03 |  1.05 |   39% |     4.2 |  0.26 |  -0.04 |   0.22 |  -0.78 |   1.12 | 16630.0 | one-sided |
| absorption_of_buying   | short |    1 |   1.86 |   inf |  100% |     1.9 |  0.00 |   0.00 |   1.86 |  -0.71 |   2.01 | 5424.0 | thin |
| imbalance_buy          | long  |    1 |  -1.09 |  0.00 |    0% |    -1.1 |  0.00 |  -1.09 |   0.00 |  -1.01 |   0.64 | 9349.0 | thin |
| bullish_divergence     | long  |  129 |  -0.20 |  0.73 |   32% |   -26.0 | -1.70 |  -0.17 |  -0.27 |  -0.80 |   1.02 | 7671.0 | dead |
| sweep_low              | long  |  143 |  -0.32 |  0.59 |   27% |   -45.4 | -3.01 |  -0.45 |   0.02 |  -0.86 |   0.97 | 13188.0 | one-sided |

6 directional cells tested; at p<0.05 expect ~0.3 false positives by chance — treat a lone significant cell with suspicion.

## Drift control (period -12.21%)

Unconditional entries — every bar close, no signal — through the identical barriers. A directional sample pays a naked long or short on its own, so a cell above is evidence of signal only to the extent it EXCEEDS its same-direction baseline.

| kind                   | dir   |    n |  exp_R |    PF |  win% |    totR |     t |     IS |    OOS |    mae |    mfe |  medTk | verdict |
|------------------------|-------|------|--------|-------|-------|---------|-------|--------|--------|--------|--------|--------|---------|
| baseline (every bar)   | short |  321 |   0.04 |  1.06 |   40% |    12.5 |  0.50 |   0.10 |  -0.10 |  -0.80 |   1.16 | 13612.0 | one-sided |
| baseline (every bar)   | long  |  321 |  -0.27 |  0.65 |   29% |   -86.1 | -3.72 |  -0.34 |  -0.10 |  -0.85 |   1.02 | 13479.0 | dead |

## Bottom line

Candidate cell(s) that survived both halves + significance:
- **bearish_divergence short** — exp 0.41R, PF 1.78, t 2.96, n 113; **+0.37R vs the same-direction drift baseline**. Next: full backtest.md gate before any live use.

⚠️ A cell whose excess over baseline is ~0 is measuring the sample period's drift, not the mark. This sample spans ONE regime — a mark that only works in it is regime-conditional until tested on an opposing trend.
