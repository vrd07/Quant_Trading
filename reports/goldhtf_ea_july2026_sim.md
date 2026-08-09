# GoldHTF_AutoOpt_EA v3.00 — July 2026 simulation

XAUUSD 5m, 2026-07-01 .. 2026-07-31. Lot band 0.02-1.0, risk 1.0%/trade, start balance $10,000, strict cost $0.2/side/point.

## Stage funnel — which code actually ran

| Stage | Count |
|---|---|
| `bars` | 6,109 |
| `blocked_position_open` | 1,361 |
| `blocked_session` | 2,368 |
| `regime_STRONG` | 345 |
| `regime_WEAK` | 3,215 |
| `regime_RANGING` | 1,176 |
| `regime_DRY` | 12 |
| `regime_no_data` | 0 |
| `dfvg_armed_scans` | 77 |
| `dfvg_tap_fired` | 2 |
| `legacy_trend_ok` | 2,378 |
| `legacy_trend_flat` | 0 |
| `legacy_fvg_found` | 898 |
| `legacy_fvg_mitigated` | 264 |
| `legacy_ob_found` | 1,785 |
| `legacy_ob_mitigated` | 655 |
| `legacy_no_zone` | 1,459 |
| `legacy_mtf_fail` | 476 |
| `legacy_mtf_ok` | 443 |
| `legacy_entry_no_pattern` | 135 |
| `legacy_entry_no_close_confirm` | 11 |
| `legacy_entry_trend_vs_zone_mismatch` | 283 |
| `legacy_entry_fired` | 14 |
| `sltp_wrong_side` | 0 |
| `lot_zero` | 0 |
| `ENTRY_DFVG` | 2 |
| `ENTRY_HTF` | 14 |

## Result

* trades **16**  |  win rate **50.0%**  |  PF **0.73**
* net **$-185.99** on $10,000 (**-1.86%**), ending $9,814.01
* max drawdown **-3.78%**  |  avg trade $-11.62  |  expectancy **-0.07R**
* risk per trade $55-$99 (median $83)

### By entry path

| Path | n | WR | PF | net |
|---|---|---|---|---|
| DFVG | 2 | 100% | inf | $+253.37 |
| HTF | 14 | 43% | 0.37 | $-439.37 |

### Exit reasons

* `open_at_window_end` x1 — net $+9.60
* `stop_loss` x14 — net $-305.48
* `take_profit` x1 — net $+109.88

### Ladder stages reached

* `locked` x4 — net $+152.48, avg peak 69% of target
* `raw` x10 — net $-579.86, avg peak 30% of target
* `trail` x2 — net $+241.39, avg peak 92% of target

### Trade list

| # | entry | path | side | regime | lot | entry px | SL | TP | exit | peak% | R | P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 07-02 06:20 | HTF | buy | WEAK | 0.02 | 4090.90 | 4050.27 | 4172.16 | stop_loss | 81% | +1.20 | $+97.89 |
| 2 | 07-06 10:55 | HTF | buy | STRONG | 0.03 | 4167.60 | 4134.59 | 4233.62 | stop_loss | 30% | -1.01 | $-99.64 |
| 3 | 07-09 11:55 | HTF | buy | WEAK | 0.04 | 4118.90 | 4097.75 | 4161.21 | stop_loss | 67% | +0.49 | $+41.51 |
| 4 | 07-09 14:55 | HTF | buy | WEAK | 0.03 | 4137.50 | 4106.43 | 4199.63 | stop_loss | 18% | -1.01 | $-93.80 |
| 5 | 07-10 11:55 | HTF | sell | RANGING | 0.03 | 4110.80 | 4133.47 | 4065.46 | stop_loss | 64% | +0.49 | $+33.40 |
| 6 | 07-10 16:20 | HTF | buy | RANGING | 0.02 | 4120.20 | 4070.58 | 4219.44 | stop_loss | 9% | -1.00 | $-99.64 |
| 7 | 07-14 12:35 | DFVG | sell | STRONG | 0.03 | 4095.00 | 4123.92 | 4037.15 | stop_loss | 103% | +1.65 | $+143.49 |
| 8 | 07-15 08:10 | HTF | sell | WEAK | 0.03 | 4023.60 | 4050.22 | 3970.36 | stop_loss | 0% | -1.01 | $-80.45 |
| 9 | 07-17 12:35 | HTF | sell | WEAK | 0.03 | 3981.30 | 4012.32 | 3919.26 | stop_loss | 29% | -1.01 | $-93.66 |
| 10 | 07-20 06:55 | HTF | sell | WEAK | 0.05 | 4008.00 | 4027.00 | 3970.01 | stop_loss | 4% | -1.01 | $-95.98 |
| 11 | 07-22 10:10 | HTF | buy | WEAK | 0.07 | 4122.00 | 4108.75 | 4148.51 | stop_loss | 66% | +0.48 | $+44.99 |
| 12 | 07-27 06:25 | HTF | buy | RANGING | 0.05 | 4097.90 | 4082.55 | 4128.59 | stop_loss | 34% | -1.01 | $-77.73 |
| 13 | 07-28 08:35 | HTF | sell | RANGING | 0.04 | 4042.50 | 4059.19 | 4009.12 | stop_loss | 78% | +0.49 | $+32.58 |
| 14 | 07-28 12:25 | DFVG | buy | RANGING | 0.02 | 4034.30 | 4006.83 | 4089.24 | take_profit | 130% | +2.00 | $+109.88 |
| 15 | 07-29 13:05 | HTF | sell | RANGING | 0.02 | 4074.80 | 4103.82 | 4016.76 | stop_loss | 36% | -1.01 | $-58.44 |
| 16 | 07-31 15:55 | HTF | buy | RANGING | 0.02 | 4102.00 | 4067.20 | 4171.60 | open_at_window_end | 14% | +0.14 | $+9.60 |

