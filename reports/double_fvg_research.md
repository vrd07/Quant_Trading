# H4 double-FVG rule — research gate

XAUUSD 5m 2022-01-02 -> 2026-08-07 (324,631 bars, 7,269 H4 bars). Strict fills, cost $0.2/side/point, fixed 0.02 lot, $5,000 capital.
IS = 2022-01-01..2024-12-31   OOS = 2025-01-01..end (never optimised on).

## 1. As-specified cell (EA v3.00 shipped defaults)

`min_atr=0.25 pair_window=12 max_age=40 lookback=60 tap=50/60 buf=1.5xATR5m rr=2.0`

Signals generated: **157** over 4.6 years.

**exit = rr**
```
  full   n=156  WR= 39.1%  PF=1.10  net=$  +295.50 exp=$  +1.89 DD=-16.28%  dayWR= 40.0% worstDay= -2.04R  Sh= 0.53 t/yr= 34.1
  IS     n=101  WR= 38.6%  PF=1.14  net=$  +158.05 exp=$  +1.56 DD= -5.43%  dayWR= 38.8% worstDay= -2.04R  Sh= 0.87 t/yr= 34.0
  OOS    n=55   WR= 40.0%  PF=1.08  net=$  +137.46 exp=$  +2.50 DD=-15.21%  dayWR= 42.3% worstDay= -2.04R  Sh= 0.45 t/yr= 34.9
  2022   n=36   WR= 41.7%  PF=1.30  net=$  +106.16 exp=$  +2.95 DD= -1.73%  dayWR= 40.0% worstDay= -1.05R  Sh= 1.87 t/yr= 37.5
  2023   n=30   WR= 46.7%  PF=1.70  net=$  +190.65 exp=$  +6.36 DD= -2.44%  dayWR= 46.7% worstDay= -1.07R  Sh= 3.65 t/yr= 30.7
  2024   n=35   WR= 28.6%  PF=0.75  net=$  -138.77 exp=$  -3.96 DD= -2.70%  dayWR= 30.3% worstDay= -2.04R  Sh=-1.97 t/yr= 36.5
  2025   n=32   WR= 34.4%  PF=0.79  net=$  -157.70 exp=$  -4.93 DD= -8.95%  dayWR= 37.9% worstDay= -2.04R  Sh=-1.73 t/yr= 32.7
  2026   n=23   WR= 47.8%  PF=1.27  net=$  +295.16 exp=$ +12.83 DD= -7.66%  dayWR= 47.8% worstDay= -1.02R  Sh= 1.58 t/yr= 39.3
```

**exit = ladder**
```
  full   n=156  WR= 55.1%  PF=1.09  net=$  +179.08 exp=$  +1.15 DD= -7.17%  dayWR= 55.4% worstDay= -2.04R  Sh= 0.45 t/yr= 34.1
  IS     n=101  WR= 53.5%  PF=0.87  net=$  -113.40 exp=$  -1.12 DD= -4.15%  dayWR= 53.1% worstDay= -2.04R  Sh=-0.89 t/yr= 34.0
  OOS    n=55   WR= 58.2%  PF=1.24  net=$  +292.47 exp=$  +5.32 DD= -6.29%  dayWR= 59.6% worstDay= -2.04R  Sh= 1.36 t/yr= 34.9
  2022   n=36   WR= 52.8%  PF=0.73  net=$   -81.45 exp=$  -2.26 DD= -2.44%  dayWR= 51.4% worstDay= -1.05R  Sh=-2.19 t/yr= 37.5
  2023   n=30   WR= 63.3%  PF=1.61  net=$  +111.47 exp=$  +3.72 DD= -1.40%  dayWR= 64.3% worstDay= -1.07R  Sh= 3.16 t/yr= 30.7
  2024   n=35   WR= 45.7%  PF=0.66  net=$  -143.42 exp=$  -4.10 DD= -3.15%  dayWR= 45.5% worstDay= -2.04R  Sh=-2.75 t/yr= 36.5
  2025   n=32   WR= 53.1%  PF=1.10  net=$   +49.89 exp=$  +1.56 DD= -3.77%  dayWR= 55.2% worstDay= -2.04R  Sh= 0.67 t/yr= 32.7
  2026   n=23   WR= 65.2%  PF=1.35  net=$  +242.58 exp=$ +10.55 DD= -4.86%  dayWR= 65.2% worstDay= -1.02R  Sh= 1.89 t/yr= 39.3
```

