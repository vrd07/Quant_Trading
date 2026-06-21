# Portfolio Correlation & Diversification — 2026 (in-sample)

**Generated:** 2026-06-21 · **Script:** `scripts/research_portfolio_correlation.py`
**Window:** 2026-01-05 → 2026-06-16 (117 business days). All P&L normalised to **R-multiples** (pnl ÷ per-trade $ risk) so the three strategies combine on one risk basis. Blend = equal risk per strategy per day.

> The all-weather property you want is a **portfolio** property, not a single-strategy one. This measures whether the live roster actually diversifies. ⚠️ All components are in-sample 2026; **kalman is OOS-dead** (`project_kalman_v2_retune_no_edge`). This shows diversification STRUCTURE, not a deploy approval.

## Daily-return correlation (all business days, flat = 0)

| | kalman_regime | london_breakout | monday_drift |
|---|---|---|---|
| **kalman_regime** | +1.00 | -0.04 | -0.01 |
| **london_breakout** | -0.04 | +1.00 | -0.07 |
| **monday_drift** | -0.01 | -0.07 | +1.00 |

- **Average pairwise correlation: -0.04.** Near-zero/low — the three are genuinely independent return streams (different instruments, clocks and edges), which is exactly what makes a blend worth more than its parts.

### Overlap-conditional correlation (only days BOTH traded)

| pair | corr | shared days |
|---|---:|---:|
| kalman_regime × london_breakout | -0.04 | 77 |
| kalman_regime × monday_drift | +0.01 | 17 |
| london_breakout × monday_drift | -0.31 | 12 |

*Strategies rarely trade the same day (different sessions/cadence), so the all-days correlation above is dominated by non-overlap — itself a form of diversification (they're active at different times).*

## Performance — standalone vs blend (R-units)

| Stream | Total R | Sharpe (ann.) | Max DD (R) | Active days |
|---|---:|---:|---:|---:|
| kalman_regime | +38.0 | 1.62 | -17.2 | 111 |
| london_breakout | +26.9 | 2.36 | -7.6 | 81 |
| monday_drift | +12.4 | 3.38 | -1.7 | 19 |
| **PORTFOLIO (risk-parity)** | +25.8 | 3.00 | -6.3 | 115 |

- Best standalone Sharpe **3.38** → risk-parity blend Sharpe **3.00** (blend does NOT beat the best *standalone* Sharpe — equal-risk weighting lets the weakest component drag it; see the allocation note below).
- **The real diversification win is the drawdown:** deepest standalone DD **-17.2R** (kalman) → blend **-6.3R**, **63% shallower.** Because the streams bleed at different times, the blend's equity path is far smoother than any single engine — that smoothness *is* the 'trade regardless of the situation' you were after.

## Reading it / what to do

1. **Diversification is structural, not cosmetic** when avg correlation is low: the blend's drawdown path is shallower than the components because they bleed at different times. That is the real 'trade regardless of situation' — when gold chops, the FX session edge and the Monday macro bet are uncorrelated to it.
2. **But a blend cannot rescue a negative component.** Equal-risk weighting lets an OOS-dead kalman drag the Sharpe down. The allocator must weight by *forward* expectancy (or drop a component to ~0), not split evenly.
3. **Next step (allocation layer):** size each strategy by its walk-forward Sharpe, cap per-strategy risk, and let the portfolio — not one super-Kalman — carry the all-weather behaviour. This is where the effort pays off.
