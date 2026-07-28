# XAUUSD Tick Scalper — research result (2026-07-28)

**Verdict: NO EDGE. Not shippable at any retail cost structure.**

Artifacts:
- `mt5_bridge/EA_GoldTickScalper.mq5` — the EA (compiles/attaches to a gold chart, symbol-guarded)
- `scripts/backtest_gold_tick_scalper.py` — tick-replay backtester mirroring the EA state machine

## Spec implemented

Tick-driven micro-reversion on XAUUSD: fade a 20s price burst that stalls in the
last 5s while stretched from an M1 EMA(20) anchor. One position at a time, max 3
entries per rolling 60s, 8s cooldown, spread filter, session filter, M1-ATR
scaled TP/SL floored at 1.8× live spread, time stop, daily loss cap.

## Backtest method

Real Dukascopy XAUUSD **bid/ask ticks** (~400k/day), not a bar model.
BUY fills at ask and exits at bid; SELL mirrors — every trade pays the true
observed spread. Stops trigger on the observed tick, so ticks that gap past the
level fill at the gapped price. M1 ATR/EMA read from the **last completed bar**
(no repaint, no lookahead). Plus 0.02 adverse slippage/side and $3.5/lot/side
commission.

## Result — 2026-07-17 (requested day, 0.01 lot)

| metric | value |
|---|---|
| Trades | 91 |
| Net P&L | **−$50.97** |
| Profit factor | **0.404** |
| Win rate | 36.3% |
| Avg trade | −$0.56 |
| Max drawdown | −$52.10 |
| Exits | 52 SL (−$82.41) / 30 TP (+$32.69) / 9 time (−$1.25) |

Hit the $50 daily loss cap at 11:00 UTC and stood down.

## Why it fails — three independent confirmations

**1. Both signs of the signal lose, every day.** 10 days, no exceptions:

| mode | trades | net | PF | days profitable |
|---|---|---|---|---|
| fade (mean-reversion) | 1,679 | −$1,101 | 0.439 | 0 / 10 |
| continuation | 2,559 | −$2,112 | 0.364 | 0 / 10 |

A signal and its exact inverse both losing means the signal carries no
monetizable information — this is not a parameter problem.

**2. The raw edge is ~7% of the cost.** Forward mid-move after ~150k triggers,
no TP/SL involved:

- continuation: ≈ 0, turning **negative** (−0.03 to −0.07) at 30–120s
- fade: **+0.05 to +0.10**, and `fade_long` @30s is positive **9 of 9 days**

The micro-reversion tendency is *real and stable*. It is also ~1/14th of the
0.69 spread it must clear.

**3. It never reaches breakeven even at zero cost.** Real mid path, synthetic spread:

| spread | PF | net (10d) |
|---|---|---|
| 0.69 (real) | 0.420 | −$1,219 |
| 0.40 | 0.587 | −$695 |
| 0.20 | 0.767 | −$332 |
| 0.10 | 0.877 | −$160 |
| 0.05 | 0.943 | −$71 |
| **0.02** | **0.979** | −$26 |

PF asymptotes to just under 1.0 and **never crosses**. Better-than-institutional
pricing does not rescue it. Spread is the dominant cost but not the whole story:
the residual edge is exactly consumed by TP/SL geometry and slippage.

## "Close as soon as it is in profit" — the trap, measured

Closing at the first +$0.30 of floating profit, same day (2026-07-17):

| | default TP | profit-lock +$0.30 |
|---|---|---|
| Win rate | 36.3% | **57.1%** |
| Net | −$50.97 | **−$129.97** |
| PF | 0.404 | 0.265 |

113 locked wins averaging +$0.39 against 84 stops averaging −$2.08. The rule
raises the win rate by 21 points and **more than doubles the loss**. Capping
winners while leaving losers uncapped inverts the payoff — high win rate,
strongly negative expectancy.

## Microstructure context (2026-07-17)

