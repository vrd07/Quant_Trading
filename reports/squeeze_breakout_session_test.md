# Squeeze Breakout — Session Filter × Strict Fills (promote-vs-kill)

**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_session.py`
Candidate cell **SL33/RR2**, lot0.04/cap$295/$50,000. Session = UTC signal hour; strict = 0.50/side cost (breakouts are chased). 2025 OOS.

> Decision rule: **promote only if a session variant clears ≈1.10 PF on BOTH years at STRICT cost** with N≥20; else research-only.

## Fills: realistic 0.20

| Session | 2026 PF | 2026 N | 2026 Net$ | 2025 OOS PF | 2025 N | 2025 Net$ |
|---|---:|---:|---:|---:|---:|---:|
| all hours | 1.27 | 151 | +3,120 | 1.05 | 191 | +660 |
| London 07-11 | 0.57 | 46 | -1,769 | 1.39 | 75 | +2,074 |
| NY 12-16 | 1.22 | 45 | +769 | 0.89 | 69 | -560 |
| London+NY 07-16 | 0.79 | 86 | -1,526 | 1.25 | 128 | +2,318 |

## Fills: strict 0.50

| Session | 2026 PF | 2026 N | 2026 Net$ | 2025 OOS PF | 2025 N | 2025 Net$ |
|---|---:|---:|---:|---:|---:|---:|
| all hours | 1.25 | 151 | +2,870 | 1.07 | 193 | +1,024 |
| London 07-11 | 0.55 | 46 | -1,946 | 1.31 | 76 | +1,752 |
| NY 12-16 | 1.21 | 45 | +734 | 0.88 | 69 | -624 |
| London+NY 07-16 | 0.80 | 87 | -1,472 | 1.20 | 129 | +1,948 |

## Verdict

**1. The session-filter hypothesis is REFUTED.** Each hour-window works in ONE year and fails the other — London 07-11 is great 2025 / poor 2026; NY 12-16 is the reverse. That is per-year overfitting, not a stable session edge. **All-hours is the most consistent variant**, so — unlike the prior Donchian research — a session filter does NOT rescue (or even help) this setup. Drop it.

**2. It IS cost-robust** (the one genuine positive). Strict 0.50/side barely dents all-hours (2026 1.27→1.25, 2025 1.05→1.07) — wide stops + RR2 + low frequency mean it isn't the slippage trap breakouts usually are.

**3. Verdict on the all-hours squeeze breakout (strict): 2026 PF 1.25, 2025 OOS PF 1.07.**
⚠️ **Marginal — do NOT trade standalone, but do NOT kill.** OOS 1.07 sits just under the 1.10 durability bar, yet it is the only additive idea all session that is positive on BOTH years AND cost-robust AND needs no fitted filter. A PF~1.07 stream is no money-maker alone — but if it is **uncorrelated** to the roster it can still improve the blend's Sharpe/DD (`project_allweather_portfolio_and_situation_map`). **Next step: measure its correlation to kalman/london/monday and, only if low + a longer OOS holds ≥1.05, add it as a small-weight diversifier — never as a standalone bet.** Park as the lead research candidate.

> Whatever the result, this stays separate from the OOS-dead Kalman entry. A pass would be a NEW standalone strategy, sized small and added to the uncorrelated roster (`project_allweather_portfolio_and_situation_map`), not bolted onto Kalman.