# Fourier transform edge research — XAUUSD

Data: XAUUSD_5m_real.csv, slices 2025 (Feb–Dec) / 2026 (Jan–Jul). Cost 0.2 pts/side (strict 0.5). Fills: next-bar open.


## Timeframe 15m — 34048 bars

### A. Fourier extrapolation — 15m
IC = Spearman(predicted H-bar move, realized H-bar move), per slice.

| W | K | H | trend | IC 2025 | IC 2026 | sign-hit 2025 | sign-hit 2026 |
|---|---|---|-------|---------|---------|---------------|---------------|
| 128 | 3 | 8 | n | +0.013 | -0.023 | 0.500 | 0.492 |
| 128 | 3 | 8 | Y | +0.017 | -0.012 | 0.503 | 0.491 |
| 128 | 3 | 16 | n | +0.009 | +0.004 | 0.502 | 0.516 |
| 128 | 3 | 16 | Y | +0.018 | +0.025 | 0.506 | 0.516 |
| 128 | 3 | 32 | n | -0.022 | -0.043 | 0.498 | 0.484 |
| 128 | 3 | 32 | Y | +0.006 | -0.017 | 0.511 | 0.500 |
| 128 | 5 | 8 | n | +0.016 | +0.008 | 0.507 | 0.502 |
| 128 | 5 | 8 | Y | +0.019 | +0.014 | 0.512 | 0.498 |
| 128 | 5 | 16 | n | +0.012 | -0.006 | 0.506 | 0.509 |
| 128 | 5 | 16 | Y | +0.017 | +0.017 | 0.507 | 0.515 |
| 128 | 5 | 32 | n | -0.022 | -0.043 | 0.497 | 0.486 |
| 128 | 5 | 32 | Y | +0.006 | -0.021 | 0.510 | 0.495 |
| 256 | 3 | 8 | n | +0.023 | -0.030 | 0.513 | 0.495 |
| 256 | 3 | 8 | Y | +0.026 | -0.017 | 0.516 | 0.504 |
| 256 | 3 | 16 | n | +0.019 | -0.032 | 0.511 | 0.490 |
| 256 | 3 | 16 | Y | +0.021 | -0.015 | 0.517 | 0.501 |
| 256 | 3 | 32 | n | +0.003 | +0.001 | 0.495 | 0.500 |
| 256 | 3 | 32 | Y | +0.005 | +0.008 | 0.509 | 0.506 |
| 256 | 5 | 8 | n | +0.030 | -0.046 | 0.509 | 0.485 |
| 256 | 5 | 8 | Y | +0.035 | -0.036 | 0.515 | 0.496 |
| 256 | 5 | 16 | n | +0.028 | -0.037 | 0.508 | 0.490 |
| 256 | 5 | 16 | Y | +0.032 | -0.023 | 0.517 | 0.503 |
| 256 | 5 | 32 | n | +0.016 | +0.025 | 0.502 | 0.515 |
| 256 | 5 | 32 | Y | +0.017 | +0.016 | 0.510 | 0.514 |
| 512 | 3 | 8 | n | +0.026 | -0.008 | 0.511 | 0.492 |
| 512 | 3 | 8 | Y | +0.019 | +0.013 | 0.512 | 0.507 |
| 512 | 3 | 16 | n | +0.032 | -0.008 | 0.517 | 0.493 |
| 512 | 3 | 16 | Y | +0.023 | +0.024 | 0.521 | 0.515 |
| 512 | 3 | 32 | n | +0.049 | -0.023 | 0.529 | 0.502 |
| 512 | 3 | 32 | Y | +0.032 | +0.019 | 0.534 | 0.505 |
| 512 | 5 | 8 | n | +0.001 | -0.005 | 0.502 | 0.509 |
| 512 | 5 | 8 | Y | -0.000 | +0.007 | 0.504 | 0.507 |
| 512 | 5 | 16 | n | +0.006 | -0.007 | 0.506 | 0.507 |
| 512 | 5 | 16 | Y | +0.003 | +0.015 | 0.510 | 0.514 |
| 512 | 5 | 32 | n | +0.029 | -0.012 | 0.522 | 0.493 |
| 512 | 5 | 32 | Y | +0.021 | +0.023 | 0.528 | 0.505 |

