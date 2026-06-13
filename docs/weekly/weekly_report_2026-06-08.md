# Weekly Report — 2026-06-08 → 2026-06-13
_Generated 2026-06-13 14:42 UTC · config `config_live_5000`_

## Status: 🔴 ACTION NEEDED
- ⚠️ Manual trades net -104.00 — discretionary clicks losing money
- ⚠️ Profit factor 0.00 < 1.0 — week is net-losing

## ML Regime Classifier
```
  AUDUSD   RANGE   conf=91%  ML  n=741  bars=182468  age=20h    [ok]
  BTCUSD   RANGE   conf=97%  ML  n=68   bars=26394   age=20h    [ok]
  BTCUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  ETHUSD   RANGE   conf=98%  ML  n=68   bars=26394   age=20h    [ok]
  ETHUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  EURUSD   RANGE   conf=92%  ML  n=739  bars=182041  age=20h    [ok]
  EURUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  GBPJPY   RANGE   conf=80%  ML  n=114  bars=32820   age=20h    [ok]
  GBPUSD   RANGE   conf=88%  ML  n=741  bars=182485  age=20h    [ok]
  USDJPY   RANGE   conf=92%  ML  n=741  bars=182521  age=20h    [ok]
  XAUUSD   RANGE   conf=65%  ML  n=341  bars=96293   age=20h    [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
```

## Trades This Week (Mon–Sat)
- **2 trades** · Net **-104.00** · Win 0% (0W/2L) · PF 0.00 · ΣR +0.00
- Bot +0.00 vs Manual -104.00 · Best -51.68 / Worst -52.32
- **Verification:** counted 2 closed trades in `trade_journal_config_live_5000.csv` with exit_time in [2026-06-08 … 2026-06-13].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | manual | 2 | 0 | -104.00 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-05-25        0     +0.00   +0.00      75%    321
  2026-06-01        3   -172.02   -0.62      73%    335
  2026-06-08        2   -104.00   +0.00      65%    341
```
- Week-over-week: Net Δ+68.02, ΣR Δ+0.62 → **improving ✅**
- ML training data Δ+6 samples (growing ✅)
