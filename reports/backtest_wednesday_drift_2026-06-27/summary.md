# Backtest Summary — 2026-06-27 15:42Z

- **Run SHA:** `19c8543`
- **Config:** `config/config_live_25000.yaml`
- **Strategies graded:** 1

## Gate breakdown (backtest.md §1)

| Strategy | Trades | WinRate | PF | Sharpe | MaxDD | DailyWR | WorstR | Gates |
|---|---:|---:|---:|---:|---:|---:|---:|:-:|
| wednesday_drift | 126 | 60.3% | 1.67 | 0.13 | 0.14% | 60% | -1.00R | 4/6 |

## Pass/fail

- **wednesday_drift** — ❌ FAIL
  - ❌ G1_daily_win_rate
  - ✅ G2_worst_day_r
  - ✅ G3_profit_factor
  - ❌ G4_sharpe
  - ✅ G5_max_dd
  - ✅ G6_min_trades