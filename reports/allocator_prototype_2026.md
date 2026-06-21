# Expectancy-Weighted Allocator — Prototype (2026)

**Generated:** 2026-06-21 · **Script:** `scripts/allocator_prototype.py`
Daily R-multiple streams (kalman/london/monday). Lookback **45 business days**, per-strategy cap **50%**, weights ∝ max(0, trailing Sharpe), recomputed walk-forward (no lookahead). Eval window starts after warm-up (2026-03-09, 72 days).

> The point is **self-defence**: weights come from trailing performance only, so a decaying strategy starves itself. ⚠️ In-sample components, short window, thin trailing samples — mechanism + current weights, not a validated allocation.

## Performance — standalone vs blends (eval window, R-units)

| Stream | Total R | Sharpe | Max DD (R) |
|---|---:|---:|---:|
| kalman_regime | +18.4 | 1.28 | -17.2 |
| london_breakout | +26.1 | 3.14 | -4.9 |
| monday_drift | +5.4 | 3.10 | -1.7 |
| **Equal-risk blend** | +16.6 | 3.07 | -6.3 |
| **Expectancy-weighted** | +5.2 | 1.31 | -4.4 |
| Equal-risk + decay floor | +10.3 | 1.99 | -6.2 |

- **Equal-risk wins on this data (Sharpe 3.07).** Pure expectancy-weighting (1.31) *underperforms* — trailing samples are too thin and the sparse high-Sharpe strategy (monday) gets over-weighted, throwing away the diversification that is the actual edge. **Tuning the allocator to beat this in-sample would be the overfitting trap.**
- **Equal-risk + decay-floor (Sharpe 1.99)** is the practical compromise: it keeps full diversification (≈ equal-risk) but drops any strategy whose trailing edge turns negative. That is the self-defence that matters — it does NOT try to time allocation, only to stop funding a dead strategy.

## Weights

| Strategy | Avg walk-forward weight | **Current target** |
|---|---:|---:|
| kalman_regime | 0.18 | **0.09** |
| london_breakout | 0.40 | **0.52** |
| monday_drift | 0.42 | **0.39** |

The **current target** column is the actionable output — feed these as per-strategy risk fractions into live sizing (e.g. scale each strategy's `risk_per_trade_usd` by its weight). A strategy whose trailing Sharpe turns negative drops to 0 automatically.

## How to wire it live (next step, not done here)

1. Emit per-strategy realised daily R from the trade journal (live tape already has pnl + per-trade risk).
2. Nightly, compute `expectancy_weights()` on the trailing window and write a `strategy_risk_weights` override (sits alongside the existing nightly regime override consumed by `_apply_regime_override`).
3. In the risk engine, scale each strategy's risk budget by its weight; floor tiny weights to 0 (stand aside) and keep the per-strategy cap.

> This is the honest 'trade regardless of the situation' lever: not one omniscient strategy, but an allocator that quietly defunds whatever stops working — including kalman, whose OOS-death (`project_kalman_v2_retune_no_edge`) this machinery would have caught and starved on its own.