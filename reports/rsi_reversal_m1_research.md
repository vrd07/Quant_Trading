# Cardwell RSI Positive/Negative Reversal on M1 XAUUSD (2026-07-29)

> **UPDATE — session-window variant FAILED true out-of-sample. See the final
> section. Nothing here is shippable on M1.**


**Verdict: NOT SHIPPABLE on M1. The pattern is real but ~6× too small for M1 costs.**
**The pattern beats its own control — it deserves a 15m retest, not the bin.**

Script: `scripts/research_rsi_reversal_m1.py`

## Spec as implemented

| | |
|---|---|
| Trend | EMA9 strictly above/below EMA21 |
| Setup (long) | price higher-low + RSI lower-low, RSI ≥ 40 |
| Setup (short) | price lower-high + RSI higher-high, RSI ≤ 60 |
| Trigger | green candle breaking prior high / red breaking prior low |
| Stop | recent M1 swing low/high (or EMA21), 0.10 buffer |
| Target | fixed 1:1.5 or 1:2 |
| Time stop | 4–5 candles |

No lookahead: pivots confirm k bars late and are only usable once confirmed;
entry fills on the first tick AFTER the trigger candle closes; exits resolve on
the real tick stream so SL-vs-TP ordering inside a candle is decided by actual
sequence, and gaps past the stop fill at the gapped price. 56 days of Dukascopy
ticks, 2026-05-01..2026-07-19.

## Base spec result (RR 1:2, 5-candle time stop)

| metric | value |
|---|---|
| Trades | 1,270 (**22.7/day**) |
| Net | **−$849.49** @ 0.01 lot |
| PF | 0.555 |
| Win rate | 33.7% |
| Total R | −339R (avg −0.267R) |
| Profitable days | **9 / 56** |

**The spec is internally inconsistent.** Exit breakdown:

| exit | n | share | avg R |
|---|---|---|---|
| time stop | 698 | 55% | **+0.087** |
| stop loss | 498 | 39% | −1.103 |
| take profit | 74 | **5.8%** | +2.021 |

Average risk is 3.13 pts, so 1:2 needs a **6.26-pt move inside 5 minutes** while
the median M1 range is 1.755. The target is a ~p99 event on the given horizon —
only 5.8% of trades ever reach it, while 39% take a full stop. Time exits being
*positive* (+0.087R) says the entry is not the problem; the geometry is.

## Grid: time-stop × RR × pivot_k × SL-mode (72 cells, IS/OOS split)

Relaxing the time stop confirms the diagnosis — PF rises monotonically:

| time stop | PF |
|---|---|
| 5 bars | 0.555 |
| 10 | 0.628 |
| 20 | 0.711 |
| 30 | 0.722 |
| 45 | 0.728 |
| 60 | **0.753** |

**0 / 72 cells profitable. Best PF 0.753. 0 cells PF>1 in both IS and OOS.**
Best cell (k2 / swing / RR1.5 / 60-bar) = PF 0.753, IS 0.740 / OOS 0.766 —
consistent across halves, consistently losing. `swing` stops beat `ema` stops
throughout; pivot_k 2 beats 3.

## Does the RSI reversal actually predict anything?

Forward move after signal, signed into trade direction, decomposed against its
own controls (FULL = trend+RSI reversal+trigger, TRIG = trend+trigger only,
TREND = trend only):

| bars | FULL | TRIG | TREND | FULL − TRIG |
|---|---|---|---|---|
| 1 | −0.038 | −0.054 | −0.010 | +0.015 |
| 3 | −0.008 | −0.072 | −0.020 | +0.064 |
| 5 | −0.020 | −0.110 | −0.025 | +0.090 |
| 10 | −0.080 | −0.104 | −0.025 | +0.024 |
| 20 | **+0.125** | −0.045 | +0.037 | **+0.170** |
| 30 | +0.075 | −0.033 | −0.009 | +0.108 |
| 60 | −0.243 | −0.106 | −0.074 | −0.137 |

**The RSI reversal condition genuinely adds value — `FULL − TRIG` is positive at
6 of 7 horizons, up to +0.170.** Cardwell's pattern is doing real work: it filters
out a large amount of junk.

The problem is what it is filtering. **The naive trigger is actively harmful**
(TRIG −0.045 to −0.110 at every horizon) — buying a green M1 candle that breaks
the prior high in an uptrend is buying the top of noise. The RSI condition lifts
that strongly negative base up to roughly *zero*, not to positive.

Best FULL reading is **+0.125 at 20 bars, against a ~0.73 round-trip cost — 6×
short.** Day-level consistency is a coin flip (23–29 of 56 days positive at any
horizon), so there is no reliable directional effect to harvest at M1.