## 2. backtest.md gates (OOS 2025-01-01 onwards, exit=ladder)

| Gate | Value | Verdict |
|---|---|---|
| G1 daily win-rate >= 70% | 59.6% | **FAIL** |
| G2 worst day >= -2R | -2.04R | **FAIL** |
| G3 profit factor >= 1.4 | 1.24 | **FAIL** |
| G4 Sharpe >= 1.0 | 1.36 | PASS |
| G5 max DD <= 12% | -6.29% | PASS |
| G6 trades/year >= 60 | 34.9 | **FAIL** |

## 3. Matched random-entry control

**exit=rr** — real PF 1.10, net $+295.50
  random null over 200 trials: PF median 1.06, mean 1.10, p95 1.69; net mean $+171.51, p95 $+1,364.21
  real result beats **56%** of random draws.

**exit=ladder** — real PF 1.09, net $+179.08
  random null over 200 trials: PF median 1.02, mean 1.09, p95 1.64; net mean $+104.48, p95 $+1,041.03
  real result beats **58%** of random draws.

## 4. Sensitivity sweep (IS-selected, OOS-reported)

If the 50-60% tap band and the 0.25xATR gap filter are real structure, neighbouring cells should behave similarly. A jagged surface means noise.

| min_atr | tap_min | rr | IS PF | IS n | OOS PF | OOS n | OOS net |
|---|---|---|---|---|---|---|---|
| 0.15 | 50 | 1.5 | 0.76 | 138 | 1.33 | 80 | $+437 |
| 0.15 | 50 | 2.0 | 0.70 | 137 | 0.95 | 79 | $-101 |
| 0.15 | 50 | 3.0 | 0.80 | 136 | 1.07 | 78 | $+160 |
| 0.15 | 62 | 1.5 | 0.84 | 126 | 1.31 | 79 | $+360 |
| 0.15 | 62 | 2.0 | 0.83 | 125 | 1.09 | 79 | $+135 |
| 0.15 | 62 | 3.0 | 0.84 | 125 | 1.15 | 79 | $+284 |
| 0.15 | 75 | 1.5 | 0.80 | 120 | 1.25 | 74 | $+235 |
| 0.15 | 75 | 2.0 | 0.77 | 119 | 1.54 | 74 | $+576 |
| 0.15 | 75 | 3.0 | 1.02 | 117 | 1.38 | 74 | $+590 |
| 0.25 | 50 | 1.5 | 0.99 | 101 | 2.04 | 55 | $+791 |
| 0.25 | 50 | 2.0 | 0.87 | 101 | 1.24 | 55 | $+292 |
| 0.25 | 50 | 3.0 | 0.91 | 101 | 1.18 | 55 | $+291 |
| 0.25 | 62 | 1.5 | 1.05 | 94 | 2.31 | 55 | $+781 |
| 0.25 | 62 | 2.0 | 1.13 | 94 | 1.73 | 55 | $+624 |
| 0.25 | 62 | 3.0 | 1.09 | 94 | 1.35 | 55 | $+483 |
| 0.25 | 75 | 1.5 | 1.12 | 91 | 1.63 | 54 | $+382 |
| 0.25 | 75 | 2.0 | 1.15 | 91 | 1.91 | 54 | $+624 |
| 0.25 | 75 | 3.0 | 1.44 | 90 | 1.62 | 54 | $+698 |
| 0.4 | 50 | 1.5 | 0.78 | 59 | 1.53 | 30 | $+258 |
| 0.4 | 50 | 2.0 | 0.78 | 59 | 0.79 | 30 | $-175 |
| 0.4 | 50 | 3.0 | 0.83 | 59 | 0.88 | 30 | $-117 |
| 0.4 | 62 | 1.5 | 0.69 | 55 | 1.74 | 31 | $+318 |
| 0.4 | 62 | 2.0 | 0.91 | 55 | 1.29 | 31 | $+163 |
| 0.4 | 62 | 3.0 | 1.05 | 55 | 1.02 | 31 | $+21 |
| 0.4 | 75 | 1.5 | 0.90 | 51 | 0.68 | 29 | $-157 |
| 0.4 | 75 | 2.0 | 1.02 | 51 | 0.84 | 29 | $-82 |
| 0.4 | 75 | 3.0 | 1.38 | 51 | 1.06 | 29 | $+40 |

