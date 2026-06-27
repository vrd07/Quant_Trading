# Weekly Report — 2026-06-22 → 2026-06-27
_Generated 2026-06-27 14:34 UTC · config `config_live_25000`_

## Status: 🟢 OK
- All systems nominal.

## ML Regime Classifier
```
  AUDJPY   RANGE   conf=92%  ML  n=750  bars=183791  age=15h    [ok]
  AUDUSD   RANGE   conf=91%  ML  n=747  bars=182468  age=15h    [ok]
  BRENTCMDUSDRANGE   conf=92%  ML  n=615  bars=160095  age=15h    [ok]
  BTCUSD   RANGE   conf=92%  ML  n=757  bars=223843  age=15h    [ok]
  BTCUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  DEUIDXEURRANGE   conf=88%  ML  n=729  bars=164856  age=15h    [ok]
  ETHUSD   RANGE   conf=90%  ML  n=743  bars=219938  age=15h    [ok]
  ETHUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  EURJPY   RANGE   conf=90%  ML  n=752  bars=184882  age=15h    [ok]
  EURUSD   RANGE   conf=92%  ML  n=739  bars=182041  age=15h    [ok]
  EURUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
  GBPJPY   RANGE   conf=80%  ML  n=114  bars=32820   age=15h    [ok]
  GBPUSD   RANGE   conf=89%  ML  n=747  bars=182485  age=15h    [ok]
  LIGHTCMDUSDRANGE   conf=92%  ML  n=749  bars=175351  age=15h    [ok]
  NAS100   RANGE   conf=89%  ML  n=733  bars=165433  age=15h    [ok]
  US30     RANGE   conf=91%  ML  n=735  bars=165385  age=15h    [ok]
  USA30IDXUSDRANGE   conf=91%  ML  n=731  bars=165385  age=15h    [ok]
  USATECHIDXUSDRANGE   conf=89%  ML  n=733  bars=165433  age=15h    [ok]
  USDJPY   RANGE   conf=92%  ML  n=747  bars=182521  age=15h    [ok]
  XAGUSD   RANGE   conf=90%  ML  n=744  bars=174370  age=15h    [ok]
  XAUUSD   RANGE   conf=73%  ML  n=353  bars=98847   age=15h    [ok]
  XAUUSDS  RANGE   conf=52%  rule n=0    bars=0       age=15h    [ok]
```

## Trades This Week (Mon–Sat)
- **20 trades** · Net **+2022.79** · Win 70% (14W/4L) · PF 6.96 · ΣR +4.78
- Bot +404.40 vs Manual +1618.39 · Best +472.60 / Worst -91.50
- **Verification:** counted 20 closed trades in `trade_journal_config_live_25000.csv` with exit_time in [2026-06-22 … 2026-06-27].

  | Strategy | Trades | Wins | Net |
  |---|---:|---:|---:|
  | kalman_regime | 6 | 6 | +404.40 |
  | manual | 14 | 8 | +1618.39 |

## Are We Improving? (trend)
```
  Week         Trades       Net      ΣR  ML conf   ML n
  2026-06-22       20  +2022.79   +4.78      73%    353
```
- Need ≥2 weekly snapshots to judge a trend; baseline recorded.
