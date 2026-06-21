# Kalman v2 — Beta vs Alpha Test (demean the 2026 drift)

**Generated:** 2026-06-21 · **Script:** `scripts/research_kalman_detrend.py` · **Signals:** real `KalmanRegimeStrategy.on_bar()` (v2), XAUUSD 15m, 2026 YTD
**Params (mirror the $50k report):** SL 33 / RR 1.0 / lot 0.04 / cost 0.2/side / daily cap $295 / $50,000 acct. PF is size-invariant, so the verdict is lot-independent.

> **Question:** SELL made the money in a year gold supposedly *fell*. Is that signal alpha, or just directional beta (short gold in a down year)?

## First correction: 2026 was not a 'down year' — it was a round-trip

- Net move over the slice is essentially **FLAT: 4331 → 4351** (+20 pts, **+0.5%**). The report's 'short gold in a down year' framing is imprecise.
- The PATH, however, is violent: gold spiked to **5586** (2026-01-29, **+29%**), then fell to **4053** (2026-06-10, **-27%**), then bounced to 4351. The dominant Feb–Jun leg was DOWN — that is the multi-week drift SELL is suspected of merely riding.
- Because the *net* drift ≈ 0 (+0.0019 pts/bar), a single full-period demean would be a no-op — the confound is the **local** trend, which is what both tests below remove.

## The drift being removed (TEST 1)

- After the 20-day detrend: close **4331 → 4295** (-36 pts residual), local trend flattened, every bar's range/ATR preserved (equal O/H/L/C offset) so the fixed 33-pt stop stays a fair comparison.

## TEST 1 — Drift-suppressed replay

Re-ran the real strategy on the driftless series and re-simulated identically.

| Run | N | PF (cap $295) | Net$ | raw PF (no cap) | MaxDD% |
|---|---:|---:|---:|---:|---:|
| **Baseline (real prices)** | 608 | 1.09 | +3,353 | 1.20 | -6.7% |
| **Detrended 20D (driftless)** | 605 | 1.18 | +6,766 | 1.11 | -4.7% |
| **Detrended 60D (robustness)** | 720 | — | — | 1.11 | — |

*60D removes only the slow macro drift (less signal-frequency reversion injected than 20D). Raw PF holds at 1.11 — the alpha read is not a 20D-detrend artifact.*

### By side

| Side | Baseline N | Baseline PF | Baseline Net$ | Detrended N | Detrended PF | Detrended Net$ |
|---|---:|---:|---:|---:|---:|---:|
| SELL | 241 | 1.32 | +4,458 | 253 | 1.37 | +5,320 |
| BUY | 367 | 0.96 | -1,105 | 352 | 1.06 | +1,446 |

## TEST 2 — Per-trade LOCAL-drift demean (cross-check on the actual trades)

Subtract from every real trade the P&L attributable purely to the **local 20-day drift** at its entry, over its holding duration (`beta = side · local_drift/bar · bars_held · lot · $/pt`); recompute PF on the residual `alpha_pnl`. (A *constant* full-period demean is skipped — net drift ≈ 0 makes it a no-op, as noted above.)

| Bucket | Raw PF | Alpha PF (drift removed) |
|---|---:|---:|
| All trades | 1.09 | 1.05 |
| SELL | 1.32 | 1.32 |
| BUY | 0.96 | 0.90 |

Result: PF barely moves (SELL 1.32→1.32, BUY 0.96→0.90). The reason is mechanical and itself informative — a fixed 33-pt bracket trade held a few hours captures only ~$4 of the slow 20-day drift, against a ±$132 bracket outcome. Per-trade P&L is dominated by **which bracket hits (timing)**, not by drift accumulation, so demeaning the drift can't explain it away. This is the same conclusion as TEST 1, reached from the opposite direction.

## Verdict

NOT PURE BETA — uncapped PF only decays 1.20 → 1.11 when the local drift is removed, and stays above 1.10. A residual timing alpha survives (BUY recovers from 0.96 → 1.06, the tell that it isn't merely directional). BUT it is marginal — PF ~1.1 is inside the slippage-noise band, still in-sample on one violent round-trip regime. Survivable, not durable.

### If it's mostly beta — the fix

Don't take both sides blindly. Add a **directional overlay**: only SELL when the HTF trend is down, only BUY when the HTF trend is up. Kalman v2 already has a one-sided HTF-EMA(50) gate on the SELL leg; a symmetric gate on BUY would stop it fighting the trend — but per this test that makes the strategy an explicit **trend-follower riding beta**, not a source of standalone alpha. Size and risk it as beta accordingly.

> ⚠️ Still in-sample on one 5.4-month 2026 slice. This test removes the *drift* confound; it does **not** remove the single-regime confound. A driftless-replay PF > 1.1 here would still need walk-forward confirmation before it counts.
