# Daily Swing Trend-Follower — Research (XAUUSD)

**Script:** `scripts/research_daily_swing_trend.py` · **Spec:** `docs/superpowers/specs/2026-07-01-daily-swing-trend-design.md`

IN-SAMPLE: 2484 daily bars (2016-01-03 -> 2023-12-29). OUT-OF-SAMPLE: 716 daily bars (2024-01-01 -> 2026-07-01).

## Stage 1 — parameter grid (in-sample)

Winner: Donchian(55), ATR-mult 3.0, confirm_bars 1, ATR-expansion-required False.

## Stage 2 — confirmation-filter layering (in-sample)

| HTF-align | Min-penetration | N | Win% | PF | Net$ | MaxDD% |
|---|---|---:|---:|---:|---:|---:|
| False | False | 41 | 43.9% | 1.52 | +681 | -4.9% |
| False | True | 39 | 46.2% | 1.64 | +766 | -4.9% |
| True | False | 37 | 48.6% | 1.67 | +800 | -4.9% |
| True | True | 36 | 50.0% | 1.73 | +830 | -4.9% |

**Final parameters:** `{'donch_n': 55, 'atr_mult': 3.0, 'confirm_bars': 1, 'atr_expansion_required': False, 'htf_ema_period': 200, 'min_penetration_atr': 0.1, 'cooldown_bars': 2}`

## Walk-forward result

| Slice | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| In-sample | 36 | 50.0% | 1.73 | +830 | -4.9% |
| Out-of-sample | 13 | 76.9% | 8.78 | +3,131 | -2.4% |
| OOS, 2x cost | 13 | 76.9% | 8.71 | +3,121 | -2.5% |

## Per-year breakdown (full span)

| Year | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| 2016 | 3 | 0.0% | 0.00 | -212 |
| 2017 | 6 | 33.3% | 0.77 | -31 |
| 2018 | 2 | 100.0% | inf | +273 |
| 2019 | 5 | 60.0% | 4.88 | +200 |
| 2020 | 5 | 60.0% | 2.34 | +350 |
| 2021 | 4 | 50.0% | 0.88 | -20 |
| 2022 | 5 | 60.0% | 3.09 | +104 |
| 2023 | 6 | 50.0% | 1.64 | +167 |
| 2024 | 4 | 75.0% | 5.01 | +517 |
| 2025 | 6 | 83.3% | 10.87 | +1,605 |
| 2026 | 3 | 66.7% | 10.34 | +1,037 |

## Verdict

- PF > 1.3 both IS and OOS: PASS (IS 1.73, OOS 8.78)
- Positive/flat every calendar year: FAIL
- Max drawdown within $5k live cap (<=5%): PASS (IS -4.9%, OOS -2.4%)
- Cost-robust at 2x cost, OOS PF > 1.3: PASS (OOS-stress PF 8.71)

❌ **DOES NOT CLEAR THE GATE.** Not shipped. Document as a rejected research spike in CLAUDE.md (same treatment as EMA-retest / EURUSD / GBPUSD hunts) and save a `project_*` memory noting the outcome.