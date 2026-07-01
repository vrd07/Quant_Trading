# EMA(20/50) Trend + Zone-Retest — Research Prototype (XAUUSD 15m)

**Script:** `scripts/research_ema_retest.py` · **Source:** user-supplied discretionary rule

**Run:** buy_retests=`3` · sell_retests=`1` · min_trend_bars=`5` · enforce_risk=`False`

Rule: price above/below BOTH EMA20 & EMA50 sets bias; no entries on the crossover itself (stack must hold `min_trend_bars`); a **retest** = price wicks into the EMA20/EMA50 zone and closes back beyond EMA20 in the trend direction; BUY needs the 3rd confirmed retest, SELL fires on the 1st; a close through EMA50 invalidates the setup. Stop = structural (beyond EMA50 / retest wick), target = fixed R:R (no stop/target was specified in the rule, so this mirrors the other `research_*` scripts in this repo). Strict fills (cost 0.20/side, next-bar-open, SL-first), $5k. 2025 = OOS, 2026 = in-sample.

## 2025 (OOS)

Bars: 21547 · signals: 471

| RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 1.5 | 276 | 42.4% | 0.99 | -28 | -9.5% |
| 2.0 | 253 | 34.0% | 0.94 | -196 | -8.9% |
| 3.0 | 232 | 28.9% | 1.09 | +280 | -8.7% |

## 2026 (in-sample)

Bars: 10791 · signals: 317

| RR | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| 1.5 | 171 | 44.4% | 1.22 | +973 | -19.9% |
| 2.0 | 153 | 34.6% | 0.97 | -150 | -22.1% |
| 3.0 | 138 | 25.4% | 1.01 | +38 | -23.8% |

## Verdict

- Best cell **RR1.5**: 2026 PF 1.22 (N171) → 2025 OOS PF 0.99 (N276).
- ⚠️ **In-sample only** — OOS PF < 1.0. Not an edge.

> Run both timeframes: `python scripts/research_ema_retest.py --tf 15` and `--tf 5`.