# Launch posts — copy / paste, do not commit changes to these without thinking

These are drafts for the three highest-leverage channels for this project.
Don't post all three on the same day — space them by 2–3 days so each
audience can engage independently.

Order of posting (recommended):
1. **r/algotrading** first (most domain-relevant audience, easiest crowd)
2. **Show HN** 2–3 days later (broader tech audience; works only if step 1
   produced at least a handful of stars and one or two issues to show life)
3. **X / Twitter thread** the same day as Show HN (drives complementary traffic)

The single biggest factor is timing — post **Tuesday–Thursday between 14:00 UTC
and 18:00 UTC** for both HN and Reddit. That is when the US wakes up and the
EU is still online; weekends are dead zones for technical content.

---

## 1. r/algotrading post

**Title:**
> I built a 13-strategy algo system designed to survive prop-firm risk rules — open-sourced with the full research paper

**Body:**

After failing two prop-firm challenges last year on tilt-driven manual trading, I decided the only way I'd survive the daily-loss-cap rules was to take myself out of the loop entirely. The result is a Python + MetaTrader 5 system I've been running for the last few months. Today I open-sourced the whole thing under MIT, plus a 15,500-word research paper that walks through every component.

**Repo:** https://github.com/vrd07/Quant_Trading
**Paper:** https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md

Highlights:

- **13 strategies** — Kalman regime-switcher, Donchian breakout, momentum, VWAP, a 10-signal Mini-Medallion composite, SMC order blocks with FVG confluence, Wyckoff continuation, Fibonacci golden zone, descending channel, Asia range fade, structure-break-retest. Each one independently configurable, regime-gated, and per-session whitelisted.
- **A 16-step risk engine** that has absolute veto over every order. Kill switch is a one-way latch — once tripped, manual intervention required. Pre-trade daily-loss budget computes "if this trade hits SL, will we breach?" and rejects proactively rather than reactively.
- **Nightly ML regime classifier** (RandomForest + Markov smoother + RL-lite performance feedback) that rewrites strategy weights once per UTC day so the system adapts TREND / RANGE / VOLATILE without code changes.
- **Audit-driven, not vibes-driven.** Every parameter in the live config carries the backtest or production-audit decision that produced it, and the paper has 13 named lessons learned from running it for real.

Audit-v3 budget run (XAUUSD 5m, Jan 2025 → Mar 2026, identical per-trade risk across strategies):

| Strategy | Return | PF | Trades | Max DD |
|---|---:|---:|---:|---:|
| Kalman Regime | +4.62% | 1.15 | 1,252 | −2.74% |
| Momentum | +4.68% | 1.10 | 2,023 | −5.33% |
| Breakout | +1.23% | 1.02 | 907 | −5.60% |
| Mini Medallion v1 | −3.44% | 0.85 | 668 | −4.07% |

The Mini Medallion v1 row is on purpose — the paper documents how it was disabled, retuned, and re-enabled as v5 with PF 1.31 on a fresh sample. The losing strategy is part of the story.

Happy to answer anything about the architecture, the strategies, the regime classifier, or the audit workflow. Stars and feedback both welcome.

---

## 2. Show HN post

**Title:**
> Show HN: A 13-strategy MetaTrader 5 trading system designed to survive prop-firm risk rules

**Body:**

Hi HN. I built this over the last several months after failing two prop-firm challenges on undisciplined manual trading. The system is now open under MIT.

The interesting engineering parts (to a non-trading reader):

- A **single-threaded 250 ms event loop** that handles 5 timeframes of bar data, 13 strategies firing on bar close, a 16-step risk-validation cascade, position management, MT5 reconciliation, and crash-safe state persistence — all in one Python process with no asyncio, no message broker, and no shared-memory tricks.
- A **file-based RPC bridge to MetaTrader 5**: the platform has no public Python API on macOS or Linux, so the system communicates with MT5 via a directory of small JSON files. This makes it cross-platform — Windows native, macOS / Linux via Wine. Round-trip latency is 200–500 ms.
- A **typed domain model** that uses Python's `Decimal` everywhere for monetary arithmetic. An earlier version used `float` and accumulated $4 of error after 200 trades; well within tolerance for a hobby project, fatal for a system that must respect a daily-loss cap by the cent.
- A **nightly RandomForest regime classifier** with a Markov-chain smoother and an RL-lite performance-feedback loop that rewrites per-strategy weights once per UTC day. Strategies that have been losing in the last 30 days get a small downward weight nudge; profitable ones get a small upward nudge.