Best both-slice cell (by min-slice IC): W=512 K=3 H=16 trend=True (min IC +0.023)

Trading the best cell (hold 16 bars, one position, cost 0.2/side):
  thr=0.00ATR 2025: n=1252  wr= 50.5%  PF= 1.02  net=   +133.8pts  exp= +0.11  t=+0.22
  thr=0.00ATR 2026: n= 720  wr= 49.3%  PF= 0.96  net=   -436.0pts  exp= -0.61  t=-0.41
  thr=0.25ATR 2025: n=1188  wr= 50.4%  PF= 0.99  net=    -36.5pts  exp= -0.03  t=-0.06
  thr=0.25ATR 2026: n= 678  wr= 50.4%  PF= 0.96  net=   -331.4pts  exp= -0.49  t=-0.32
  thr=0.50ATR 2025: n=1083  wr= 49.9%  PF= 0.91  net=   -625.7pts  exp= -0.58  t=-1.06
  thr=0.50ATR 2026: n= 611  wr= 49.1%  PF= 0.94  net=   -485.7pts  exp= -0.79  t=-0.50

### B. Dominant-cycle phase — 15m (W=256, band 16-128 bars)
Power-fraction distribution: median 0.225, p75 0.328, p90 0.442
Entry: cyc at trough (cos<−0.8, turning up) → BUY / peak → SELL; hold half the dominant period. Gate on power fraction q.

  gate power_frac ≥ 0.00: 505 trades
    2025: n= 329  wr= 51.7%  PF= 0.89  net=   -452.9pts  exp= -1.38  t=-0.80
    2026: n= 176  wr= 42.6%  PF= 0.79  net=   -974.5pts  exp= -5.54  t=-1.04

  gate power_frac ≥ 0.15: 367 trades
    2025: n= 250  wr= 52.8%  PF= 0.93  net=   -223.5pts  exp= -0.89  t=-0.43
    2026: n= 117  wr= 45.3%  PF= 0.65  net=  -1170.9pts  exp=-10.01  t=-1.62

  gate power_frac ≥ 0.25: 238 trades
    2025: n= 164  wr= 47.6%  PF= 0.70  net=   -664.6pts  exp= -4.05  t=-1.59
    2026: n=  74  wr= 41.9%  PF= 0.80  net=   -343.0pts  exp= -4.63  t=-0.68

  gate power_frac ≥ 0.35: 132 trades
    2025: n=  86  wr= 46.5%  PF= 0.70  net=   -407.1pts  exp= -4.73  t=-1.20
    2026: n=  46  wr= 45.7%  PF= 1.06  net=    +57.5pts  exp= +1.25  t=+0.13

### C. Spectral shape vs future trendiness — 15m (W=256, fwd=32 bars)
| feature | Spearman vs fwd ER 2025 | 2026 |
|---------|------------------------|------|
| spectral entropy | +0.003 | +0.006 |
| low-freq power frac | +0.005 | -0.001 |
| (anchor: past ER) | -0.011 | -0.007 |


## Timeframe 1H — 8526 bars

### A. Fourier extrapolation — 1H
IC = Spearman(predicted H-bar move, realized H-bar move), per slice.

