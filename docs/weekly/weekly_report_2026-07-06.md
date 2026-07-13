# Weekly Report — 2026-07-06 → 2026-07-11
_Generated 2026-07-11 14:40 UTC · config `config_live_25000`_

## Status: 🟢 OK
- All systems nominal.

## ML Regime Classifier
```
  AUDJPY   RANGE   conf=92%  ML  n=753  bars=183791  age=20h    [ok]
  AUDUSD   RANGE   conf=91%  ML  n=746  bars=182468  age=20h    [ok]
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
  GBPUSD   RANGE   conf=89%  ML  n=746  bars=182485  age=20h    [ok]
  LIGHTCMDUSDRANGE   conf=92%  ML  n=749  bars=175351  age=20h    [ok]
  NAS100   RANGE   conf=89%  ML  n=733  bars=165433  age=20h    [ok]
  US30     RANGE   conf=91%  ML  n=736  bars=165385  age=20h    [ok]
  USA30IDXUSDRANGE   conf=91%  ML  n=731  bars=165385  age=20h    [ok]
  USATECHIDXUSDRANGE   conf=89%  ML  n=733  bars=165433  age=20h    [ok]
  USDJPY   RANGE   conf=61%  ML  n=746  bars=182521  age=20h    [ok]
  XAGUSD   RANGE   conf=90%  ML  n=744  bars=174370  age=20h    [ok]
  XAUUSD   RANGE   conf=84%  ML  n=365  bars=101401  age=20h    [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=20h    [ok]
```

## Trades This Week (Mon–Sat)
- **2 trades** · Net **+86.00** · Win 50% (1W/1L) · PF 1.89 · ΣR +0.00
- Bot +0.00 vs Manual +86.00 · Best +182.60 / Worst -96.60
- **Verification:** counted 2 closed trades in `trade_journal_config_live_25000.csv` with exit_time in [2026-07-06 … 2026-07-11].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | manual | 2 | 1 | +86.00 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-06-22       20  +2022.79   +4.78      73%    353
  2026-06-29        4   -126.00   -0.00      87%    359
  2026-07-06        2    +86.00   +0.00      84%    365
```
- Week-over-week: Net Δ+212.00, ΣR Δ+0.00 → **flat ➖**
- ML training data Δ+6 samples (growing ✅)
