# Kalman adverse-liquidity gate — diagnostic

Trades: 11534  (IS 9906 / OOS 1628)

Thresholds are chosen on the IS tables only. The OOS tables are printed for information and must not be consulted while picking a number.

## IS

### All modes

| adverse_atr     |    n |   win_rate |     mean_R |   total_R |
|:----------------|-----:|-----------:|-----------:|----------:|
| [0.0, 0.25)     |   35 |   0.371429 | -0.38624   |  -13.5184 |
| [0.25, 0.5)     |  504 |   0.436508 | -0.153229  |  -77.2274 |
| [0.5, 0.75)     |  651 |   0.451613 | -0.145145  |  -94.4893 |
| [0.75, 1.0)     |  662 |   0.406344 | -0.229721  | -152.075  |
| [1.0, 2.0)      | 1810 |   0.442541 | -0.119632  | -216.534  |
| [2.0, inf)      | 5665 |   0.427361 | -0.119306  | -675.869  |
| no adverse pool |  579 |   0.488774 |  0.0196167 |   11.3581 |

### TREND (n=7311)

| adverse_atr     |    n |   win_rate |     mean_R |    total_R |
|:----------------|-----:|-----------:|-----------:|-----------:|
| [0.0, 0.25)     |    9 |   0.333333 | -0.46816   |   -4.21344 |
| [0.25, 0.5)     |  121 |   0.404959 | -0.155436  |  -18.8077  |
| [0.5, 0.75)     |  159 |   0.471698 | -0.0577389 |   -9.18048 |
| [0.75, 1.0)     |  209 |   0.401914 | -0.181898  |  -38.0168  |
| [1.0, 2.0)      |  982 |   0.45112  | -0.058355  |  -57.3046  |
| [2.0, inf)      | 5348 |   0.428945 | -0.109407  | -585.111   |
| no adverse pool |  483 |   0.513458 |  0.085376  |   41.2366  |

### TREND / BUY (n=5115)

| adverse_atr     |    n |   win_rate |     mean_R |    total_R |
|:----------------|-----:|-----------:|-----------:|-----------:|
| [0.0, 0.25)     |    5 |   0.2      | -0.947922  |   -4.73961 |
| [0.25, 0.5)     |   98 |   0.479592 |  0.0182801 |    1.79145 |
| [0.5, 0.75)     |  134 |   0.492537 | -0.0317913 |   -4.26003 |
| [0.75, 1.0)     |  165 |   0.406061 | -0.17792   |  -29.3568  |
| [1.0, 2.0)      |  750 |   0.46     | -0.0359999 |  -27       |
| [2.0, inf)      | 3670 |   0.464578 | -0.0329339 | -120.867   |
| no adverse pool |  293 |   0.518771 |  0.091029  |   26.6715  |

### TREND / SELL (n=2196)

| adverse_atr     |    n |   win_rate |     mean_R |    total_R |
|:----------------|-----:|-----------:|-----------:|-----------:|
| [0.0, 0.25)     |    4 |  0.5       |  0.131543  |    0.52617 |
| [0.25, 0.5)     |   23 |  0.0869565 | -0.895617  |  -20.5992  |
| [0.5, 0.75)     |   25 |  0.36      | -0.196818  |   -4.92045 |
| [0.75, 1.0)     |   44 |  0.386364  | -0.196818  |   -8.66001 |
| [1.0, 2.0)      |  232 |  0.422414  | -0.130624  |  -30.3047  |
| [2.0, inf)      | 1678 |  0.351013  | -0.276665  | -464.243   |
| no adverse pool |  190 |  0.505263  |  0.0766585 |   14.5651  |

### RANGE (n=2595)

