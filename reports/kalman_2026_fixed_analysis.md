# Kalman v2 — 2026 YTD Fixed-Parameter Backtest (XAUUSD 15m)

**Generated:** 2026-06-16 · **Script:** `scripts/backtest_kalman_2026_fixed.py`
**Period:** 2026-01-02 → 2026-06-15 (this year only) · 10,469 15m bars
**Signal engine:** the *real* `KalmanRegimeStrategy.on_bar()` (same code as live), config = `config_live_5000.yaml` kalman_regime block.

## Exact rules simulated (live-faithful)

| Knob | Value | Source |
|---|---|---|
| Stop loss | **FIXED 33.0 pts** | ≈ live 3.0 × median 2026 15m ATR(14)=10.95 |
| Take profit | **FIXED 33.0 pts (RR 1.0)** | live `kalman_min_tp_rr: 1.0` |
| Lot | **FIXED 0.02** | XAUUSD `min_lot`; the floor live actually trades |
| Breakeven | SL→entry at +1.2R | `trailing_stop.breakeven_atr_mult: 1.2` |
| Lock | SL→entry+0.5R at +2.0R | `lock_atr_mult: 2.0`, `lock_fraction: 0.5` |
| Daily loss limit | **$150, blocks new entries, resets daily** | `absolute_max_loss_usd: 150` |
| Kill switch / circuit breaker / max-DD halt | **OFF (ignored)** | per request |
| max_positions / hedge lock | 2 / no-hedge | live |
| Fills | signal@close(t) → fill@open(t+1); 0.20/side cost; TP exact; adverse gaps fill at gapped open; same-bar SL+TP → SL first | realistic |

Edge cases modeled: gap-through stop, gap-through target, same-bar SL+TP tie, same-bar entry+exit, breakeven/lock ratchet, daily-cap mid-day block + reset, weekend holds, end-of-data force-close.

---

## Headline (primary scenario)

| Metric | Value |
|---|---|
| Signals emitted | 1,429 (840 BUY / 589 SELL) |
| Trades taken | 608 (skipped: 548 max-pos, 215 daily-cap, 58 hedge-lock) |
| **Net P&L** | **+$1,676.42 (+33.5% of $5k)** |
| Final equity | $6,676.42 |
| Win rate | 53.0% (322W / 286L) |
| Profit factor | 1.09 |
| Expectancy | +$2.76 / trade |
| Avg win / loss | +$66.00 / −$68.45 |
| Largest loss | −$207.80 (gap-through) |
| Max consecutive losses | 12 |
| **Max drawdown** | **−$1,823.40 (−25.4%)** — Apr 8 → Jun 8 |
| Days daily-cap hit | 28 of 118 |

> Thin but real edge (PF 1.09) **wrapped around a 25% drawdown** that is far outside the live $250 / 5% limit. The daily cap cannot contain it (see §Daily-cap).

## Gold context — 2026 was a post-peak DOWN/round-trip year
Jan-2 open **4327** → Feb intraday peak **5589** → trough **4053** → Jun-15 **4353**.
A-shape: rally into Feb, then a ~27% correction into June. This *inverts* gold's historical bullish drift and drives most patterns below.

---

## Month by month

| Month | N | Win% | PF | Net$ | Exp$ | MaxLoss | MaxConsecL | EndEq |
|---|---|---|---|---|---|---|---|---|
| 2026-01 | 123 | 58.5% | 1.38 | **+1,312.62** | +10.67 | −100.38 | 8 | 6,312.62 |
| 2026-02 | 85 | 45.9% | 0.84 | −480.40 | −5.65 | −66.40 | 9 | 5,832.22 |
| 2026-03 | 165 | 51.5% | 1.06 | +298.00 | +1.81 | −66.40 | 13 | 6,130.22 |
| 2026-04 | 99 | 51.5% | 0.94 | −196.40 | −1.98 | −207.80 | 10 | 5,933.82 |
| 2026-05 | 82 | 53.7% | 1.08 | +223.80 | +2.73 | −131.20 | 6 | 6,157.62 |
| 2026-06 | 54 | 57.4% | 1.34 | +518.80 | +9.61 | −66.40 | 9 | 6,676.42 |

Best: Jan (+1,313) & Jun (+519). Worst: Feb (−480, the choppy top) & Apr (−196, war whipsaw). Losses cluster in topping / high-vol-whipsaw regimes.

## Patterns

**By side** — the down-year flips kalman's usual bias:
| Side | N | Win% | PF | Net$ |
|---|---|---|---|---|
| BUY | 367 | 49.9% | 0.96 | −552.60 |
| SELL | 241 | 57.7% | **1.32** | **+2,229.02** |

> SELL carried the year. ⚠️ Live was silently **long-only** until the 2026-06-12 HTF-filter fix → live missed the profitable short side for most of H1.

