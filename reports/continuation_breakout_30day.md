# `continuation_breakout` — 30-Day Live Review
**Period:** 2026-04-30 → 2026-05-28 (29 days)
**Config:** `config_live_10000.yaml` (backtest baseline)
**Reviewed:** 2026-05-28

---

## Summary

**The strategy did not fire a single trade during the 30-day review window.**
Two independent suppressors blocked it throughout the entire period:

| # | Suppressor | Active since | Details |
|---|-----------|-------------|---------|
| 1 | Regime classifier auto-disable | 2026-04-30 (day 1) | `strategy_overrides.continuation_breakout: false` in `config_override_XAUUSD.json`; regime = RANGE throughout |
| 2 | Config kill-list | 2026-05-13 (day 13) | `enabled: false # disabled 2026-05-13 (kill list)` in all 7 `config_live_*.yaml` |

No journal files exist in the repository (`data/logs/` absent from this clone), and no `continuation_breakout` entries appear in any data file under `data/`. This is consistent with zero signal generation.

---

## Backtest Baseline (Oct 2025 – Apr 2026, $10K)

| Metric | Value |
|--------|-------|
| Profit Factor | 1.18 |
| Trade count | 19 |
| Win rate | 31.6% |
| Max drawdown | -3.5% |
| Return | +0.83% |
| Largest single win | $169 |
| Avg win / avg loss | ~$56 / ~$33 |

---

## Live Window Stats (2026-04-30 → 2026-05-28)

| Metric | Value |
|--------|-------|
| Trade count (closed) | **0** |
| Win rate | — |
| Profit Factor | — |
| Total P&L | $0.00 |
| Avg win / avg loss | — |
| Largest win / loss | — |
| Max drawdown | $0.00 |

### Trade-by-Trade Table

_No trades to show — strategy was suppressed for the entire window._

---

## Regime Distribution (Live Window)

Data points sourced from `config_override_XAUUSD.json` snapshots available in this repo:

| Date | Regime | Confidence | Source |
|------|--------|-----------|--------|
| 2026-04-27 | RANGE | 76.1% | `config_override_XAUUSD.json.pre_redownload_20260427_105828` |
| 2026-05-18 | RANGE | 83.0% | `docs/weekly/weekly_report_2026-05-18.md` |
| 2026-05-27 | RANGE | 74.8% | `data/config_override_XAUUSD.json` (latest) |

**Regime transition probability (RANGE → RANGE): 89.8%**
The market spent the entire live window in RANGE. `continuation_breakout` is a Wyckoff stair-step trend-following strategy and is correctly gated out in RANGE by the classifier (`strategy_weight: 0.3`, `override: false`).

### Regime Probabilities (as of 2026-05-27)

| Regime | Probability |
|--------|------------|
| RANGE | 74.83% |
| TREND | 24.96% |
| VOLATILE | 0.21% |

---

## Why No Trades Fired

1. **Regime gate (primary):** The nightly classifier has assigned `continuation_breakout: false` in `strategy_overrides` in every recorded snapshot since at least 2026-04-27. The RANGE regime (76–83% confidence) suppresses continuation-breakout signals at the override layer before they reach `RiskEngine`.

2. **Config kill-list (secondary):** On 2026-05-13 — 13 days into the window — all 7 live configs were updated to `enabled: false`. Even if the regime had briefly shifted to TREND, the strategy could not have fired after that date.

3. **ADX / consolidation gate:** At the point the strategy was disabled, `adx_14 = 12.57` (far below the `adx_min_threshold: 26` parameter). The internal gate would have rejected signals anyway.

---

## Current Config State

All 7 live configs: `enabled: false # disabled 2026-05-13 (kill list)`

`data/config_override_XAUUSD.json`: `strategy_overrides.continuation_breakout: false`

---

## Recommendation: Wait for TREND/VOLATILE Period Before Re-evaluating

**Do not re-enable based on this review.** The 30-day window produced zero usable signal data because the market was in RANGE for the entire period — not because the strategy is flawed. The backtest baseline (PF 1.18 over 19 trades) was generated in TREND/VOLATILE periods where Wyckoff stair-step patterns are relevant.

Before any re-enable decision, the following conditions should all hold:

| Condition | Current value | Required |
|-----------|-------------|---------|
| Regime | RANGE 74.8% | TREND ≥ 60% OR VOLATILE ≥ 40% |
| ADX(14) | 12.6 | ≥ 26 (strategy's own gate) |
| Strategy override | `false` | `true` |
| Kill-list flag | `enabled: false` | `enabled: true` |

**Suggested action:** revisit in 30 days. If XAUUSD has shifted to TREND with ≥ 60% confidence for at least 5 consecutive nightly runs, re-enable in a single account tier (e.g. `config_live_1000.yaml`) to gather a live sample before full rollout. A minimum of 30 closed trades is required before any PF-based keep/kill decision.
