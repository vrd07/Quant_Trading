# Decay-Floor Allocator — wiring (2026-06-21)

The one lever this session validated as ship-worthy. Every FIXED entry filter on
kalman flattered 2026 and failed 2025 OOS (BUY gate, RANGE layers, RANGE-drop,
squeeze, trend-quality gate). The decay-floor doesn't predict the regime — it
measures each strategy's TRAILING realised edge and **stops funding whatever is
currently losing**, restoring it when the edge recovers.

## Pieces
| Part | File | Role |
|---|---|---|
| Nightly job | `scripts/strategy_allocator.py` | reads the trade journal, writes `data/strategy_risk_weights.json` |
| Consumption | `src/risk/risk_engine.py` `_check_17_decay_floor` | vetoes signals from a strategy with weight ≤ 0 |
| Tests | `tests/unit/test_decay_floor.py` | weight logic + veto (11 tests) |

## Rule (per strategy, trailing `window_days`, default 45)
- `< min_trades` closed (default 8) → **weight 1.0** (insufficient data — never starve)
- trailing daily-R Sharpe ≥ `sharpe_floor` (default 0.0) → **1.0**
- else → **0.0** (defund until it recovers)

Binary 0/1 — sidesteps the pinned-min-lot fractional-sizing problem. R = `realized_pnl / initial_risk` (both in the journal).

## Safety
- **Default OFF.** Consumption only fires when `risk.decay_floor.enabled: true`.
- **Fail-open.** Missing/stale/corrupt weights file ⇒ no veto (no strategy starved).
- Weights file is **mtime-reloaded** in the risk engine — the nightly rewrite takes effect without a bot restart.

## Enable it
1. Add to the active config:
   ```yaml
   risk:
     decay_floor:
       enabled: true
       weights_file: data/strategy_risk_weights.json
   ```
2. Run the job nightly (account-specific journal):
   ```
   python scripts/strategy_allocator.py --journal data/logs/trade_journal_<config-stem>.csv
   ```
   Schedule via `/schedule` or cron (note: launchd under ~/Documents needs Full Disk Access — see `project_launchd_tcc`).

## Caveat
In-sample, equal-risk actually beat the allocator on Sharpe (3.07 vs 1.99) because all
roster components were positive — the decay-floor's value is purely *defunding a
strategy that genuinely dies* (e.g. kalman going OOS-negative). It is risk control,
not a return booster. Tune `window_days` / `min_trades` to taste; longer = slower to
defund and slower to restore.
