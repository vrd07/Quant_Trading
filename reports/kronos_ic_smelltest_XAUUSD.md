# Kronos IC Smell-Test — XAUUSD

**Verdict: RED**

Reasons:
- (verdict horizon h1)
- 2026 IC 0.067 >= floor 0.03
- 2026 strict-fill PF 0.81 below floor 1.1

Model: NeoQuasar/Kronos-base | ctx 256 | stride 8 | paths 5 | commit a338061

## Stage 1 — Spearman IC (per horizon × year)

| horizon | 2025 | 2026 |
|---|---|---|
| h1 | 0.020 | 0.067 |
| h2 | -0.006 | 0.048 |
| h3 | -0.010 | 0.018 |
| h4 | -0.012 | -0.014 |

## Stage 2 — strict-fill toy sim (per horizon × year: PF)

| horizon | 2025 | 2026 |
|---|---|---|
| h1 | 0.43 | 0.81 |
| h2 | 0.54 | 0.82 |
| h3 | 0.53 | 0.79 |
| h4 | 0.51 | 0.85 |
