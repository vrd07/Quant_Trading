# Weekly Report — 2026-06-01 → 2026-06-06
_Generated 2026-06-06 14:35 UTC · config `config_live_5000`_

## Status: 🔴 ACTION NEEDED
- ⚠️ Manual trades net -143.76 — discretionary clicks losing money
- ⚠️ Profit factor 0.00 < 1.0 — week is net-losing

## ML Regime Classifier
```
  BTCUSD   RANGE   conf=97%  ML  n=68   bars=26394   age=15h    [ok]
  BTCUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  ETHUSD   RANGE   conf=98%  ML  n=68   bars=26394   age=15h    [ok]
  ETHUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  EURUSD   TREND   conf=55%  rule n=0    bars=11293   age=15h    [ok]
  EURUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  XAUUSD   RANGE   conf=73%  ML  n=335  bars=94916   age=15h    [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
```
- XAUUSD per-strategy performance scores: kalman_regime=-0.0121

## Trades This Week (Mon–Sat)
- **3 trades** · Net **-172.02** · Win 0% (0W/3L) · PF 0.00 · ΣR -0.62
- Bot -28.26 vs Manual -143.76 · Best -28.26 / Worst -72.64
- **Verification:** counted 3 closed trades in `trade_journal_config_live_5000.csv` with exit_time in [2026-06-01 … 2026-06-06].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | manual | 2 | 0 | -143.76 |
  | kalman_regime | 1 | 0 | -28.26 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-05-25        0     +0.00   +0.00      75%    321
  2026-06-01        3   -172.02   -0.62      73%    335
```
- Week-over-week: Net Δ-172.02, ΣR Δ-0.62 → **declining ❌**
- ML training data Δ+14 samples (growing ✅)