| adverse_atr     |   n |   win_rate |    mean_R |    total_R |
|:----------------|----:|-----------:|----------:|-----------:|
| [0.0, 0.25)     |  26 |   0.384615 | -0.357883 |   -9.30496 |
| [0.25, 0.5)     | 383 |   0.446475 | -0.152532 |  -58.4197  |
| [0.5, 0.75)     | 492 |   0.445122 | -0.173392 |  -85.3088  |
| [0.75, 1.0)     | 453 |   0.408389 | -0.251785 | -114.059   |
| [1.0, 2.0)      | 828 |   0.432367 | -0.192306 | -159.23    |
| [2.0, inf)      | 317 |   0.400631 | -0.286304 |  -90.7583  |
| no adverse pool |  96 |   0.364583 | -0.311235 |  -29.8786  |

### RANGE / BUY (n=2527)

| adverse_atr     |   n |   win_rate |    mean_R |    total_R |
|:----------------|----:|-----------:|----------:|-----------:|
| [0.0, 0.25)     |  23 |   0.391304 | -0.36346  |   -8.35958 |
| [0.25, 0.5)     | 363 |   0.438017 | -0.168538 |  -61.1794  |
| [0.5, 0.75)     | 478 |   0.441423 | -0.181004 |  -86.52    |
| [0.75, 1.0)     | 447 |   0.411633 | -0.242675 | -108.476   |
| [1.0, 2.0)      | 810 |   0.42963  | -0.198302 | -160.624   |
| [2.0, inf)      | 310 |   0.396774 | -0.294938 |  -91.4309  |
| no adverse pool |  96 |   0.364583 | -0.311235 |  -29.8786  |

### RANGE / SELL (n=68)

| adverse_atr     |   n |   win_rate |      mean_R |   total_R |
|:----------------|----:|-----------:|------------:|----------:|
| [0.0, 0.25)     |   3 |   0.333333 |  -0.315125  | -0.945376 |
| [0.25, 0.5)     |  20 |   0.6      |   0.137986  |  2.75972  |
| [0.5, 0.75)     |  14 |   0.571429 |   0.0865136 |  1.21119  |
| [0.75, 1.0)     |   6 |   0.166667 |  -0.930479  | -5.58287  |
| [1.0, 2.0)      |  18 |   0.555556 |   0.0774836 |  1.3947   |
| [2.0, inf)      |   7 |   0.571429 |   0.0960768 |  0.672537 |
| no adverse pool |   0 | nan        | nan         |  0        |

### Veto simulation — IS


**TREND** baseline: n=7311, total_R=-671.40, mean_R=-0.0918, win_rate=0.4371

|   threshold_atr |   vetoed_n |   vetoed_mean_R |   kept_n |   kept_win_rate |   kept_wr_se |   kept_mean_R |   mean_R_delta |
|----------------:|-----------:|----------------:|---------:|----------------:|-------------:|--------------:|---------------:|
|            0.25 |          9 |       -0.46816  |     7302 |        0.437277 |   0.00580503 |    -0.09137   |    0.000463837 |
|            0.5  |        130 |       -0.177086 |     7181 |        0.437822 |   0.00585455 |    -0.0902905 |    0.00154335  |
|            0.75 |        289 |       -0.111424 |     7022 |        0.437055 |   0.0059193  |    -0.0910275 |    0.000806279 |
|            1    |        498 |       -0.141001 |     6813 |        0.438133 |   0.00601105 |    -0.0882399 |    0.00359389  |

**RANGE** baseline: n=2595, total_R=-546.96, mean_R=-0.2108, win_rate=0.4258

|   threshold_atr |   vetoed_n |   vetoed_mean_R |   kept_n |   kept_win_rate |   kept_wr_se |   kept_mean_R |   mean_R_delta |
|----------------:|-----------:|----------------:|---------:|----------------:|-------------:|--------------:|---------------:|
|            0.25 |         26 |       -0.357883 |     2569 |        0.426236 |   0.00975685 |     -0.209285 |     0.00148884 |
|            0.5  |        409 |       -0.165586 |     2186 |        0.42269  |   0.0105655  |     -0.219229 |    -0.00845469 |
|            0.75 |        901 |       -0.169848 |     1694 |        0.416175 |   0.0119763  |     -0.232541 |    -0.0217674  |
|            1    |       1354 |       -0.197261 |     1241 |        0.419017 |   0.0140059  |     -0.225517 |    -0.014743   |