| W | K | H | trend | IC 2025 | IC 2026 | sign-hit 2025 | sign-hit 2026 |
|---|---|---|-------|---------|---------|---------------|---------------|
| 128 | 3 | 8 | n | +0.038 | -0.014 | 0.524 | 0.502 |
| 128 | 3 | 8 | Y | +0.024 | +0.026 | 0.533 | 0.510 |
| 128 | 3 | 16 | n | +0.065 | -0.039 | 0.515 | 0.478 |
| 128 | 3 | 16 | Y | +0.009 | +0.001 | 0.500 | 0.494 |
| 128 | 3 | 32 | n | +0.030 | +0.006 | 0.488 | 0.498 |
| 128 | 3 | 32 | Y | -0.043 | +0.069 | 0.496 | 0.549 |
| 128 | 5 | 8 | n | +0.021 | -0.010 | 0.521 | 0.488 |
| 128 | 5 | 8 | Y | +0.014 | +0.026 | 0.526 | 0.511 |
| 128 | 5 | 16 | n | +0.049 | -0.063 | 0.511 | 0.467 |
| 128 | 5 | 16 | Y | +0.012 | -0.017 | 0.524 | 0.485 |
| 128 | 5 | 32 | n | +0.039 | -0.002 | 0.497 | 0.491 |
| 128 | 5 | 32 | Y | -0.026 | +0.053 | 0.503 | 0.529 |
| 256 | 3 | 8 | n | -0.016 | -0.014 | 0.498 | 0.492 |
| 256 | 3 | 8 | Y | -0.013 | -0.003 | 0.510 | 0.499 |
| 256 | 3 | 16 | n | +0.002 | -0.021 | 0.508 | 0.495 |
| 256 | 3 | 16 | Y | -0.005 | -0.024 | 0.522 | 0.485 |
| 256 | 3 | 32 | n | +0.030 | +0.037 | 0.510 | 0.508 |
| 256 | 3 | 32 | Y | +0.010 | +0.035 | 0.525 | 0.531 |
| 256 | 5 | 8 | n | -0.013 | -0.028 | 0.491 | 0.484 |
| 256 | 5 | 8 | Y | -0.013 | -0.019 | 0.507 | 0.496 |
| 256 | 5 | 16 | n | -0.012 | +0.006 | 0.488 | 0.508 |
| 256 | 5 | 16 | Y | -0.019 | +0.003 | 0.510 | 0.500 |
| 256 | 5 | 32 | n | +0.015 | +0.071 | 0.502 | 0.537 |
| 256 | 5 | 32 | Y | +0.004 | +0.063 | 0.519 | 0.541 |
| 512 | 3 | 8 | n | -0.010 | +0.033 | 0.499 | 0.498 |
| 512 | 3 | 8 | Y | +0.012 | +0.031 | 0.519 | 0.512 |
| 512 | 3 | 16 | n | -0.001 | +0.041 | 0.510 | 0.494 |
| 512 | 3 | 16 | Y | +0.025 | +0.041 | 0.524 | 0.512 |
| 512 | 3 | 32 | n | +0.017 | +0.085 | 0.507 | 0.513 |
| 512 | 3 | 32 | Y | +0.043 | +0.096 | 0.521 | 0.527 |
| 512 | 5 | 8 | n | +0.007 | +0.019 | 0.491 | 0.497 |
| 512 | 5 | 8 | Y | +0.020 | +0.019 | 0.508 | 0.505 |
| 512 | 5 | 16 | n | +0.005 | +0.042 | 0.489 | 0.502 |
| 512 | 5 | 16 | Y | +0.022 | +0.041 | 0.513 | 0.507 |
| 512 | 5 | 32 | n | +0.025 | +0.132 | 0.488 | 0.548 |
| 512 | 5 | 32 | Y | +0.044 | +0.136 | 0.519 | 0.550 |

Best both-slice cell (by min-slice IC): W=512 K=5 H=32 trend=True (min IC +0.044)

Trading the best cell (hold 32 bars, one position, cost 0.2/side):
  thr=0.00ATR 2025: n= 150  wr= 50.0%  PF= 1.13  net=   +335.7pts  exp= +2.24  t=+0.55
  thr=0.00ATR 2026: n=  92  wr= 55.4%  PF= 1.15  net=   +573.8pts  exp= +6.24  t=+0.47
  thr=0.25ATR 2025: n= 149  wr= 46.3%  PF= 0.92  net=   -240.1pts  exp= -1.61  t=-0.37
  thr=0.25ATR 2026: n=  91  wr= 59.3%  PF= 1.38  net=  +1274.0pts  exp=+14.00  t=+1.04
  thr=0.50ATR 2025: n= 146  wr= 53.4%  PF= 1.41  net=   +944.9pts  exp= +6.47  t=+1.55
  thr=0.50ATR 2026: n=  89  wr= 55.1%  PF= 1.13  net=   +426.7pts  exp= +4.79  t=+0.41