- Median spread **0.69**; median 20s move **0.435** — cost exceeds the typical
  short-horizon move.
- Spread / M1-ATR = 0.38.
- Movement-to-spread ratio is only favourable 12:00–16:00 UTC (peak 2.78 at 13h)
  and 06:00–08:00. Asia hours are unprofitable by construction.

## TP/SL grid — wider TP + tighter SL + 120s exit (user request, 2026-07-29)

40 geometries (TP 0.6–3.0×ATR × SL 0.40–0.90×ATR × profit-lock 0/$1), 120s max
hold, 19 days, ~80k trades, IS 2026-06-22..07-02 / OOS 07-03..07-17.

**0 / 40 cells profitable. Best PF anywhere 0.482 (OOS).** Best IS cell
(TP 1.5 / SL 0.90 / no lock) = IS PF 0.439, OOS PF 0.460 — negative both halves.

Tighter SL is monotonically **worse**, the opposite of the hypothesis:

| SL ×ATR | PF | win rate |
|---|---|---|
| 0.90 | 0.43 | 32% |
| 0.70 | 0.40 | 27% |
| 0.55 | 0.34 | 20% |
| 0.40 | 0.29 | 14% |

A stop tighter than the spread is hit on entry (buy fills at ask, stop measured on
bid, already 0.69 below) — so tightening just converts spread noise into stop-outs.
Hard floor on gold ≈ 0.375×ATR.

Widening TP is nearly inert: at SL 0.90, TP 0.6→3.0×ATR moves PF only 0.418→0.435.
This is the optional-stopping result — on a driftless series, barrier placement
redistributes win rate against win size but leaves E[PnL] = edge − cost unchanged.
Edge +0.05, cost 0.73. Hence all 40 cells cluster in 0.29–0.48 with no winners.

With a $1 profit-lock, results are **identical across all TP values** (the lock
always fires first) and PF drops to 0.36 — confirming the lock dominates the exit.

## M1 bar-mode variant (user request, 2026-07-29)

Converted from tick-granularity to a true 1-minute scalper: entries evaluated
ONCE per completed M1 bar, burst measured across the last completed bar(s),
stretch from the M1 EMA anchor. Exits still managed on every tick.

| variant | days | trades | PF | net |
|---|---|---|---|---|
| M1 fade | 10 | 1,102 | 0.423 | −$751 |
| M1 continuation | 10 | 1,054 | 0.335 | −$931 |
| **M1 fade, 13–16 UTC** | **126** | **4,825** | **0.601** | **−$3,143** |

Note: the tick-mode "stall" condition has no bar-granularity analogue — a bar that
fell against the prior close *and* closed green is near-empty once the stretch gate
is applied (it produced literally 0 trades). Bar mode is therefore burst + stretch
only.

Evaluating once per bar also caps entries at **one per minute** by construction, so
the original "3 trades per minute" requirement cannot be met in M1 mode.

Restricting to the highest movement-to-spread window (13–16 UTC) over the full
126 days still gives PF 0.601 across 4,825 trades — the session filter does not
rescue it either, matching the independent finding in
`reports/rsi_reversal_m1_research.md` that no session window survives a true
holdout.

## Target search: WR > 50% at RR 1.5 (user request, 2026-07-29)

288-cell search over fade/follow × trigger × stretch × stop width × lookback ×
session × time stop, RR **fixed at 1.5**. Holdout declared BEFORE searching:
search = Jan 07–Apr 30 (84 days), holdout = May 01–Jul 19 (67 days).

**0 / 288 cells reached WR > 50%. 0 / 288 reached PF > 1.0.**
Best win rate anywhere **36.25%** — below the 40% that merely breaks even at RR 1.5.
Best PF anywhere 0.750. **The holdout was never spent: nothing qualified.**

### Why the win-rate target cannot be bought

Win rate is set by the RR you choose, not by skill. Best entry config, RR swept:

