# Weekly Report — 2026-05-18 → 2026-05-23
_Generated 2026-05-25 09:17 UTC · config `config_live_1000`_

## Status: 🔴 ACTION NEEDED
- ⚠️ ML overrides STALE on 7 symbol(s) (>36h) — nightly classifier not refreshing
- ⚠️ Manual trades net -45.06 — discretionary clicks losing money
- ⚠️ Profit factor 0.37 < 1.0 — week is net-losing

## ML Regime Classifier
```
  BTCUSD   RANGE   conf=74%  ML  n=37   bars=16704   age=73h    [STALE]
  BTCUSDS  RANGE   conf=52%  rule n=0    bars=0       age=73h    [STALE]
  ETHUSD   RANGE   conf=77%  ML  n=36   bars=16704   age=73h    [STALE]
  ETHUSDS  RANGE   conf=52%  rule n=0    bars=0       age=73h    [STALE]
  EURUSD   TREND   conf=55%  rule n=0    bars=11293   age=73h    [STALE]
  EURUSDS  RANGE   conf=52%  rule n=0    bars=0       age=73h    [STALE]
  XAUUSD   RANGE   conf=83%  ML  n=316  bars=90600   age=1h     [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=73h    [STALE]
```
- XAUUSD per-strategy performance scores: descending_channel_breakout=+0.0386, kalman_regime=-0.0433

## Trades This Week (Mon–Sat)
- **2 trades** · Net **-28.50** · Win 50% (1W/1L) · PF 0.37 · ΣR +0.37
- Bot +16.56 vs Manual -45.06 · Best +16.56 / Worst -45.06
- **Verification:** counted 2 closed trades in `trade_journal_config_live_1000.csv` with exit_time in [2026-05-18 … 2026-05-23].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | manual | 1 | 0 | -45.06 |
  | kalman_regime | 1 | 1 | +16.56 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-05-18        2    -28.50   +0.37      83%    316
  2026-05-25        0     +0.00   +0.00      83%    316
```
- Week-over-week: Net Δ+28.50, ΣR Δ-0.37 → **declining ❌**
- ML training data Δ+0 samples (not growing ⚠️)