## OOS

### All modes

| adverse_atr     |   n |   win_rate |     mean_R |   total_R |
|:----------------|----:|-----------:|-----------:|----------:|
| [0.0, 0.25)     |   5 |   0.4      | -0.238031  |  -1.19015 |
| [0.25, 0.5)     | 114 |   0.342105 | -0.334195  | -38.0983  |
| [0.5, 0.75)     | 129 |   0.395349 | -0.258225  | -33.3111  |
| [0.75, 1.0)     |  92 |   0.336957 | -0.399704  | -36.7728  |
| [1.0, 2.0)      | 288 |   0.402778 | -0.185818  | -53.5156  |
| [2.0, inf)      | 969 |   0.50258  |  0.0507192 |  49.1469  |
| no adverse pool |  31 |   0.322581 | -0.384624  | -11.9233  |

### TREND (n=1198)

| adverse_atr     |   n |   win_rate |     mean_R |    total_R |
|:----------------|----:|-----------:|-----------:|-----------:|
| [0.0, 0.25)     |   2 |   0.5      |  0.143177  |   0.286353 |
| [0.25, 0.5)     |  28 |   0.428571 | -0.0664584 |  -1.86083  |
| [0.5, 0.75)     |  34 |   0.352941 | -0.264092  |  -8.97914  |
| [0.75, 1.0)     |  25 |   0.32     | -0.297305  |  -7.43262  |
| [1.0, 2.0)      | 172 |   0.424419 | -0.115036  | -19.7862   |
| [2.0, inf)      | 916 |   0.507642 |  0.066461  |  60.8783   |
| no adverse pool |  21 |   0.428571 | -0.198149  |  -4.16112  |

### TREND / BUY (n=718)

| adverse_atr     |   n |   win_rate |      mean_R |    total_R |
|:----------------|----:|-----------:|------------:|-----------:|
| [0.0, 0.25)     |   1 |   0        | -1.04434    |  -1.04434  |
| [0.25, 0.5)     |  17 |   0.411765 | -0.131941   |  -2.24299  |
| [0.5, 0.75)     |  27 |   0.296296 | -0.407017   | -10.9894   |
| [0.75, 1.0)     |  17 |   0.235294 | -0.534438   |  -9.08544  |
| [1.0, 2.0)      | 110 |   0.318182 | -0.390197   | -42.9217   |
| [2.0, inf)      | 530 |   0.488679 | -0.00181665 |  -0.962827 |
| no adverse pool |  16 |   0.5625   |  0.149664   |   2.39462  |

### TREND / SELL (n=480)

| adverse_atr     |   n |   win_rate |     mean_R |   total_R |
|:----------------|----:|-----------:|-----------:|----------:|
| [0.0, 0.25)     |   1 |   1        |  1.33069   |  1.33069  |
| [0.25, 0.5)     |  11 |   0.454545 |  0.0347416 |  0.382157 |
| [0.5, 0.75)     |   7 |   0.571429 |  0.287188  |  2.01031  |
| [0.75, 1.0)     |   8 |   0.5      |  0.206602  |  1.65282  |
| [1.0, 2.0)      |  62 |   0.612903 |  0.373153  | 23.1355   |
| [2.0, inf)      | 386 |   0.533679 |  0.16021   | 61.8411   |
| no adverse pool |   5 |   0        | -1.31115   | -6.55574  |

### RANGE (n=430)

