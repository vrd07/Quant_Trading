# GoldHTF_AutoOpt_EA v3.00 — July 2026 simulation

XAUUSD 5m, 2026-06-01 .. 2026-07-31. Lot band 0.02-1.0, risk 1.0%/trade, start balance $10,000, strict cost $0.2/side/point.

## Stage funnel — which code actually ran

| Stage | Count |
|---|---|
| `bars` | 11,970 |
| `blocked_position_open` | 555 |
| `blocked_session` | 5,516 |
| `regime_STRONG` | 1,589 |
| `regime_WEAK` | 7,198 |
| `regime_RANGING` | 2,568 |
| `regime_DRY` | 60 |
| `regime_no_data` | 0 |
| `dfvg_armed_scans` | 139 |
| `dfvg_tap_fired` | 4 |
| `legacy_trend_ok` | 0 |
| `legacy_trend_flat` | 0 |
| `legacy_fvg_found` | 0 |
| `legacy_fvg_mitigated` | 0 |
| `legacy_ob_found` | 0 |
| `legacy_ob_mitigated` | 0 |
| `legacy_no_zone` | 0 |
| `legacy_mtf_fail` | 0 |
| `legacy_mtf_ok` | 0 |
| `legacy_entry_no_pattern` | 0 |
| `legacy_entry_no_close_confirm` | 0 |
| `legacy_entry_trend_vs_zone_mismatch` | 0 |
| `legacy_entry_fired` | 0 |
| `sltp_wrong_side` | 0 |
| `lot_zero` | 0 |
| `ENTRY_DFVG` | 4 |
| `ENTRY_HTF` | 0 |

## Result

* trades **4**  |  win rate **100.0%**  |  PF **inf**
* net **$+242.65** on $10,000 (**+2.43%**), ending $10,242.65
* max drawdown **0.00%**  |  avg trade $+60.66  |  expectancy **+0.71R**
* risk per trade $73-$97 (median $82)

### By entry path

| Path | n | WR | PF | net |
|---|---|---|---|---|
| DFVG | 4 | 100% | inf | $+242.65 |

### Exit reasons

* `stop_loss` x4 — net $+242.65

### Ladder stages reached

* `locked` x3 — net $+71.54, avg peak 71% of target
* `trail` x1 — net $+171.11, avg peak 99% of target

### Per-month breakdown

| Month | Trades | Wins | Losses | WR | PF | Net $ | Net pts | Best $ | Worst $ | Avg MFE pts | Avg MAE pts | Avg hold |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-06 | 1 | 1 | 0 | 100% | inf | $+18.42 | +4.6 | $+18.42 | $+18.42 | 13.1 | 0.3 | 1.2h |
| 2026-07 | 3 | 3 | 0 | 100% | inf | $+224.23 | +71.8 | $+171.11 | $+17.55 | 38.6 | 17.1 | 15.8h |

*pts = gold price points ($1 move). MFE = furthest the trade went in your favour before exiting; MAE = furthest against you.*

### Wins vs losses

**WINS** — n=4, avg $+60.66 (+0.71R), avg +19.1 pts, avg hold 12.1h, avg MFE 32.2 pts, avg MAE 12.9 pts

Biggest win  $+171.11 (+57.0 pts, peak 99% of target)
Biggest loss $+17.55 (+5.9 pts, peak 71% of target)

### Trade list

| # | entry | path | side | regime | RR | lot | entry px | SL | stop pts | TP | exit | MFE pts | MAE pts | peak% | pts | hold | R | P&L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 06-02 14:25 | DFVG | buy | RANGING | 1.0 | 0.04 | 4525.10 | 4505.88 | 19.2 | 4544.32 | stop_loss | 13.1 | 0.3 | 68% | +4.6 | 1.2h | +0.24 | $+18.42 |
| 2 | 07-09 13:15 | DFVG | sell | WEAK | 1.5 | 0.04 | 4130.00 | 4154.24 | 24.2 | 4093.63 | stop_loss | 27.0 | 18.4 | 74% | +8.9 | 21.1h | +0.37 | $+35.57 |
| 3 | 07-14 12:35 | DFVG | sell | STRONG | 2.5 | 0.03 | 4095.00 | 4123.92 | 28.9 | 4022.69 | stop_loss | 71.7 | 13.7 | 99% | +57.0 | 20.3h | +1.97 | $+171.11 |
| 4 | 07-28 11:15 | DFVG | buy | RANGING | 1.0 | 0.03 | 4030.30 | 4006.09 | 24.2 | 4054.51 | stop_loss | 17.1 | 19.2 | 71% | +5.9 | 5.9h | +0.24 | $+17.55 |

### Matched random-entry control

Same trade count (4), same side mix, stop distances and RRs resampled from the real trades, entry bars drawn uniformly from the same window, executed by the same engine.

* real: PF **inf**, net **$+242.65**
* null over 500 trials: PF median 0.99, p95 10.00; net median $-0.84, p95 $+315.01
* real beats **89%** of draws on PF, **89%** on net

