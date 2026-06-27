# Backtest Summary — 2026-06-27 15:40Z

- **Run SHA:** `19c8543`
- **Config:** `config/config_live_25000.yaml`
- **Strategies graded:** 1

## Gate breakdown (backtest.md §1)

| Strategy | Trades | WinRate | PF | Sharpe | MaxDD | DailyWR | WorstR | Gates |
|---|---:|---:|---:|---:|---:|---:|---:|:-:|
| squeeze_breakout | 283 | 36.7% | 1.50 | 0.20 | 5.76% | 41% | -3.00R | 3/6 |

## Pass/fail

- **squeeze_breakout** — ❌ FAIL
  - ❌ G1_daily_win_rate
  - ❌ G2_worst_day_r
  - ✅ G3_profit_factor
  - ❌ G4_sharpe
  - ✅ G5_max_dd
  - ✅ G6_min_trades