# GitHub Discussions seed posts

Three short threads to drop into the Discussions tab the day you open it,
plus the welcome announcement. Posted in this order, they fill out
Announcements, Q&A, Ideas, and Show-and-tell so the tab is never empty
when a first-time visitor lands on it.

Order to post:
1. Welcome (Announcements) — pin this one
2. Show-and-tell — gives visitors a concrete result to look at
3. Q&A — anchors the kind of depth you want in technical questions
4. Ideas — open call, easiest for visitors to participate in

---

## 1. Announcements — Welcome (PIN THIS)

**Title:** 👋 Welcome to Quant Trading System Discussions

**Category:** Announcements

**Body:**

Hi, and thanks for stopping by. This is the discussion space for the [Quant Trading System](https://github.com/vrd07/Quant_Trading) — a multi-strategy MetaTrader 5 trading system designed to survive prop-firm risk rules.

If you're new here, the best on-ramp is:

1. The [README](https://github.com/vrd07/Quant_Trading) — what the system does and how to run it
2. The [research paper](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md) — 15,500 words covering every subsystem in plain English
3. The [audit-driven lessons](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md#18-empirical-lessons) — 13 things production has taught the system

### What goes where

| If you want to... | Use |
|---|---|
| Report a bug or a crash | [Issues](https://github.com/vrd07/Quant_Trading/issues) |
| Propose a concrete code change | [Pull request](https://github.com/vrd07/Quant_Trading/pulls) |
| Ask "how does X work?" or "why was Y chosen?" | **Q&A** |
| Suggest a new strategy, indicator, or feature | **Ideas** |
| Share your config tweaks, backtest results, or a forked variant | **Show and tell** |
| Anything else — broker-specific advice, philosophical debates, war stories | **General** |

### Ground rules

- **No financial advice.** Nothing in this repo or in these discussions is investment guidance. Trade real money at your own risk.
- **No "what should I trade" threads.** This isn't a signals service. The discussions are about the *system*, not about market predictions.
- **Be specific.** If you're reporting a problem, paste the version, the config you ran, and the relevant log lines. If you're proposing an idea, sketch how it would interact with the risk engine.
- **Be kind.** Trading is a humbling activity. Most of us are here because we lost money figuring something out. Treat people accordingly.

— Varad

---

## 2. Show-and-tell — Audit-v3 budget run results

**Title:** 📊 Audit-v3 budget run — per-strategy results (Jan 2025 → Mar 2026)

**Category:** Show and tell

**Body:**

Sharing the canonical comparison run that's referenced throughout the [research paper](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md). All strategies were run on XAUUSD 5-minute / 15-minute bars over the same 14-month window, under an identical per-trade USD risk budget, so the differences below isolate the strategy from the position sizer's choices.

![equity curves](https://raw.githubusercontent.com/vrd07/Quant_Trading/main/docs/equity_curves.png)

| Strategy | Return | Sharpe | PF | WR | Trades | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| **Kalman Regime** | **+4.62%** | 0.08 | 1.15 | 26.8% | 1,252 | −2.74% |
| **Momentum** | **+4.68%** | 0.06 | 1.10 | 25.2% | 2,023 | −5.33% |
| **Breakout** | +1.23% | 0.02 | 1.02 | 30.1% | 907 | −5.60% |
| Mini Medallion v1 | −3.44% | −0.08 | 0.85 | 26.9% | 668 | −4.07% |

Notes for anyone reading the table for the first time:

- **The win rates are deliberately low.** Most strategies use a 2–8× ATR take-profit against a 1.5–2× ATR stop. A 25 % win rate at RR 4 is profitable; a 60 % win rate at RR 1 is not.
- **Mini Medallion v1 is shown on purpose.** It lost money, was disabled, was retuned with completely different parameters as v5, and is now back in production with PF 1.31 on a fresh 12-month sample. The full audit story is in [Section 18 of the paper](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md#18-empirical-lessons).
- **The drawdowns look small because the per-trade risk budget was tight** ($15 / trade on a $50K notional balance — same as a $10K live account at 0.15 % per trade). Drawdowns scale roughly linearly with per-trade risk; do not expect this from a fractional-Kelly run.

If you fork this and run a different period or a different per-trade budget, drop the numbers below and we'll keep a running comparison thread.

The chart is regenerable from `data/backtests/audit_v3_budget_*.csv` via `python docs/generate_equity_curve.py`.

---

## 3. Q&A — Why a 16-step risk engine?

**Title:** 🛡️ Why a 16-step risk engine instead of a single combined check?

**Category:** Q&A

**Body:**

A reasonable first reaction to [`src/risk/risk_engine.py`](https://github.com/vrd07/Quant_Trading/blob/main/src/risk/risk_engine.py) is "this could be one big function." It used to be. Posting the rationale here so it's findable as a thread.

Three reasons each check is its own numbered helper:

**1. Cheap-to-expensive ordering matters.** The first check (kill switch) is a single boolean read; the last check (per-trade risk-per-dollar) is a Decimal multiplication with a value-per-lot lookup. Arranging the cascade so the cheap ones short-circuit the expensive ones means a clearly-blocked order rejects in microseconds rather than touching the position sizer.

**2. Each check has different exit semantics.** Some checks raise typed exceptions that propagate out of the trading loop and stop the system (kill switch, daily-loss limit, drawdown). Others return `(False, reason)` and let the loop continue with the next signal. Mixing those two in one function led to bugs where a "soft" rejection accidentally raised, halting the whole system over a max-positions cap.

**3. Audit-driven configurability.** Three of the sixteen checks were added *after* a production audit revealed a specific bleed pattern. Hour-blackout (Check 03) blocks UTC 14–16 because the first 145-trade audit lost $196 in those three hours. Pre-trade daily-loss budget (Check 06) computes "if this trade hits SL, will we breach?" and rejects proactively, because the original reactive cap was too late to save the day. Manual-trade cap (Check 09) was added because manual-tagged orders accounted for −$358 of −$400 net loss. Each of those was a one-paragraph diff to the risk engine; in a monolithic function it would have been a much higher-risk change.

The full description with motivations is in [Section 7.2 of the paper](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md#72-the-sixteen-checks-in-order).

If you have a 17th check that should exist, post it here — what failure mode does it catch, and what does it look at? The bar for a new check is "this prevents a class of breach that the existing 16 cannot."

---

## 4. Ideas — Strategies for the next backtest cycle

**Title:** 💡 What strategies should the next backtest cycle evaluate?

**Category:** Ideas

**Body:**

The current strategy stack is 13 strategies, of which 11 are live. The full list is in the [README table](https://github.com/vrd07/Quant_Trading#-the-strategy-stack); the rationale for each is in [Section 6 of the paper](https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md#6-the-strategy-layer).

I'm collecting candidates for the next backtest cycle. The criteria for inclusion:

1. **Symbol-agnostic by construction.** XAUUSD is the primary, but the strategy should make sense on BTC, ETH, and EUR/USD too.
2. **Bar-aligned, not tick-aligned.** The system polls every 250ms but evaluates strategies on bar close. Anything that needs sub-second decisions is out.
3. **Stateless w.r.t. positions.** Strategies cannot see open positions, account balance, or daily P&L. They emit `Signal` objects; the risk engine and executor handle the rest. Anything that needs to know its own P&L mid-trade does not fit the contract.
4. **Has a published or empirical backtest you can point at.** "I think this might work" is fine for a forked branch but won't get backtest time on the canonical run.

Drop suggestions below — name, one-paragraph rationale, link to source material if there is one. I'll grade each against the four criteria above and pick 2–3 to actually backtest.

A few I've been thinking about, to anchor the format:

- **Kelly-sized momentum** — same momentum signal, but lot size scaled by a fractional-Kelly estimator from the live trade journal. The Kelly module already exists ([`src/risk/kelly.py`](https://github.com/vrd07/Quant_Trading/blob/main/src/risk/kelly.py)) but is not currently in the live path.
- **News-reaction momentum** — fire 30 seconds after a high-impact news release if the post-release move is in the same direction as the pre-release trend. The news filter ([`src/data/news_filter.py`](https://github.com/vrd07/Quant_Trading/blob/main/src/data/news_filter.py)) already knows when news is happening; this would invert it from a blackout into a trigger.
- **Cross-asset basket reversion** — when XAUUSD diverges from a rolling-correlation basket of (DXY inverse, BTC, S&P 500), fade the divergence. Would require a small data engine extension to ingest the secondary symbols' bars at the same cadence.

What else?