| adverse_atr     |   n |   win_rate |    mean_R |   total_R |
|:----------------|----:|-----------:|----------:|----------:|
| [0.0, 0.25)     |   3 |   0.333333 | -0.492169 |  -1.47651 |
| [0.25, 0.5)     |  86 |   0.313953 | -0.421366 | -36.2374  |
| [0.5, 0.75)     |  95 |   0.410526 | -0.256126 | -24.332   |
| [0.75, 1.0)     |  67 |   0.343284 | -0.437913 | -29.3401  |
| [1.0, 2.0)      | 116 |   0.37069  | -0.290771 | -33.7294  |
| [2.0, inf)      |  53 |   0.415094 | -0.221348 | -11.7314  |
| no adverse pool |  10 |   0.1      | -0.776222 |  -7.76222 |

### RANGE / BUY (n=424)

| adverse_atr     |   n |   win_rate |    mean_R |   total_R |
|:----------------|----:|-----------:|----------:|----------:|
| [0.0, 0.25)     |   3 |   0.333333 | -0.492169 |  -1.47651 |
| [0.25, 0.5)     |  83 |   0.301205 | -0.454163 | -37.6955  |
| [0.5, 0.75)     |  95 |   0.410526 | -0.256126 | -24.332   |
| [0.75, 1.0)     |  67 |   0.343284 | -0.437913 | -29.3401  |
| [1.0, 2.0)      | 113 |   0.362832 | -0.31013  | -35.0447  |
| [2.0, inf)      |  53 |   0.415094 | -0.221348 | -11.7314  |
| no adverse pool |  10 |   0.1      | -0.776222 |  -7.76222 |

### RANGE / SELL (n=6)

| adverse_atr     |   n |   win_rate |     mean_R |   total_R |
|:----------------|----:|-----------:|-----------:|----------:|
| [0.0, 0.25)     |   0 | nan        | nan        |   0       |
| [0.25, 0.5)     |   3 |   0.666667 |   0.486024 |   1.45807 |
| [0.5, 0.75)     |   0 | nan        | nan        |   0       |
| [0.75, 1.0)     |   0 | nan        | nan        |   0       |
| [1.0, 2.0)      |   3 |   0.666667 |   0.438444 |   1.31533 |
| [2.0, inf)      |   0 | nan        | nan        |   0       |
| no adverse pool |   0 | nan        | nan        |   0       |

### Veto simulation — OOS


**TREND** baseline: n=1198, total_R=18.94, mean_R=0.0158, win_rate=0.4841

|   threshold_atr |   vetoed_n |   vetoed_mean_R |   kept_n |   kept_win_rate |   kept_wr_se |   kept_mean_R |   mean_R_delta |
|----------------:|-----------:|----------------:|---------:|----------------:|-------------:|--------------:|---------------:|
|            0.25 |          2 |       0.143177  |     1196 |        0.484114 |    0.0144506 |     0.0156007 |   -0.000212982 |
|            0.5  |         30 |      -0.0524827 |     1168 |        0.485445 |    0.0146239 |     0.0175678 |    0.00175419  |
|            0.75 |         64 |      -0.1649    |     1134 |        0.489418 |    0.0148445 |     0.0260127 |    0.010199    |
|            1    |         89 |      -0.202093  |     1109 |        0.493237 |    0.0150129 |     0.0333012 |    0.0174875   |

**RANGE** baseline: n=430, total_R=-144.61, mean_R=-0.3363, win_rate=0.3628

|   threshold_atr |   vetoed_n |   vetoed_mean_R |   kept_n |   kept_win_rate |   kept_wr_se |   kept_mean_R |   mean_R_delta |
|----------------:|-----------:|----------------:|---------:|----------------:|-------------:|--------------:|---------------:|
|            0.25 |          3 |       -0.492169 |      427 |        0.362998 |    0.0232706 |     -0.335205 |    0.0010951   |
|            0.5  |         89 |       -0.423752 |      341 |        0.375367 |    0.0262218 |     -0.313476 |    0.0228247   |
|            0.75 |        184 |       -0.337206 |      246 |        0.361789 |    0.0306367 |     -0.335623 |    0.000677473 |
|            1    |        251 |       -0.364088 |      179 |        0.368715 |    0.0360605 |     -0.297335 |    0.0389647   |

---