| RR | actual win% | breakeven win% | shortfall | PF |
|---|---|---|---|---|
| 0.25 | 56.8 | 80.0 | −23.2 | 0.532 |
| 0.50 | 51.9 | 66.7 | −14.8 | 0.594 |
| 0.75 | 45.5 | 57.1 | −11.6 | 0.662 |
| 1.00 | 39.5 | 50.0 | −10.5 | 0.696 |
| 1.50 | 33.3 | 40.0 | −6.7 | 0.750 |
| 2.00 | 28.4 | 33.3 | −4.9 | 0.732 |
| 3.00 | 25.7 | 25.0 | +0.7 | 0.726 |

WR > 50% **is** reachable — at RR 0.25 (56.8%) or RR 0.50 (51.9%). But breakeven at
those RRs is 80% and 66.7%, so the shortfall *widens*: the strategy is furthest from
profitable exactly where the win rate looks best. A high win rate bought with a tight
target is a presentation choice, not an edge.

Note the shortfall shrinks monotonically as RR rises (−23.2 → +0.7) because the fixed
0.73 round-trip cost is a smaller fraction of a wider target. At RR 3.0 the raw win
rate finally clears the cost-free breakeven — and PF is still 0.726, because the
breakeven formula ignores costs.

### "Big lot" is a multiplier, not an improvement

Best cell, RR 1.5, −$14.11/day at 0.01 lot over 72 days:

| lot | $/day | $/month (21d) |
|---|---|---|
| 0.01 | −14.11 | −296 |
| 0.10 | −141.11 | −2,963 |
| 0.50 | −705.54 | −14,816 |
| 1.00 | −1,411.07 | −29,633 |

Lot size scales P&L linearly and leaves win rate and PF untouched. Applied to a
negative expectancy it scales the loss.

## Final exhaustive search — all-day + all RRs (2026-07-29)

480 cells: session {all-day, 12-16} × fade/follow × trigger {0.30,0.55,0.90} ×
stretch {0,0.35} × stop {0.4,0.6,0.9,1.3}×ATR × RR {0.5,1.0,1.5,2.0,3.0}.
Search 72 days (Jan 07–Apr 30), holdout 56 days (May 01–Jul 19) spent once.

**0 / 480 cells reached PF > 1.0.**
**All-day subset: 0 / 240, best PF 0.703, best WR 51.7%.**
Best overall 0.802 (12-16 UTC, stop 1.3×ATR, RR 2.0).

### The holdout agrees — this is measurement, not overfitting

| config | search PF | holdout PF | holdout net |
|---|---|---|---|
| 12-16, sl1.3, RR2.0 | 0.802 | 0.779 | −$663 |
| 12-16, sl1.3, RR1.5 | 0.787 | 0.807 | −$599 |
| 12-16, sl1.3, RR1.0 | 0.784 | 0.770 | −$709 |
| 12-16, sl1.3, RR3.0 | 0.801 | 0.767 | −$1,147 |

Search and holdout PF match within ~0.03. Contrast with the RSI session window
(1.668 → 0.446), which was overfitting. Here there is nothing to overfit: the
strategy reproduces its loss rate exactly on unseen data. **True performance of
this family is PF ≈ 0.78**, stable across 151 days.

Wider stops (1.3×ATR) and RR 1.5–2.0 dominate the top of the table — the opposite
of the tight-stop/low-RR direction that maximises win rate. Cost is a smaller
fraction of a wider target, so the loss shrinks; it never reverses.

## Do not re-research

Tick-burst fade/continuation on gold with M1-ATR targets, in any of: either sign,
profit-lock exits, ATR-scaled or spread-floored targets, session-restricted. The
zero-cost limit test (PF 0.979 at 0.02 spread) rules out the whole family — no
exit tuning or cost improvement reaches PF 1.0.

Real HFT works here only by *earning* the spread (passive quoting) rather than
paying it. That is a market-making problem requiring co-location and queue
priority, not an EA on a retail MT5 bridge.
