# Liquidity Race — calibration report

Generated: 2026-08-06 19:26 UTC  
Data: `data/historical/XAUUSD_5m_real.csv` resampled to 15m, 2022-01-02 → 2026-08-06  
Split: IS 2022-01-01..2025-12-31 / OOS 2026-01-01..2026-07-31  
Snapshot stride: 4 bar(s), horizon 96 bars (24h)

## Verdict: `full`

> Liquidity race: calibrated model active.

| Gate | Requirement | Result | |
|---|---|---|---|
| Leg 1 — beats distance | OOS log-loss lower | 0.64589 vs 0.98969 | PASS |
| | OOS top-1 higher | 0.7027 vs 0.6982 | PASS |
| | day-block 95% CI excludes 0 | [-0.38478, -0.30082] | PASS |
| Leg 2 — calibrated | ECE ≤ 5pp | 0.64pp | PASS |
| | no decile off by > 10pp | 1.69pp | PASS |

## Sample-size honesty

- OOS snapshots: **3,343**
- OOS effective N (distinct UTC days): **165**

24h forward windows overlap 95-deep, so the effective independent sample is the number of days, not the number of snapshots. Every confidence interval above resamples whole days. Read the day count, not the snapshot count.

- Mean OOS log-loss difference (model − baseline): **-0.34380** (negative favours the model)
- OOS top-1 restricted to snapshots where something WAS touched: model 0.7165 vs baseline 0.7207

## Fitted coefficients

L2 λ = `0.1` (day-blocked 5-fold CV on IS only)

| # | feature | β (z-scored) | IS mean | IS std |
|---|---|---|---|---|
| 0 | `log_dist_atr` | -0.9644 | +1.4601 | 0.5098 |
| 1 | `n_closer_same_side` | -4.6206 | +1.4743 | 1.3926 |
| 2 | `side_up` | +0.0292 | +0.4844 | 0.4998 |
| 3 | `type_equal` | +0.0318 | +0.0312 | 0.1738 |
| 4 | `type_session` | +0.0556 | +0.6052 | 0.4888 |
| 5 | `log_age_bars` | -0.0217 | +3.5880 | 1.5854 |
| 6 | `touch_count` | +0.0226 | +0.5684 | 0.9438 |
| 7 | `trend_align` | -0.0367 | +0.5035 | 0.5000 |
| 8 | `atr_pctile` | -2.1572 | +0.5025 | 0.2989 |
| 9 | `session_london` | +0.9912 | +0.4296 | 0.4950 |
| 10 | `session_ny` | +0.0339 | +0.3741 | 0.4839 |
| 11 | `dist_x_atrpctile` | +0.0208 | +0.7419 | 0.5373 |

Distance-only baseline β = `-2.0486` on `log_dist_atr`.

### λ selection (IS only)

| λ | CV log-loss |
|---|---|
| 0.0 | 0.62690 |
| 0.0001 | 0.62690 |
| 0.001 | 0.62690 |
| 0.01 | 0.62690 |
| 0.1 | 0.62690 |
| 1.0 | 0.62691 |
| 10.0 | 0.62856 |

## Reliability (OOS)

Platt recalibration: none

| decile | n | mean predicted | observed | gap |
|---|---|---|---|---|
| 0 | 2,291 | 0.0000 | 0.0000 | -0.0000 |
| 1 | 2,291 | 0.0000 | 0.0000 | -0.0000 |
| 2 | 2,291 | 0.0000 | 0.0000 | -0.0000 |
| 3 | 2,291 | 0.0001 | 0.0000 | -0.0001 |
| 4 | 2,291 | 0.0010 | 0.0000 | -0.0010 |
| 5 | 2,291 | 0.0047 | 0.0000 | -0.0047 |
| 6 | 2,290 | 0.0116 | 0.0000 | -0.0116 |
| 7 | 2,290 | 0.1644 | 0.1786 | +0.0142 |
| 8 | 2,290 | 0.4705 | 0.4860 | +0.0155 |
| 9 | 2,290 | 0.7846 | 0.7677 | -0.0169 |

## Level mix

| kind | share of rows | share of first-touches |
|---|---|---|
| `swing_low` | 19.4% | 7.1% |
| `swing_high` | 17.4% | 8.0% |
| `london_high` | 11.3% | 17.1% |
| `london_low` | 10.8% | 16.2% |
| `asia_low` | 8.8% | 12.9% |
| `asia_high` | 8.2% | 13.5% |
| `ny_low` | 7.7% | 10.9% |
| `ny_high` | 7.6% | 11.6% |
| `pd_high` | 1.9% | 0.3% |
| `pd_low` | 1.8% | 0.3% |
| `equal_highs` | 1.6% | 1.0% |
| `equal_lows` | 1.5% | 0.9% |
| `pw_high` | 1.1% | 0.1% |
| `pw_low` | 1.0% | 0.1% |

## Scope

Research and chart only. Nothing here imports into `src/strategies`, `src/risk` or `src/execution`. If the calibration is strong, wiring it into a strategy is a separate decision behind the full `backtest.md` 8-gate process.