## Verdict — STOP, no effect (Task A3, Step 4)

**Decision: the adverse-pool entry gate is NOT supported. Tasks A4 and A5 are not done.**

Applied against the pre-registered stop condition: *no candidate threshold produces a
`mean_R_delta` that is both positive and larger than the noise in `kept_win_rate`.*

Basis — IS only (9,906 trades, 2022-01-01 → 2025-12-31; 2026 held out):

| mode | baseline | best `mean_R_delta` | win-rate move | `kept_wr_se` | move in s.e. |
|---|---|---|---|---|---|
| TREND | n=7311, mean_R −0.0918, WR 0.4371 | +0.0036 @ 1.0 ATR | 0.4371 → 0.4381 | 0.0060 | **0.17 σ** |
| RANGE | n=2595, mean_R −0.2108, WR 0.4258 | +0.0015 @ 0.25 ATR | 0.4258 → 0.4262 | 0.0098 | **0.05 σ** |

Every win-rate move is inside one-fifth of a standard error. In RANGE the `mean_R_delta`
is *negative* at three of the four thresholds (−0.008, −0.022, −0.015) — vetoing near-pool
trades there makes the kept population worse, not better. There is no threshold to carry
into Task A4.

The bucket tables show no monotone relationship between adverse-pool distance and
outcome. IS `mean_R` by bucket runs −0.386 / −0.153 / −0.145 / −0.230 / −0.120 / −0.119 —
non-monotone, and the two widest buckets are indistinguishable from each other. The
nearest bucket `[0, 0.25)` is indeed the worst cell in IS (mean_R −0.386), which is the
direction the hypothesis predicted, but it holds 35 of 9,906 trades (0.35%). Even if that
cell were real it is too rare to move the strategy, and OOS it is 5 trades.

### The one bucket that looked alive, and why it is not

`no adverse pool` was the only positive `mean_R` bucket in IS (n=579, WR 0.4888,
mean_R +0.0196 against a −0.123 base) — a ~2.7σ win-rate move. It was not part of the
pre-registered veto family, so it is a post-hoc observation. It does not survive:

| slice | n | win rate | mean_R |
|---|---|---|---|
| IS, all modes | 579 | 0.4888 | **+0.0196** |
| IS, TREND | 483 | 0.5135 | **+0.0854** |
| IS, RANGE | 96 | 0.3646 | −0.3112 |
| OOS, all modes | 31 | 0.3226 | **−0.3846** |
| OOS, TREND | 21 | 0.4286 | −0.1981 |

It is IS-TREND-only, already negative in IS RANGE, and flips hard negative in 2026. Noise
in a favourable slice, not a gate. Recorded here so a later reader does not rediscover it
and mistake it for a missed edge.

### What this does and does not prove

- The base strategy is strongly negative-expectancy in this configuration — every year
  ran PF < 1.0 (2022 0.69 / 2023 0.85 / 2024 0.76 / see summaries). The diagnostic asks
  only whether adverse-pool distance *separates* outcomes within that population. It does
  not. It says nothing about kalman's viability either way.
- Because the base is negative, `total_R` improves at almost every threshold purely by
  trading less of a losing distribution. That is the artifact the R-multiple + win-rate
  criterion was written to reject, and it is why `total_R` is not cited above.
- Sizing is equity-proportional and the account decays hard within each year, so dollar
  PnL partly measures *when* a trade happened. All figures above are R-multiples
  (`pnl / r_dollars`), which divides that out.
- This is **not** a "the gate never fired" plumbing null (the failure mode Task A4 Step 1
  guards against). The gate was never wired into the strategy; distances were computed
  directly from the trade log against a full-span pool context, so a non-firing gate
  cannot be masquerading as a null result here.
- OOS is printed for information only and was not consulted in choosing any threshold —
  there was no threshold to choose. It is used above solely to retire the post-hoc
  `no adverse pool` observation.

Consistent with the prior from the indicator calibration, where the `type_equal` and
`type_session` betas were also near zero.
