# Volatility-Squeeze Breakout — Research Prototype (XAUUSD 15m)

**Generated:** 2026-06-21 · **Script:** `scripts/research_squeeze_breakout.py`
COIL = ATR(14) ≤ 20th pctile(100) **and** flat Kalman; BREAK = ATR expanding **and** close clears the coil's Donchian(20) high/low → enter with the break. Fixed-fill sim, lot0.04/cost0.20/cap$295/$50k. 2025 is OOS.

> ⚠️ Generic gold-15m breakout was already killed (`project_breakout_15m_research`). This tests whether the squeeze pre-condition rescues it. Same discipline: wire live only if it clears 1.0 PF on BOTH years.

## 2025 (OOS)

Coil bars: 4183/21547 (19%) · breakout signals: 379

| SL / RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 33 / 1.0 | 229 | 49.8% | 0.98 | -380 | -4.3% |
| 33 / 2.0 | 191 | 30.4% | 1.05 | +660 | -4.8% |
| 49 / 2.0 | 126 | 25.4% | 0.86 | -2,047 | -9.7% |

## 2026 (in-sample)

Coil bars: 2391/10583 (23%) · breakout signals: 185

| SL / RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 33 / 1.0 | 165 | 47.3% | 0.89 | -1,260 | -4.7% |
| 33 / 2.0 | 151 | 36.4% | 1.27 | +3,120 | -3.2% |
| 49 / 2.0 | 114 | 33.3% | 1.28 | +3,155 | -7.2% |

## Verdict

- **RR is decisive:** RR1.0 loses both years (breakouts need room to run); **RR2.0 is the only viable target** and is net-positive in BOTH years — no sign-flip across regimes, which already beats the BUY-gate and RANGE-layer attempts this session.
- Most-robust cell **SL33/RR2.0**: 2026 PF 1.27 (N151) → 2025 OOS PF 1.05 (N191).

⚠️ **Marginal — promising but not promotable as-is.** OOS PF 1.05 is positive and consistent but BELOW the 1.10 durability bar and inside the slippage-noise band. Breakouts are the MOST slippage-sensitive setup (you enter chasing the break), so strict fills would likely erode 1.05 toward/below 1.0. Unlike the prior breakout work it isn't dead — but it needs (a) strict-fill re-test, (b) a London/NY session filter (where the prior research found the real breakout edge), (c) a longer OOS sample — before wiring live. Best candidate of the session; not yet a yes.

> Reminder: gold intraday is mean-reverting (`project_intraday_edge_research`), so a breakout *continuation* model is swimming upstream. A pass here would still need strict-fill + session-filter checks before any live consideration.