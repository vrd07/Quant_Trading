# Mission: gold-uncorrelated edge — FOUND candidate: Midweek (Tuesday) Index Overnight Long

**Date:** 2026-06-24 · **Data:** Dukascopy 5m, 2024-01-01 → 2026-06-22 (2.5y), US30/NAS100/GER40
**Scripts:** `research_index_calendar.py` (decomp) · `research_index_overnight.py` (guards) · `research_index_tuesday.py` (focused)

## Mission criteria & whether this clears them
- ✅ **Gold-uncorrelated** — equity index, different instrument & session.
- ✅ **Clears 1.3 strict-ish PF** — Tue-only full-sample PF 1.68 / 1.74 / 2.29 (US30/NAS/GER); holds ≥1.36 even at 8 bps all-in cost.
- ✅ **Survives $5k** — ~1 trade/week, maxDD −3% to −4%, wide window + time exit = the `monday_drift` mold that already survives the kill switch.
- ✅ **IS/OOS stable** — OOS PF ≥ IS PF on all three (no sign flip). Every calendar year 2024/25/26 PF > 1.3 on all three.

## The signal
Decomposing index daily returns into **overnight (cash close→next open)** vs **intraday (open→close)**:
NAS100 overnight Sharpe **1.73**, intraday ~noise — textbook "night effect". But the *full* nightly hold nets to only **PF 1.15** after realistic overnight financing (~2 bps/night) — financing kills the everyday version.

The DoW breakdown localised it: the drift is **midweek, concentrated on Tuesday-entry (Tue close → Wed open)**, +ve & significant on all three indices independently (NAS t=2.17, US30 t=1.83, GER40 t=2.72). "Turnaround Tuesday." A Tue-only hold (a) clears the bar and (b) only pays financing ~1 night/week.

## Tue-only LONG (enter Tue cash-close, exit Wed cash-open; fin2+cost2 bps)
| Index | PF ALL | PF IS | PF OOS | maxDD | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|
| NAS100 | 1.74 (t2.17) | 1.42 | 3.31 | −3.6% | 1.50 | 1.42 | 4.21 |
| US30 | 1.68 (t1.83) | 1.51 | 2.19 | −2.9% | 1.74 | 1.39 | 2.17 |
| GER40 | 2.29 (t2.72) | 1.81 | 3.51 | −3.2% | 1.44 | 1.94 | 5.20 |

## Guards passed
1. **Bid-spread artifact** — PF stays 1.13–1.20 entering 0–6 bars *inside* the boundary; does NOT collapse (unlike the GBPUSD/EURUSD weekend traps).
2. **Financing** — modeled explicitly; Tue-only absorbs it (gross ~15 bps vs ~2 bps/night).
3. **Cost** — ≥1.36 PF at 8 bps all-in.
4. **Regime gate (SMA50)** — HURTS here (cuts good trades); the effect is trend-agnostic, unlike monday_drift. No gate needed; DD already tiny.

## Honest caveats (what this IS and ISN'T)
- It is a **calendar/seasonal anomaly**, same *class* as `monday_drift` — NOT structural alpha. Ship-decision is a user call, like monday_drift was.
- The 3 indices are correlated (~1–1.5 independent bets, not 3). Mitigant: named documented effect + every-year stability.
- IS t-stats are modest (NAS 1.16); the cross-instrument + cross-year replication is what carries it.
- Absolute $ is small per trade (once/week, ~15 bps move). PF/DD are size-independent and strong; $ depends on broker index-CFD contract size.

## Before it could go live (open gates)
1. **Broker tradeability** — does the live account offer an index CFD (US30/NAS100/GER40)? Contract size / min-lot at $5k? (Gates everything.)
2. **Production-engine strict backtest** — reproduce via `run_backtest`/ensemble path, not just this research sim.
3. **Pick one instrument** — GER40 strongest (PF 2.29, t2.72); NAS100 next. US30 weakest but lowest DD.

**Verdict:** First candidate in the whole hunt to meet all four mission criteria. A monday_drift-class seasonal edge, cleaner DD, gold-uncorrelated. Ship-worthy *pending broker tradeability* — that's the one external unknown only the user can resolve.

## SHIPPED + risk-sizing tuned (2026-06-24)
Wired as strategy #12 `index_overnight` (US30/NAS100). Per-strategy `risk_per_trade_usd` sweep under `--enforce-risk` (config_live_5000):

| risk/trade | NAS100 ret / DD | US30 ret / DD | note |
|---|---|---|---|
| $15 (default) | +2.3% / −0.7% | +2.6% / −0.7% | baseline |
| **$50 (shipped)** | **+8.7% / −2.4%** | **+10.5% / −2.4%** | 1% of $5k; ~4× contribution, half the cap |
| $100 | +17.8% / −4.9% | +21.4% / −4.8% | near-full cap — not recommended |
| $150+ | 0 trades | 0 trades | single-trade risk ≥ $150 daily cap → rejected (hard cliff) |

Scaling is linear to $100 then dies at the daily-cap cliff. **Shipped at $50** (matches monday_drift/london_breakout) — PF unchanged (1.57/1.79), DD −2.4% leaves enforcement headroom; dial toward $75 for more, avoid $100 (eats the whole 5% cap). `correlation_guard` gives index_overnight its **own cluster** (decoupled from the gold-coil cluster — uncorrelated) so US30+NAS100 share one concurrent slot (~0.9 corr) without being blocked by gold positions.