### B. Dominant-cycle phase — 1H (W=256, band 16-128 bars)
Power-fraction distribution: median 0.234, p75 0.346, p90 0.475
Entry: cyc at trough (cos<−0.8, turning up) → BUY / peak → SELL; hold half the dominant period. Gate on power fraction q.

  gate power_frac ≥ 0.00: 118 trades
    2025: n=  78  wr= 47.4%  PF= 0.80  net=   -443.1pts  exp= -5.68  t=-0.73
    2026: n=  40  wr= 45.0%  PF= 1.40  net=   +600.9pts  exp=+15.02  t=+0.73

  gate power_frac ≥ 0.15: 91 trades
    2025: n=  60  wr= 48.3%  PF= 0.84  net=   -259.1pts  exp= -4.32  t=-0.49
    2026: n=  31  wr= 51.6%  PF= 1.52  net=   +608.5pts  exp=+19.63  t=+0.86

  gate power_frac ≥ 0.25: 57 trades
    2025: n=  36  wr= 41.7%  PF= 0.76  net=   -277.8pts  exp= -7.72  t=-0.62
    2026: n=  21  wr= 52.4%  PF= 1.42  net=   +350.1pts  exp=+16.67  t=+0.61

  gate power_frac ≥ 0.35: 30 trades
    2025: n=  21  wr= 38.1%  PF= 0.95  net=    -32.3pts  exp= -1.54  t=-0.09
    2026: n=   9  wr= 22.2%  PF= 0.87  net=    -63.0pts  exp= -7.00  t=-0.14

### C. Spectral shape vs future trendiness — 1H (W=256, fwd=32 bars)
| feature | Spearman vs fwd ER 2025 | 2026 |
|---------|------------------------|------|
| spectral entropy | -0.092 | -0.072 |
| low-freq power frac | +0.014 | +0.027 |
| (anchor: past ER) | -0.044 | -0.077 |

---

## Ablation of the best 1H cell (W=512, K=5, H=32)

The only cell that looked alive bundles a linear trend term with the harmonics, so
the Fourier contribution was isolated:

| predictor | IC 2025 | IC 2026 | t_eff 2025 | t_eff 2026 | PF 2025 | PF 2026 |
|-----------|---------|---------|-----------|-----------|---------|---------|
| slope-only (no Fourier) | −0.007 | +0.019 | −0.08 | +0.18 | 1.25 | 1.06 |
| cycles-only (Fourier)   | +0.025 | +0.132 | +0.31 | +1.27 | **0.94** | 1.76 |
| cycles+trend            | +0.044 | +0.136 | +0.54 | +1.31 | — | — |

(t_eff = overlap-corrected: effective n = n/H since H-bar forecasts overlap.)

The Fourier part's apparent 2026 IC (+0.132) is (a) not significant once forecast
overlap is corrected (t ≈ 1.3), (b) **negative-PF in 2025** (0.94), and (c) the best
of 36 searched cells — pure selection bias on one hot slice. The 2026 "cycle" is the
gold round-trip swing that a 3-month window happened to phase-lock onto.

## VERDICT — NOT SHIPPED (no edge)

1. **A. Fourier extrapolation ("predict the structure"): DEAD.** 15m ICs are ±0.03
   noise, sign-hit ~50%, all trading cells PF 0.91–1.02. 1H's one hot cell fails the
   both-years gate after ablation (2025 PF 0.94). Root cause is structural, not
   parametric: the FFT assumes the signal is stationary and periodic over the window;
   price is a near-martingale with a 1/f² random-walk spectrum, so the "dominant
   cycles" are re-fit noise whose phase does not persist out of window.
2. **B. Dominant-cycle phase trading (Ehlers-style): DEAD.** 15m PF 0.65–1.06 across
   all power-fraction gates (mostly < 0.9); 1H negative in 2025 at every gate. The
   power-fraction gate does not rescue it — high power fraction just means the window
   fit one big swing, not that the swing repeats.
3. **C. Spectral shape as regime feature: the only consistent (weak) signal.**
   1H spectral entropy vs forward efficiency-ratio: ρ = −0.09/−0.07, same sign both
   slices, and it beats the naive past-trendiness anchor — but |ρ| < 0.1 is far too
   weak to gate anything on its own. 15m: zero. Not worth wiring.

Do NOT re-research: rolling-FFT extrapolation, top-K harmonic forecasting,
dominant-cycle trough/peak phase entries on gold intraday. If spectral ideas ever
return, the only door left ajar is entropy-style regime features as one input among
several — never a standalone signal.