**By regime** — the structural edge is trend, not range:
| Mode | N | Win% | PF | Net$ |
|---|---|---|---|---|
| TREND | 482 | 54.8% | 1.16 | +2,458.82 |
| RANGE (OU) | 126 | 46.0% | 0.83 | −782.40 |

**By exit reason** — binary outcomes; **breakeven NEVER fires at RR 1.0** (TP@+1.0R beats BE@+1.2R; all 608 exits at stage 0):
| Reason | N | Net$ |
|---|---|---|
| take_profit | 322 | +21,252 |
| stop_loss | 286 | −19,576 |
| breakeven / locked | 0 | 0 |

**Daily distribution** — classic trend-follower, **opposite of "no losing days"**:
- Green days **39%** (46 of 118) · median day **−$1.80** · mean day +$14.21
- Best +$660 (Jan-28) · worst **−$369 (Apr-12)** — overshoots the $150 cap via open positions + gaps

**Best/worst UTC hours** (small samples): strong 13h, 21h, 16h, 4h, 1h; weak 0h, 9h, 11h, 17h, 20h.
**Weekday:** Tue best (+$1,832, PF 1.67); Thu/Fri weak (negative).

---

## The daily-cap experiment (the core of the request)

Ignoring the kill switch and keeping only a $150/day loss limit:

| Config | Trades | Net$ | PF | WR | Max DD |
|---|---|---|---|---|---|
| Daily-cap **ON** $150 (primary) | 608 | +1,676 | 1.09 | 53.0% | **−25.4%** |
| Daily-cap **OFF** | 703 | **+4,239** | 1.20 | 55.3% | −19.3% |

**The cap costs ~$2,560 of profit AND deepens the drawdown.** Because kalman has positive per-trade expectancy, blocking entries after an early-day −$150 removes positive-EV recovery trades and locks in red days. A daily cap protects a strategy whose *losses predict more losses*; kalman's don't.

**Why the daily cap cannot save the drawdown:** the −25% max DD is a **slow 2-month bleed (Apr-8 peak $7,183 → Jun-8 trough $5,360)**, ~40 trading days each within the $150 cap. Only a trailing max-DD kill switch (ignored here) enforces the 5% live limit.

---

## Sensitivity grid (lot 0.02, cost 0.20, BE on, daily-cap on)

| SL | RR | TP | N | Win% | PF | Net$ | MaxDD% |
|---|---|---|---|---|---|---|---|
| 22 | 1.0 | 22 | 811 | 52.8% | 1.08 | +1,386 | −18.2% |
| 22 | 1.5 | 33 | 714 | 43.1% | 1.13 | **+2,365** | −22.7% |
| 22 | 2.0 | 44 | 667 | 31.3% | 0.99 | −106 | −36.4% |
| **33** | **1.0** | **33** | **608** | **53.0%** | **1.09** | **+1,676** | **−25.4%** |
| 33 | 1.5 | 50 | 478 | 38.9% | 0.96 | −709 | −50.9% |
| 33 | 2.0 | 66 | 423 | 29.6% | 0.94 | −984 | −54.8% |
| 49 | 1.0 | 49 | 360 | 50.6% | 0.99 | −206 | −50.5% |
| 49 | 1.5 | 74 | 284 | 41.9% | 1.10 | +1,530 | −40.6% |
| 49 | 2.0 | 98 | 264 | 31.1% | 0.99 | −236 | −56.9% |

- **Tighter stop wins risk-adjusted:** SL 22 / RR 1.5 = +$2,365 at −22.7% DD; SL 22 / RR 1.0 = lowest DD (−18.2%).
- Wide RR (2.0) consistently degrades — kalman's exits don't reach 2R reliably on 2026 gold.
- **Lot:** 0.05 → +$4,486 but −51.8% DD (linear scaling of edge *and* risk).
- **Cost:** 0.0/0.20/0.50 per side → +$2,126 / +$1,676 / +$1,495 (cost-tolerant).
- **BE toggle (RR 2.0):** 42 BE exits net −$119 — breakeven slightly *hurts* (trims winners-in-progress to ~$0).

---

## Verdict

1. Kalman v2 has a **real but thin edge** on 2026 gold (PF ~1.1, +$2.76/trade) that survives realistic costs and fills.
2. Its **risk profile is incompatible with the live $250/5% drawdown limit** without the kill switch — fixed-param DD is 19–25%.
3. **In 2026 the edge lived on the SHORT side and in TREND mode** — a regime inversion vs gold's historical bullish drift; the long-only HTF bug cost live most of H1.
4. The **$150 daily cap is a prop-firm constraint, not a performance tool** — it costs profit and worsens DD for this positive-expectancy strategy.
5. **Breakeven is dead weight at the live RR 1.0.** If you want BE to matter, raise RR ≥ 1.5 — but that lowers win rate and (at this stop) net.
6. **Best fixed geometry in-sample:** tighter stop (22 pt) + RR 1.0–1.5. *In-sample only — not walk-forward validated.*
