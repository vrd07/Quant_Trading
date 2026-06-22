# Handcrafted Portfolio Weights — 2026 (in-sample)

**Generated:** 2026-06-23 · **Script:** `scripts/handcraft_weights.py` · **Method:** Rob Carver handcrafting (*Advanced Futures Trading Strategies*)
**Window:** 2026-01-05 → 2026-06-16 (117 business days). All P&L in **R-multiples**; tapes shared with `research_portfolio_correlation.py`. **Sharpe-tilt λ = 0.0** (0 = pure handcraft).

> Handcrafting turns the correlation matrix into robust RISK weights without an optimiser: correlated strategies are grouped onto the same branch and each branch gets equal weight, so no cluster of similar edges can dominate and a genuine diversifier is rewarded. ⚠️ In-sample 2026 tapes, **kalman is OOS-dead** — this is an allocation *recommendation*, not a deploy approval.

## Correlation matrix

| | kalman_regime | london_breakout | monday_drift | squeeze_breakout | stoch_pullback |
|---|---|---|---|---|---|
| **kalman_regime** | +1.00 | -0.04 | -0.01 | +0.07 | -0.05 |
| **london_breakout** | -0.04 | +1.00 | -0.07 | +0.05 | +0.14 |
| **monday_drift** | -0.01 | -0.07 | +1.00 | -0.03 | -0.05 |
| **squeeze_breakout** | +0.07 | +0.05 | -0.03 | +1.00 | +0.34 |
| **stoch_pullback** | -0.05 | +0.14 | -0.05 | +0.34 | +1.00 |

## Weights

| Strategy | Sharpe (ann.) | Equal | **Handcraft** |
|---|---:|---:|---:|
| kalman_regime | 1.62 | 20.0% | **25.0%** |
| london_breakout | 2.36 | 20.0% | **12.5%** |
| monday_drift | 3.38 | 20.0% | **50.0%** |
| squeeze_breakout | 3.34 | 20.0% | **6.2%** |
| stoch_pullback | -0.42 | 20.0% | **6.2%** |

- **IDM (handcraft): 1.76** vs equal-weight 2.09 (capped at 2.5). The combined book diversifies enough to be scaled ~1.76× before it carries the same risk as a single undiversified component.
- ⚠️ **Handcraft IDM (1.76) is BELOW equal-weight (2.09):** the off-diagonals are mostly correlation *noise*, so the tree's hierarchy concentrates risk without a real diversification payoff. When this holds, **equal weight is the more robust default** — read the handcraft weights only as *which strategies cluster together* (the one real signal), not as a precise split.
- **Base (λ=0):** weights depend only on the correlation structure, not on the noisy in-sample Sharpe. Re-run with `--sharpe-tilt 0.5` to see the (heavily shrunk) expectancy tilt.

## How to use it

1. **Base weights are the robust default** — they need no return forecast, so they don't decay when an edge does. Re-run as the roster changes.
2. **Add a strategy** by adding its tape to `load_strategies()` in `research_portfolio_correlation.py`; this allocator picks it up with no other change.
3. **The Sharpe tilt is a dial, not a default.** Use a small λ (≤0.5) if you trust the forward expectancy; λ=0 when you don't (current stance, given kalman).
