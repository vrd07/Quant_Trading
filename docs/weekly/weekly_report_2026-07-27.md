# Weekly Report — 2026-07-27 → 2026-08-01
_Generated 2026-08-01 14:30 UTC · config `config_live_50000`_

## Status: 🔴 ACTION NEEDED
- ⚠️ Manual trades net -711.80 — discretionary clicks losing money
- ⚠️ Profit factor 0.00 < 1.0 — week is net-losing

## ML Regime Classifier
```
  AUDJPY   RANGE   conf=93%  ML  n=752  bars=183791  age=20h    [ok]
  AUDUSD   RANGE   conf=91%  ML  n=745  bars=182468  age=20h    [ok]
  BRENTCMDUSDRANGE   conf=92%  ML  n=615  bars=160095  age=20h    [ok]
  BTCUSD   RANGE   conf=92%  ML  n=757  bars=223843  age=20h    [ok]
  BTCUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  DEUIDXEURRANGE   conf=88%  ML  n=729  bars=164856  age=20h    [ok]
  ETHUSD   RANGE   conf=90%  ML  n=743  bars=219938  age=20h    [ok]
  ETHUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  EURJPY   RANGE   conf=90%  ML  n=752  bars=184882  age=20h    [ok]
  EURUSD   RANGE   conf=92%  ML  n=739  bars=182041  age=20h    [ok]
  EURUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
  GBPJPY   RANGE   conf=80%  ML  n=114  bars=32820   age=20h    [ok]
  GBPUSD   RANGE   conf=88%  ML  n=745  bars=182485  age=20h    [ok]
  LIGHTCMDUSDRANGE   conf=92%  ML  n=749  bars=175351  age=20h    [ok]
  NAS100   RANGE   conf=89%  ML  n=733  bars=165433  age=20h    [ok]
  US30     RANGE   conf=91%  ML  n=735  bars=165385  age=20h    [ok]
  USA30IDXUSDRANGE   conf=91%  ML  n=731  bars=165385  age=20h    [ok]
  USATECHIDXUSDRANGE   conf=89%  ML  n=733  bars=165433  age=20h    [ok]
  USDJPY   RANGE   conf=92%  ML  n=745  bars=182521  age=20h    [ok]
  XAGUSD   RANGE   conf=90%  ML  n=744  bars=174370  age=20h    [ok]
  XAUUSD   RANGE   conf=86%  ML  n=383  bars=105530  age=20h    [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
```

## Trades This Week (Mon–Sat)
- **5 trades** · Net **-711.80** · Win 0% (0W/5L) · PF 0.00 · ΣR +0.00
- Bot +0.00 vs Manual -711.80 · Best -125.40 / Worst -192.60
- **Verification:** counted 5 closed trades in `trade_journal_config_live_50000.csv` with exit_time in [2026-07-27 … 2026-08-01].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | manual | 5 | 0 | -711.80 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-07-20       16   -316.00   +0.00      88%    376
  2026-07-27        5   -711.80   +0.00      86%    383
```
- Week-over-week: Net Δ-395.80, ΣR Δ+0.00 → **flat ➖**
- ML training data Δ+7 samples (growing ✅)
