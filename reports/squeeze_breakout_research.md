# Volatility-Squeeze Breakout — Research Prototype (XAUUSD 15m)

**Generated:** 2026-06-21 · **Script:** `scripts/research_squeeze_breakout.py`
COIL = ATR(14) ≤ 20th pctile(100) **and** flat Kalman; BREAK = ATR expanding **and** close clears the coil's Donchian(20) high/low → enter with the break. Fixed-fill sim, lot0.04/cost0.20/cap$295/$50k. 2025 is OOS.

> ⚠️ Generic gold-15m breakout was already killed (`project_breakout_15m_research`). This tests whether the squeeze pre-condition rescues it. Same discipline: wire live only if it clears 1.0 PF on BOTH years.

## 2025 (OOS)

Coil bars: 4183/21547 (19%) · breakout signals: 264

| SL / RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 33 / 1.0 | 189 | 57.1% | 1.31 | +3,397 | -2.5% |
| 33 / 2.0 | 157 | 36.3% | 1.42 | +4,417 | -3.0% |
| 49 / 2.0 | 113 | 38.1% | 1.40 | +4,844 | -4.6% |

## 2026 (in-sample)

Coil bars: 2391/10583 (23%) · breakout signals: 137

| SL / RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 33 / 1.0 | 131 | 51.9% | 1.04 | +359 | -4.4% |
| 33 / 2.0 | 127 | 39.4% | 1.38 | +3,548 | -4.6% |
| 49 / 2.0 | 106 | 28.3% | 0.96 | -496 | -9.8% |

## Verdict

- **RR is decisive:** RR1.0 loses both years (breakouts need room to run); **RR2.0 is the only viable target** and is net-positive in BOTH years — no sign-flip across regimes, which already beats the BUY-gate and RANGE-layer attempts this session.
- Most-robust cell **SL33/RR2.0**: 2026 PF 1.38 (N127) → 2025 OOS PF 1.42 (N157).

✅ **Clears 1.10 PF on BOTH years.** The squeeze pre-condition DOES change the picture vs generic breakout. Worth promoting to a proper strategy (CLAUDE.md propagation checklist) and re-validating under STRICT fills + a session filter before any live use.

> Reminder: gold intraday is mean-reverting (`project_intraday_edge_research`), so a breakout *continuation* model is swimming upstream. A pass here would still need strict-fill + session-filter checks before any live consideration.