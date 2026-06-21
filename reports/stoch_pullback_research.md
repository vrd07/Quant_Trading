# Stochastic Pullback Continuation (2R-3R) — Research Prototype (XAUUSD 15m)

**Script:** `scripts/research_stoch_pullback.py` · **Source:** ACY *How to Trade Gold Using Stochastics (2R/3R)*

**Run:** session=`london_ny` · enforce_risk=`True`

Trend-continuation pullback: EMA(50) trend + Stochastic(14,3) cool-off into the 20-30 zone, enter on the consolidation breakout in the trend direction, **structural stop behind the range**, fixed 2R/3R target. Strict fills (cost 0.20/side, next-bar-open, SL-first), $5k. 2025 = OOS, 2026 = in-sample.

> **enforce_risk** models config_live_5000: risk-$15/trade structural sizing (min_lot 0.02 floor), $150 daily cap, max 10 trades/day, +$260 daily-profit stop, 2-consec-loss 30-min circuit breaker, and the **$250 (5%) trailing-drawdown kill switch that halts the run once hit**.

## 2025 (OOS)

Bars: 21547 · signals: 451

| RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 1.5 | 75 | 49.3% | 1.32 | +381 | -5.1% |
| 2.0 | 64 | 39.1% | 1.21 | +261 | -6.6% |
| 3.0 | 37 | 27.0% | 1.03 | +22 | -5.3% |

## 2026 (in-sample)

Bars: 10783 · signals: 224

| RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 1.5 | 14 | 42.9% | 0.74 | -123 | -6.0% |
| 2.0 | 10 | 20.0% | 0.44 | -260 | -6.0% |
| 3.0 | 10 | 20.0% | 0.71 | -123 | -5.3% |

## Verdict

➖ **No in-sample edge** — nothing clears 1.10 PF on 2026. The ACY discretionary 'wait for the range / breakout' steps don't survive a mechanical breakout proxy on gold (which mean-reverts intraday).

> Run both timeframes: `python scripts/research_stoch_pullback.py --tf 15` and `--tf 5`.