I wrote a 15,500-word research paper walking through every subsystem in plain English: https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md

Repo: https://github.com/vrd07/Quant_Trading

The paper might be more interesting than the code. Section 18 in particular is an honest catalogue of 13 audit-driven lessons learned from running this system for real money — including the bug that made me write a regression test for UTF-16 file encoding, and the auto-clearing kill switch experiment that I had to revert after the first time it tripped on garbage broker data.

Happy to discuss the architecture, the strategies, or the operational lessons.

---

## 3. X / Twitter thread (10 tweets)

1/ Last year I failed two prop-firm trading challenges on tilt-driven manual trading. So I built a system to take myself out of the loop. Today I open-sourced it. 13 strategies, 16-step risk engine, 15,500-word research paper. 🧵 https://github.com/vrd07/Quant_Trading

2/ The hardest part of a prop-firm challenge isn't generating returns. It's not blowing the daily-loss cap. The win-condition is asymmetric — you gain nothing from staying inside the rules but lose everything by exceeding them, even by one cent.

3/ So the centre of the system isn't strategies. It's a 16-step risk-validation cascade that has absolute veto power over every order. Kill switch is a one-way latch. Pre-trade daily-loss budget computes "if this hits SL, will we breach?" and rejects proactively.

4/ The strategy layer is 13 independent algorithms running on 5-min and 15-min bars: Kalman regime-switcher, Donchian breakout, momentum, VWAP reversion, SMC order blocks + FVG, Wyckoff stair-step, Fibonacci golden zone, plus a 10-signal Mini-Medallion composite.

5/ Every parameter in the live config carries the backtest or production audit decision that produced it. Mini Medallion v1 lost money in production → was disabled → was retuned with v5 parameters → re-enabled with PF 1.31 on a fresh sample.

6/ A nightly RandomForest classifier (with a Markov smoother and an RL-lite performance feedback loop) rewrites per-strategy weights once per UTC day. The system adapts to TREND / RANGE / VOLATILE regimes without code changes.

7/ The whole thing runs cross-platform — Windows native, macOS / Linux via Wine — because the bridge to MetaTrader 5 is just a directory of JSON files. No asyncio, no message broker. Single 250ms event loop. Boring solutions ship.

8/ The 15,500-word paper covers everything: architecture, data pipeline, all 13 strategies in detail, the risk engine, the regime classifier, the file bridge, the testing strategy, deployment, and 13 named lessons learned from running it live: https://github.com/vrd07/Quant_Trading/blob/main/RESEARCH_PAPER.md

9/ Honest things in the paper: a regression test I had to write after MQL5's UTF-16 encoding silently dropped every other character; an auto-clearing kill switch that I reverted after it tripped on a garbage broker quote; why mean-reversion does not work on gold.

10/ Repo is MIT-licensed: https://github.com/vrd07/Quant_Trading. Stars and issues both welcome. Happy to answer anything in the replies.

---

## When you post — what to do in the first hour

1. The first 30 minutes determine the next 24 hours of traffic. Have the repo open in a browser tab and refresh the issues / PRs / discussions tab every few minutes.
2. Reply to **every** top-level comment within an hour. Do not let the first hour pass without engagement.
3. If a question is technical, drop a code link from the repo (`src/risk/risk_engine.py:143-270` for "how does the risk engine work?"). Specific links convert browsers into stargazers.
4. Do **not** auto-reply with a generic thank-you. Read each comment, respond substantively, and ask a follow-up question of your own.
5. If someone finds a bug or suggests a feature, open an issue with their wording in the title and tag them. They get a notification and the repo gains an issue (visible signal of activity).
6. If a comment is critical (e.g. "this won't work on broker X" or "you're missing slippage modelling"), thank them, ask for specifics, and add their concern to a `KNOWN_LIMITATIONS.md` file. Acknowledged limitations beat hidden ones every time.

---

## When NOT to post

- Friday afternoon UTC, weekends, or major US holidays.
- If the repo has a broken build or a failing test on `main`.
- If you don't have 4 hours of free time the day of the post.