Cells with IS PF > 1.0: **10/27**. Positive on BOTH IS and OOS: **9/27**.

## 5. Session filter variant

The EA ships `InpUseSession=true` with hours 8-20 in BROKER time. Dukascopy here is UTC, so 06:00-18:00 UTC approximates a GMT+2 broker.

```
  all hours  full n=156  WR= 55.1%  PF=1.09  net=$  +179.08 exp=$  +1.15 DD= -7.17%  dayWR= 55.4% worstDay= -2.04R  Sh= 0.45 t/yr= 34.1
             OOS  n=55   WR= 58.2%  PF=1.24  net=$  +292.47 exp=$  +5.32 DD= -6.29%  dayWR= 59.6% worstDay= -2.04R  Sh= 1.36 t/yr= 34.9
```
```
  06-18 UTC  full n=125  WR= 52.0%  PF=1.12  net=$  +182.10 exp=$  +1.46 DD= -5.08%  dayWR= 52.9% worstDay= -2.04R  Sh= 0.71 t/yr= 27.3
             OOS  n=41   WR= 58.5%  PF=1.48  net=$  +343.44 exp=$  +8.38 DD= -4.26%  dayWR= 61.5% worstDay= -2.04R  Sh= 2.74 t/yr= 26.0
```

## 6. Under $5k risk enforcement (daily cap $150, trailing DD halt $250)

```
  exit=rr      n=93   WR= 38.7%  PF=1.14  net=$  +147.17 exp=$  +1.58 DD= -5.43%  dayWR= 38.9% worstDay= -2.04R  Sh= 0.91 t/yr= 35.7
```
```
  exit=ladder  n=137  WR= 53.3%  PF=0.84  net=$  -264.29 exp=$  -1.93 DD= -7.17%  dayWR= 53.5% worstDay= -2.04R  Sh=-0.97 t/yr= 33.7
```

## 7. Ablation — is the DOUBLE, and the FIRST, actually load-bearing?

The rule's two distinctive claims are (a) require TWO same-direction gaps and (b) trade the FIRST-formed one. If dropping either changes nothing, the extra conditions are decoration.

```
  double, arm FIRST (spec)     full n=156  WR= 55.1%  PF=1.09  net=$  +179.08 exp=$  +1.15 DD= -7.17%  dayWR= 55.4% worstDay= -2.04R  Sh= 0.45 t/yr= 34.1
                               OOS  n=55   WR= 58.2%  PF=1.24  net=$  +292.47 exp=$  +5.32 DD= -6.29%  dayWR= 59.6% worstDay= -2.04R  Sh= 1.36 t/yr= 34.9
```
```
  double, arm SECOND           full n=337  WR= 52.8%  PF=0.98  net=$  -101.54 exp=$  -0.30 DD=-15.04%  dayWR= 52.3% worstDay= -2.05R  Sh=-0.15 t/yr= 73.7
                               OOS  n=126  WR= 52.4%  PF=0.88  net=$  -319.05 exp=$  -2.53 DD=-15.04%  dayWR= 52.2% worstDay= -2.04R  Sh=-0.86 t/yr= 79.9
```
```
  single gap, no pair needed   full n=548  WR= 52.9%  PF=1.04  net=$  +263.98 exp=$  +0.48 DD=-22.50%  dayWR= 52.9% worstDay= -3.05R  Sh= 0.23 t/yr=119.6
                               OOS  n=194  WR= 52.1%  PF=0.96  net=$  -158.67 exp=$  -0.82 DD=-22.50%  dayWR= 52.9% worstDay= -3.05R  Sh=-0.27 t/yr=122.6
```

## 8. Verdict

* backtest.md gates cleared on OOS: **2/6**.
* Spec cell full-span PF **1.09** (ladder) / **1.10** (fixed TP), on **34 trades/year** against a G6 floor of 60.
* It beats **58%** of matched random-entry draws (null median PF 1.02, p95 1.64). A real edge sits near 100%; 50% is a coin flip.
* Ablation: arming the FIRST gap (PF 1.09) vs the SECOND (PF 0.98) vs no pair requirement at all (PF 1.04) — all three sit inside the random null's spread, so neither the 'double' nor the 'first' is load-bearing.
* Year map is sign-unstable: no variant is positive in every calendar year, which is the repo's standing kill criterion.

**Do not enable live.** The rule is indistinguishable from trading gold at a fixed R:R with a structural stop.