## On "a trade every minute"

The pattern fires 22.7×/day at its loosest (1,260 M1 bars/day). Forcing ~1,260
trades/day would mean dropping every structural condition and paying 0.69 spread
~1,260 times — roughly **−$870/day in pure spread at 0.01 lot** before any
directional error. Frequency is the tax here, not the edge: across the whole grid,
PF falls monotonically as trades/day rises (24.0/day → PF 0.474; 9.1/day → 0.691).

## The constructive read

This is not a dead idea, it is a **timeframe mismatch**. The cost is fixed at 0.69
regardless of timeframe; only the move scales. On M1 the swing stop is 3.13 pts so
cost is ~23% of risk. On 15m the swing stop is ~5–6 pts, putting cost near 12% of
risk — and the pattern's best signal already lives at a 20–30 bar horizon, which is
where a higher timeframe naturally operates.

**Recommended next test:** the identical rules on 15m bars, where
`XAUUSD_5m_real.csv` gives years of history rather than 56 days. Same script,
resampled bars. Do NOT re-test on M1 — the forward-return probe rules that out
independently of exit tuning.

---

# Session-window variant — FALSE POSITIVE, caught by a true holdout

Restricting the same rules to the hours where gold's M1 move is largest relative
to spread produced the only positive result of the session — and it did not
survive fresh data. Recorded in full because the failure mode is the useful part.

## What it looked like on the selection sample (May–Jul 2026, 56 days)

| session | PF | IS | OOS |
|---|---|---|---|
| all day | 0.753 | 0.740 | 0.766 |
| 12–16 UTC | 1.222 | 1.300 | 1.146 |
| **13–16 UTC** | **1.668** | **1.648** | **1.690** |

Chosen cell (pivot_k 2 / swing SL / RR 1.5 / 60-bar time stop): 80 trades,
+$106.09, win 51.2%, **+0.244R per trade**, both directions positive. It also
passed two checks that normally inspire confidence:

- **Plateau, not a spike** — all 12 cells in the time-stop × RR neighbourhood were
  profitable in *both* halves (PF 1.20–1.76).
- **Cost-robust** — PF still 1.62 at 10× the modelled slippage.

## What fresh data said

Fetched Jan–Apr 2026 ticks *after* the strategy was fixed — genuinely unseen.

| period | days | trades | PF | win rate | net |
|---|---|---|---|---|---|
| May–Jul 2026 (selection) | 56 | 80 | 1.668 | 51.2% | +$106.09 |
| **Jan–Apr 2026 (holdout)** | **68** | **77** | **0.446** | **22.1%** | **−$143.03** |
| Full Jan–Jul 2026 | 126 | 160 | 0.953 | 38.1% | −$19.59 (**−0.089R/trade**) |

Over the full 7 months the edge is **negative** (−0.089R/trade, −14.2R total).
Profitable days on the holdout: 14/68. Win rate collapses from 51.2% to 22.1%
on data that played no part in selection.

And on the holdout **no session window works at all**:

| window | PF (Jan–Apr) |
|---|---|
| 12–16 | 0.584 |
| 13–16 | 0.457 |
| 13–14 | 0.441 |
| 14–17 | 0.770 |
| 07–12 | 0.830 |
| all day | 0.690 |

So 13–16 UTC was never a structural property of gold. It was the best-looking
slice of one 56-day sample.

## Why the IS/OOS split failed to catch it

**The session window was selected using all 56 days, then "validated" on an
IS/OOS split of those same 56 days.** The selection decision contaminated both
halves simultaneously, so no split of that sample could detect it. The 12/12
parameter plateau was computed on the same contaminated sample — **a plateau
measured on contaminated data is still contaminated**, and it produced false
confidence rather than catching the problem.

The high movement-to-spread ratio at 13:00 UTC is real (it is a property of the
data). What does not exist is a *directional* edge there. Large moves make being
right pay more; they do not make you right.

## Rules this establishes

1. **Selecting a filter on a sample poisons every split of that sample.** Any
   hyperparameter chosen by looking at the data — session, symbol, side, regime —
   must be fixed BEFORE the validation period exists, or validated on data fetched
   afterwards.
2. **A parameter plateau is not evidence of robustness** unless it is measured on
   data that played no part in the selection.
3. With ~1 trade/day, 56 days is ~80 trades — far too few to distinguish PF 1.67
   from noise. Demand a holdout of comparable size before believing any M1 result.

**Verdict: no shippable M1 gold scalper. The tested rules do not have a
directional edge on 1-minute gold in any session.**
