# Kronos IC Smell-Test — BTCUSD

**Verdict: RED**

Reasons:
- (verdict horizon h3)
- 2026 IC 0.028 below floor 0.03 (likely no signal / drift)

Model: NeoQuasar/Kronos-base | ctx 256 | stride 16 | paths 5 | commit a338061

## Stage 1 — Spearman IC (per horizon × year)

| horizon | 2024 | 2025 | 2026 |
|---|---|---|---|
| h1 | -0.034 | 0.025 | 0.009 |
| h2 | 0.023 | 0.047 | 0.004 |
| h3 | 0.011 | 0.070 | 0.028 |
| h4 | 0.043 | 0.040 | -0.008 |

## Stage 2 — strict-fill toy sim (per horizon × year: PF)

| horizon | 2024 | 2025 | 2026 |
|---|---|---|---|
| h1 | 0.54 | 0.70 | 0.45 |
| h2 | 0.73 | 0.87 | 0.62 |
| h3 | 0.78 | 0.88 | 0.64 |
| h4 | 0.86 | 0.85 | 0.81 |
