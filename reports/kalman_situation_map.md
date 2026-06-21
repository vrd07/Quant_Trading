# Kalman v2 — Gold Situation → Risk-Action Map (defensive)

**Generated:** 2026-06-21 · **Script:** `scripts/research_kalman_situation_map.py` · tape: `kalman_50k_2026_trades.csv` (608 trades, in-sample 2026)

> The binding constraint is the **drawdown path**, not expectancy (per the $50k autopsy). This is a *defensive* layer — it decides WHEN to stand aside and how much to SIZE in hostile gold situations. It does **not** add entry alpha, and the underlying entry is OOS-dead. Goal: survivability.

## Drawdown / PF attribution by a-priori situation

Each situation is knowable at decision time (no P&L mining). This shows WHERE the bleed concentrates.

### MODE (trend vs OU-range)

| bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| trend | 482 | 54.8% | 1.16 | +4,918 |
| range | 126 | 46.0% | 0.83 | -1,565 |

### VOLATILITY regime — ATR(14) quartile

| bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| Q1 calm | 38 | 44.7% | 0.75 | -754 |
| Q2 | 111 | 45.9% | 0.82 | -1,469 |
| Q3 | 139 | 60.4% | 1.43 | +3,358 |
| Q4 spike | 320 | 53.1% | 1.11 | +2,217 |

### HTF TREND alignment (1h EMA-50)

| bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| True | 485 | 55.3% | 1.19 | +5,578 |
| False | 123 | 43.9% | 0.76 | -2,226 |

### SESSION (UTC hour)

| bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| Asia 00-06 | 166 | 54.8% | 1.18 | +1,814 |
| London 07-11 | 120 | 52.5% | 1.07 | +514 |
| NY 12-16 | 134 | 56.0% | 1.25 | +1,963 |
| Late 17-23 | 188 | 49.5% | 0.93 | -938 |

### WEEKEND risk (Fri + long hold)

| bucket | N | Win% | PF | Net$ |
|---|---:|---:|---:|---:|
| False | 585 | 54.0% | 1.14 | +4,964 |
| True | 23 | 26.1% | 0.33 | -1,612 |

**Reading it — and a refuted assumption.** The bleed concentrates in three buckets with clear a-priori rationale: the OU **range** mode, trades **fighting the HTF trend**, and **weekend holds** (Fri + long hold, WR 26%). **But the volatility assumption was WRONG:** I expected the top-ATR 'vol-spike' quartile to be the risk; the data shows the opposite — Kalman *profits* in high vol (Q4 PF 1.11) and *loses in calm chop* (Q1 0.75, Q2 0.82). So the vol lever is dropped from the recommended map. It is deliberately NOT flipped to down-size calm instead — flipping a rule after seeing the result is the overfitting trap.

## The defensive size map (a-priori, conservative)

Multiplicative, applied to the real tape (pnl scales linearly with lot). **V1** = naive a-priori (with the vol guess); **V2** = refined, vol lever dropped:

| Situation | V1 | V2 (recommended) | Live wiring |
|---|---|---|---|
| RANGE / OU mode | × 0.5 | × 0.5 | down-weight/skip range-mode signals |
| Fighting HTF 1h-EMA(50) | × 0.5 | × 0.5 | **symmetric BUY-side trend gate** (the gap) |
| Top-quartile ATR | × 0.5 | — (dropped) | — |
| Friday held over weekend | × 0.5 | × 0.5 | no new Fri entries that can't close by EOD |

| Run | PF | Net$ | Max DD% | Max DD$ | Avg size |
|---|---:|---:|---:|---:|---:|
| Baseline (flat size) | 1.09 | +3,353 | -6.7% | -3,647 | 1.00× |
| V1 (incl. vol lever) | 1.20 | +4,306 | -3.3% | -1,736 | 0.59× |
| **V2 (recommended)** | 1.18 | +5,514 | -4.6% | -2,462 | 0.83× |

- **V2: drawdown +32%, return retained 164%, at 0.83× average size.** Cutting only the genuinely-dead buckets shrinks the drawdown by far more than it costs in return (return actually *rises* when the down-sized buckets were net-negative) and lifts PF — the clean signature of removing dead weight, not edge.

## What's already live vs the gap

| Situation | Already handled live? |
|---|---|
| High-impact news | ✅ news blackout suppresses signals |
| Regime (trend/range/volatile) | ✅ nightly + intraday regime classifier reweights |
| Illiquid session | ✅ kalman session mask `[[3,4],[20,23]]` |
| HTF trend (SELL side) | ✅ 1h-EMA(50) gate on shorts |
| **HTF trend (BUY side)** | ❌ gap — BUY can still fight the trend |
| **Vol-spike down-sizing** | ❌ gap — fixed budget regardless of ATR regime |
| **Drawdown-state de-risking** | ⚠️ only a hard kill switch; no graded taper |

## Verdict / next step

The defensive map is a real **survivability** lever (shrinks DD, raises PF), and the three gaps above are the honest places to wire it: a symmetric BUY-side trend gate, ATR-regime size taper, and a graded drawdown de-risk before the hard halt. But round-number multipliers fit on 2026 must be **walk-forward validated** before live, and none of this revives an OOS-dead entry — it only makes whatever deploys bleed slower. Pairs with the portfolio finding: smoothness comes from diversification first, defensive sizing second.
