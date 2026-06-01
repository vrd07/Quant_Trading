# A Production Quantitative Trading System for XAUUSD: Architecture, Risk Management, and Empirical Lessons

**Repository:** `vrd07/Quant_Trading`
**Target instrument:** XAUUSD (spot gold), MetaTrader 5
**Target account:** The5ers prop-firm challenge, $5,000 tier
**Initial draft:** April 2026
**Last updated:** May 2026

---

## Table of Contents

1. [Abstract](#1-abstract)
2. [Introduction](#2-introduction)
3. [System Overview](#3-system-overview)
4. [Data Architecture](#4-data-architecture)
5. [Strategy Design](#5-strategy-design)
6. [Risk Engine](#6-risk-engine)
7. [Execution Engine](#7-execution-engine)
8. [Portfolio Engine and Trade Journal](#8-portfolio-engine-and-trade-journal)
9. [State Management and Crash Recovery](#9-state-management-and-crash-recovery)
10. [Nightly Regime Classifier](#10-nightly-regime-classifier)
11. [MT5 Bridge](#11-mt5-bridge)
12. [Configuration System](#12-configuration-system)
13. [Confluence Gate](#13-confluence-gate)
14. [Backtesting Framework](#14-backtesting-framework)
15. [Testing Strategy](#15-testing-strategy)
16. [Performance Analysis](#16-performance-analysis)
17. [Discussion](#17-discussion)
18. [Empirical Lessons](#18-empirical-lessons)
19. [Limitations and Future Work](#19-limitations-and-future-work)
20. [Conclusion](#20-conclusion)

---

## 1. Abstract

This paper documents a production quantitative trading system designed for the XAUUSD (spot gold) market on MetaTrader 5 (MT5). The system targets The5ers $5,000 prop-firm evaluation, which imposes a 5% daily-loss limit, a 10% maximum drawdown limit, and a one-strike account termination rule. The architecture combines thirteen independent signal-generating strategies, a sixteen-step risk engine with absolute veto power over every order, a nightly machine-learning regime classifier that rebalances strategy weights across TREND / RANGE / VOLATILE market states, a file-based MT5 bridge for cross-platform compatibility, and periodic state serialization for crash recovery. Section 18 documents empirical lessons learned during development and live deployment; Section 19 summarises limitations and future work.

---

## 2. Introduction

Prop-firm trading challenges impose constraints that are unusual in discretionary or institutional contexts: a hard daily-loss cap that resets at midnight UTC, a trailing maximum drawdown that closes the account permanently if breached, and a profit target that must be reached before the drawdown is hit. These constraints mean a single bad session can end the evaluation regardless of prior performance. The system described here was designed from the ground up around these constraints rather than retrofitting them onto a general-purpose algo framework.

The design philosophy prioritises determinism and auditability over novelty. Every parameter in every live config is annotated with the backtest or production-audit finding that produced it. Strategies are killed or retuned when audits produce negative evidence, not retained for psychological reasons. Six of the original thirteen strategies are currently on the kill list and disabled in all live configs.

---

## 3. System Overview

The system starts from `src/main.py`. `TradingSystem.setup()` wires six subsystems from the active YAML config: MT5Connector, DataEngine, StrategyManager, RiskEngine, ExecutionEngine, and PortfolioEngine. `TradingSystem.run()` is the main event loop.

**Data flow:**

```
MT5 Terminal → EA_FileBridge.mq5 (file I/O) → MT5Connector
→ DataEngine (ticks → multi-TF bars → indicators)
→ StrategyManager (strategies emit Signals)
→ ConfluenceGate (combo filtering, kill-list enforcement)
→ RiskEngine (VETO POINT: kill switch, drawdown, sizing)
→ ExecutionEngine → MT5 → Market
→ PortfolioEngine → TradeJournal + StateManager
```

A nightly regime classifier (`scripts/regime_classifier.py`) rewrites strategy weights by emitting a `config_override_XAUUSD.json` file that `main.py` consumes on the next loop iteration.

---

## 4. Data Architecture

`DataEngine` (`src/data/`) ingests raw ticks from the MT5 file bridge and maintains multi-timeframe OHLCV bars at 1m, 5m, 15m, 1h, and 1d granularity. Bars are built using left-labeling (the bar's timestamp is its open time) to avoid look-ahead bias. Indicators — ATR, Bollinger Bands, VWAP, Kalman filter states, ADX, RSI, MACD, EMA stacks — are computed incrementally on each new closed bar and cached in DataEngine; strategies receive a read-only snapshot.

The DataEngine also maintains a rolling tick buffer used by the MT5Connector heartbeat monitor to detect bridge disconnects. A `SymbolMatches` helper (added 2026-05-20, commit `34eaa06`) resolves broker-appended symbol suffixes (e.g. `XAUUSDs` → canonical `XAUUSD`) at the data-ingestion layer so upstream code works with canonical names throughout.

---

## 5. Strategy Design

All strategies inherit `BaseStrategy` (`src/strategies/base_strategy.py`), implement an `on_bar(bars, indicators)` method, and emit `Signal` objects. A key design constraint is that **strategies must be stateless**: no persistent state is stored inside strategy objects between bar events. All mutable state (running P&L, open positions, regime weights) lives in DataEngine or PortfolioEngine. This constraint enables clean crash recovery — strategy objects can be reconstructed from config alone without replaying history.

### Strategy inventory

| # | Strategy | File | Regime | Live |
|---|----------|------|--------|------|
| 1 | KalmanRegime | `kalman_regime_strategy.py` | TREND + RANGE | ✅ |
| 2 | Breakout | `breakout_strategy.py` | TREND | ❌ kill list |
| 3 | MeanReversion | `mean_reversion_strategy.py` | — | ❌ kill list |
| 4 | Momentum | `momentum_strategy.py` | TREND | ✅ (confluence filter only) |
| 5 | VWAP | `vwap_strategy.py` | RANGE | ✅ (confluence filter only) |
| 6 | MiniMedallion | `mini_medallion_strategy.py` | All | ❌ kill list |
| 7 | StructureBreakRetest | `structure_break_retest.py` | TREND | ✅ (confluence filter only) |
| 8 | FibonacciRetracement | `fibonacci_retracement_strategy.py` | All | ✅ (confluence filter only) |
| 9 | DescendingChannelBreakout | `descending_channel_breakout_strategy.py` | TREND | ❌ kill list |
| 10 | SMCOrderBlock | `smc_ob_strategy.py` | All | ✅ (confluence filter only) |
| 11 | SupplyDemand | `supply_demand_strategy.py` | — | ❌ kill list |
| 12 | AsiaRangeFade | `asia_range_fade_strategy.py` | RANGE | ✅ (confluence filter only) |
| 13 | ContinuationBreakout | `continuation_breakout_strategy.py` | TREND | ❌ kill list |

---

## 6. Risk Engine

`RiskEngine` (`src/risk/`) is the system's central control point. No order reaches MT5 without passing all sixteen sequential checks. Any single failed check rejects the signal and logs the veto reason. The checks, in order, are:

1. Kill switch (manual or auto-triggered hard stop)
2. Circuit breaker (active pause after consecutive losses)
3. Hour blackout (no trading during defined off-hours)
4. News blackout (ForexFactory high-impact event window)
5. Regime override (classifier-emitted strategy disable)
6. Daily loss budget check
7. Maximum drawdown check
8. Absolute loss ceiling check
9. Maximum open positions check
10. Directional lock (configurable; prevents same-direction stacking)
11. Per-strategy exposure limit
12. Symbol exposure limit
13. Correlation filter
14. Signal strength floor
15. ATR-based position sizing via `PositionSizer`
16. Minimum lot and margin adequacy check

The kill switch is one-way: once tripped it requires a manual reset in config. This prevents an automated re-entry after a catastrophic drawdown event.

### Position sizing

`PositionSizer` (`src/risk/`) uses ATR-scaled sizing: lot size = `risk_per_trade_usd / (atr * atr_multiplier * value_per_lot)`. The `value_per_lot` is instrument-specific and accounts for the different pip values across gold, crypto, and FX symbols. An optional `max_notional_pct` cap (added 2026-05-30, commit `597379a`) limits position size to a percentage of account balance, providing a price-aware ceiling for instruments whose per-lot notional value changes with price.

---

## 7. Execution Engine

`ExecutionEngine` (`src/execution/`) translates validated `Signal` objects into MT5 order commands, tracks order state through the MT5 file bridge, and processes fill notifications. Order lifecycle states are: PENDING → SENT → FILLED / REJECTED / CANCELLED. Fill processing updates PortfolioEngine and appends a trade record to the journal.

The execution engine is idempotent on duplicate fill events: a fill already recorded for a given MT5 ticket is a no-op. This protects against the file bridge delivering duplicate response files on reconnect.

---

## 8. Portfolio Engine and Trade Journal

`PortfolioEngine` (`src/portfolio/`) maintains the canonical view of open positions and realised P&L. On each bar it reconciles the local position state against MT5's reported open positions, using ticket numbers as the primary key. The reconciliation layer uses a `SymbolMatches` helper to handle broker symbol suffixes.

From 2026-05-30 (commits `7d800d7`, `435290b`, `f3a8770`), a **signal context sidecar** persists per-position metadata — strategy name, signal strength, regime at entry, and entry timestamp — to a JSON file keyed by MT5 ticket. This sidecar survives process restarts and is loaded during reconciliation, enabling per-strategy P&L attribution and exit-reason classification for positions that were open during a reconnect cycle.

`TradeJournal` appends a structured CSV row for each closed trade, capturing entry/exit price, lot size, P&L, strategy name, regime, signal strength, and exit reason (SL / TP / manual / confidence-flip).

---

## 9. State Management and Crash Recovery

`StateManager` (`src/state/`) serialises the full runtime state — open positions, daily P&L, consecutive-loss counter, kill-switch status — to a JSON snapshot every 10 seconds. On startup, `TradingSystem.setup()` loads the latest snapshot and restores risk counters before accepting new signals. A process killed at 03:00 UTC will recover at 03:01 UTC with correct daily-loss accounting and position state.

The design constraint that strategies are stateless is what makes this recovery possible: only the six scalar risk counters and the position list need to be persisted. If strategies held local moving averages or open-trade context, those would also need to be serialised.

---

## 10. Nightly Regime Classifier

`scripts/regime_classifier.py` runs once per UTC day and classifies the current market state for each active symbol into one of three regimes: TREND, RANGE, or VOLATILE. The classifier uses a RandomForest model trained on rolling indicator features (ADX, ATR ratio, volatility percentile, directional bias), smoothed by a Markov chain to prevent single-bar regime flips, with a reinforcement-learning feedback loop that adjusts feature weights based on recent per-strategy performance scores.

The classifier emits `data/config_override_XAUUSD.json` (and sibling files for BTC/ETH), which `main.py` loads at the top of each event loop. The override file contains per-strategy enable/disable flags and weight multipliers. The `STRATEGY_WEIGHTS` dictionary in `regime_classifier.py` (around line 243) must have identical keys across all three regime columns — `test_weights_table_completeness` enforces this in CI.

---

## 11. MT5 Bridge

The system communicates with MetaTrader 5 through a file-based bridge rather than the MT5 Python API. The bridge consists of:

- `mt5_bridge/EA_FileBridge.mq5`: an MQL5 Expert Advisor that polls the MT5 Common Files directory for command JSON files, executes them (open/close/modify), and writes response files.
- `mt5_bridge/mt5_file_client.py`: the Python-side wrapper that writes command files and polls for responses with configurable timeout.

The MT5 Common Files directory is auto-detected per OS: `%APPDATA%\MetaQuotes\Terminal\Common\Files` on Windows; the Wine-hosted equivalent on macOS/Linux. This makes the system cross-platform without requiring OS-level branches in the trading logic.

The file-bridge design trades latency for portability. For 15-minute bar strategies, the additional file I/O latency (~50–200ms) is negligible. Scalping strategies (sub-1m) are not currently supported.

---

## 12. Configuration System

Live configs live in `config/config_live_{account_size}.yaml`. All six account tiers (100, 1000, 5000, 10000, 25000, 50000) share the same strategy parameter names; only risk limits and lot-sizing parameters differ. This design means a strategy parameter tuned on one account tier can be propagated to all tiers with a one-line commit per file. The CLAUDE.md documents the propagation checklist to ensure no file is missed.

Key risk parameters for the $5k account (as of 2026-05-30):
- `risk_per_trade_usd: 50` (Kalman); `risk_per_trade_pct: 0.003` (other strategies)
- `max_daily_loss_pct: 0.03` ($150)
- `max_drawdown_usd: 250`
- `max_positions: 2`
- Circuit breaker: 3 consecutive losses → 15-minute pause; 5 → hard stop

---

## 13. Confluence Gate

`ConfluenceGate` (`src/strategies/confluence_gate.py`, added 2026-05-14) post-filters raw strategy signals before they reach the RiskEngine. The gate implements three combination policies:

- **COMBO A (TREND):** `structure_break_retest` primary + `momentum` confirm + optional `fibonacci_retracement` level agreement. All legs must agree on direction within a configurable `window_minutes`.
- **COMBO B (RANGE):** `vwap` primary + `smc_ob` precision + optional `asia_range_fade` session gate.
- **COMBO C (Sniper):** `smc_ob` + `fibonacci_retracement` + `momentum` all aligned in any regime → emits a `combo_sniper` signal sized `sniper_lot_multiplier` (default 1.5×).

`kalman_regime` is on an explicit solo-allowlist and bypasses all combo requirements. Kill-list strategies (`breakout`, `mean_reversion`, `supply_demand`, `descending_channel_breakout`, `mini_medallion`, `continuation_breakout`) are blocked by the gate even if `enabled: true` appears in config, providing a defence-in-depth backstop. Disabling the gate (`confluence_gate.enabled: false`) reverts to passthrough for non-kill-list strategies.

---

## 14. Backtesting Framework

`scripts/run_backtest.py` drives `src/backtest/` through historical OHLCV data. The backtest engine reuses the same `StrategyManager`, `ConfluenceGate`, `RiskEngine`, and `PositionSizer` as the live system — there is no separate backtest-only code path for these components. This ensures that a parameter change validated in backtest applies identically in live.

The data engine enforces left-labeling in resampled bars (commit `468f875`, 2026-05-25) to prevent a subtle look-ahead bias where the bar's close is used to compute an indicator before the bar has actually closed. Walk-forward validation (rolling train/test split) is the preferred evaluation method over full-period optimisation; the commit logs document each case where walk-forward results diverged from in-sample results.

---

## 15. Testing Strategy

Unit tests in `tests/unit/` mock all MT5 dependencies and run without a live connection (`pytest -m "not integration"`). Integration tests in `tests/integration/` require a running MT5 instance. Key tests include:

- `test_regime_classifier.py::test_weights_table_completeness` — enforces that `STRATEGY_WEIGHTS` has identical keys in all three regime columns.
- Per-strategy unit tests verify signal generation logic on synthetic bar sequences.
- `test_risk_engine.py` verifies all sixteen veto checks in isolation and in combination.

---

## 16. Performance Analysis

Backtested results (Jan 2025 → Mar 2026, XAUUSD 5m/15m, identical per-trade USD risk budget):

| Strategy | Return | Profit Factor | Trades | Max DD |
|----------|-------:|----------:|-------:|-------:|
| KalmanRegime | +4.62% | 1.15 | 1,252 | −2.74% |
| Momentum | +4.68% | 1.10 | 2,023 | −5.33% |
| Breakout | +1.23% | 1.02 | 907 | −5.60% |
| MiniMedallion v1 | −3.44% | 0.85 | 668 | −4.07% |

Post-walk-forward tuning of KalmanRegime (train Jan–Mar 2026, test Apr–May 2026): win rate lifted from ~30% to ~50%; net return on test window +8.58%, PF 1.92 (`d09411d`, 2026-05-30).

Live performance (week of 2026-05-18): 2 closed trades, KalmanRegime +$16.56 (1W/0L), manual trade −$45.06. Automated system net-positive; discretionary override the sole loss.

---

## 17. Discussion

The system's architecture reflects three recurring production-trading tensions:

**Determinism vs. adaptivity.** Pure deterministic rule-based systems degrade as market regimes shift; pure adaptive systems overfit and behave unpredictably in production. The nightly regime classifier resolves this tension by isolating adaptivity to a single, auditable overnight batch job that rewrites config; intraday execution remains fully deterministic against the current config snapshot.

**Strategy count vs. correlation.** More strategies increase signal frequency but can introduce correlated drawdowns when multiple strategies fire in the same direction during the same adverse regime. The ConfluenceGate explicitly controls this by requiring multi-leg agreement before routing signals to the RiskEngine.

**Prop-firm constraints vs. strategy alpha.** Strategies that are net-positive in unrestricted backtests may still be unsuitable for prop-firm trading if their drawdown path triggers the account limit before the profit target is reached. The kill-list decisions for `breakout` (PF 1.02, high DD) and `mean_reversion` reflect this constraint rather than an absolute judgement about edge.

---

## 18. Empirical Lessons

This section records lessons learned through backtest audits and live deployment. Lessons are not rewritten after the fact; new entries are appended with date and commit references.

---

**Lesson 1: A ten-signal composite strategy (MiniMedallion v1) produced negative alpha in the initial audit period.**
MiniMedallion v1 returned −3.44% with PF 0.85 over 668 backtest trades (Jan 2025–Mar 2026, XAUUSD). Combining ten weak alpha signals into a composite score did not suppress noise sufficiently; the threshold (±3.0) was too permissive, admitting marginal entries that individually contributed small losses. The strategy was disabled, audited, and retuned to v5 (51% WR, PF 1.31, +6.9% annualised on a fresh 12-month out-of-sample window) before a conditional re-enable was considered. It remains on the kill list in all current live configs pending further validation.

**Lesson 2: A pure Donchian breakout strategy on gold had near-zero edge after costs.**
The `breakout` strategy returned +1.23% with PF 1.02 over 907 backtest trades. At this profit factor, realistic spread and slippage costs eliminate the edge entirely; the strategy also produced the highest maximum drawdown of the four tested (−5.60%). It was added to the kill list. Breakout dynamics are captured without the noise by `structure_break_retest`, which waits for a retest of the broken level rather than entering on the initial candle.

**Lesson 3: Pure z-score mean reversion without regime gating is not viable on gold.**
`mean_reversion` applies an Ornstein-Uhlenbeck z-score with entry at |z| > 2.0. XAUUSD spends significant time in trending regimes where a z-score entry is a counter-trend trade with unlimited adverse excursion. The strategy has no regime filter of its own; without one it is unreliable. The `kalman_regime` strategy handles OU mean-reversion in RANGE with an explicit regime gate, making a separate `mean_reversion` strategy redundant. Disabled and placed on kill list.

**Lesson 4: Confluencing signals from independent strategies reduces correlated false positives.**
Before the ConfluenceGate, six independent strategies could simultaneously emit BUY signals during a high-volatility news candle, all reaching the RiskEngine. The RiskEngine's per-strategy exposure limit blocked most of them, but the rate of marginal entries (signals that just cleared all risk checks individually) was elevated. The ConfluenceGate (`src/strategies/confluence_gate.py`, 2026-05-14) requires multi-leg agreement and reduced the signal count while preserving most of the expected-value-positive entries.

**Lesson 5: Stateless strategy design is not merely a code-quality preference — it is a crash-recovery requirement.**
Early prototypes stored rolling indicator state (e.g. Kalman filter covariance matrices) inside strategy objects. A process crash required manual inspection to determine which partial state needed to be reset. Moving all mutable state to DataEngine and PortfolioEngine meant strategy objects could be reconstructed from config alone on restart, with no integrity risk. The ten-second StateManager serialization cycle covers the remaining recovery window.

**Lesson 6: ATR-based position sizing responds appropriately to volatility regime changes.**
Fixed-lot sizing produced outsized drawdowns during the high-volatility sessions of early 2026 when XAUUSD ATR expanded significantly above its 20-day average. Switching to ATR-scaled sizing (`lot = risk_usd / (atr * multiplier * value_per_lot)`) automatically reduced lot size during high-volatility periods, capping the per-trade dollar risk regardless of intraday ATR spikes. The mechanism is implemented in `PositionSizer` (`src/risk/`).

**Lesson 7: High-impact ForexFactory news events require a hard blackout, not a soft signal-strength penalty.**
During US CPI and FOMC releases, gold exhibits adversarial price action — tight spreads widen dramatically and initial directional moves often reverse within minutes. A soft signal-strength penalty (reducing position size during news) did not adequately protect against the first candle after the release. A hard blackout window (±N minutes around each high-impact event) in the RiskEngine reduced news-related losses to near zero. The cost is missed moves on genuine breakouts immediately after news, which the backtest analysis judged as an acceptable trade-off for prop-firm constraint compliance.

**Lesson 8: A nightly regime classifier with Markov-chain smoothing prevents regime-flip churn.**
An initial classifier without smoothing produced day-to-day regime oscillations (TREND → RANGE → TREND) that caused strategy weight changes to fire and reverse within 48 hours. Adding a Markov transition probability matrix to the regime decision smoothed the output: regime transitions now require consistent evidence over multiple bars before the override file is rewritten, reducing unnecessary strategy weight swings and their associated strategy-switch transaction costs.

**Lesson 9: Supply and Demand zone detection generates an unacceptable false-positive rate without stricter impulse confirmation.**
`supply_demand` identifies the first retest of a fresh ATR-sized zone after an impulse candle. In practice, XAUUSD generates many partial impulse candles that qualify under the initial criteria but are not genuine institutional order flow. The false-positive rate produced a negative expectancy in audit. The strategy is disabled; the impulse-confirmation logic from `smc_ob` (which uses a 5-phase state machine including sweep confirmation) is the preferred implementation for zone-based entries.

**Lesson 10: A Wyckoff continuation strategy generates no signal volume in a RANGE regime.**
`continuation_breakout` requires TREND conditions: a stair-step structure of higher lows (long) or lower highs (short) built over multiple bars. During the 30-day live review period (2026-04-30 to 2026-05-28), XAUUSD remained in RANGE with 75–83% classifier confidence, and the strategy fired zero trades. The ADX(14) reading of 12.57 at the time of the review was far below the strategy's own `adx_min_threshold: 26` gate. This confirmed that regime-gating alone is sufficient to suppress the strategy in unfavourable conditions; the kill-list flag added on 2026-05-13 was a belt-and-suspenders measure pending a fresh TREND-regime live sample (commit `c9c1547`).

**Lesson 11: Consecutive-loss circuit breakers are necessary but their parameters must be calibrated to the strategy's natural drawdown run length.**
Setting the circuit breaker too tight (pause after 2 consecutive losses) caused frequent mid-session pauses during normal drawdown sequences that did not exceed the daily loss budget. Setting it too loose (pause after 6) allowed the daily loss budget to be consumed before the breaker activated. Calibrating the breaker window against the strategy's empirical maximum losing streak from backtest data produced a parameter of 3 losses → 15-minute pause, 5 losses → hard stop, which protects the daily budget while avoiding spurious pauses.

**Lesson 12: The one-way kill switch must be manually reset to prevent automated re-entry after a catastrophic event.**
An early implementation allowed the kill switch to self-clear after a cooling-off period. This created a risk of automated re-entry into a market that was still in an adverse state. The kill switch was changed to be permanently latched: once set (either by the circuit breaker's hard-stop threshold or manually), it requires an explicit config change and process restart to clear. This ensures a human reviews the situation before automated trading resumes.

**Lesson 13: Three-leg confluence in the COMBO_SNIPER pattern justifies a larger position multiplier.**
When `smc_ob` (institutional order block), `fibonacci_retracement` (golden zone pullback), and `momentum` (trend confirmation) all emit a signal in the same direction within the confluence window, the alignment of three independent detection methods with different information sources reduces the probability that the combined signal is noise. Backtesting this condition with a 1.5× size multiplier improved expectancy without meaningfully increasing drawdown, because the base cases (only one or two legs agree) still occur at 1.0× size. The multiplier is configurable via `sniper_lot_multiplier` in the `confluence_gate` config block.

---

**Lesson 14: Walk-forward tuning of KalmanRegime's TP ratio reveals a win-rate / expectancy trade-off that strongly favours a lower floor.**
The `kalman_min_tp_rr` parameter was tuned from 2.0 to 1.0 (commits `d09411d`, `2fde429`, 2026-05-30). Walk-forward validation (train Jan–Mar 2026, test Apr–May 2026) showed that requiring TP at 2× stop earned higher per-trade expectancy in-sample but missed too many valid exits on the test window — the filter was too restrictive in the ranging structure that dominated April–May. Allowing TP at 1× stop, combined with raising `min_signal_strength` from 0.50 to 0.70, lifted out-of-sample win rate from ~30% to ~50% and was the only parameter combination that remained net-positive on the test window. Full-period XAUUSD backtest: +8.58% net, PF 1.92. This parameter was propagated to all six account-size configs on the same day.

**Lesson 15: Broker-appended symbol suffixes silently break live risk logic that uses exact ticker equality.**
The confidence-flip block in the RiskEngine compared the signal's canonical ticker (`"XAUUSD"`) against the broker-reported open position ticker (`"XAUUSDs"`). The strict equality check returned no match; the signal fell through to the Kalman low-confidence suppressor and was blocked rather than triggering the intended position flip. This was caught during live verification on the paper account (commit `a3f5ffc`, 2026-05-27). The fix uses a case-insensitive prefix match in either direction, so broker suffixes are tolerated while distinct instruments (`BTCUSD` vs `XAUUSD`) still produce no match. The broader lesson: any live code path that compares ticker strings across Python and MT5 must treat suffix variants as equal.

**Lesson 16: A flat `max_lot` cap is price-blind and produces near-zero risk exposure on low-price-per-lot instruments.**
When BTC and ETH were configured with `min_lot == max_lot == 0.01`, the RiskEngine treated this as an operator-fixed lot and returned it verbatim, bypassing `PositionSizer` entirely. Even when `PositionSizer` ran, the flat cap was insensitive to price: 0.01 lot on BTC was ~$700 notional but 0.01 lot on ETH was ~$23 notional, yielding only $0.16 of actual risk when the config intended $15–50 per trade. A new `max_notional_pct` field caps lots at `min(max_lot, balance * pct / (price * value_per_lot))`, providing a margin-aware, price-aware ceiling (commit `597379a`, 2026-05-30). Default value is 0 (disabled), so XAUUSD and all configs without the field are byte-identical to the prior behaviour. Backtest finding from the same commit: even with correct sizing, Kalman on BTC/ETH cannot pass the $250 max-drawdown / $150 daily-loss limits of the $5k config.

**Lesson 17: A volatility breaker is anti-correlated with strategies that extract alpha from volatility spikes.**
A magnitude-based ATR breaker was implemented to pause new entries and move green stops to breakeven when ATR spikes significantly above its trailing baseline (commit `9623649`, 2026-05-30). Jan–May 2026 XAUUSD backtest showed it made `kalman_regime` worse at every threshold tested: `kalman_regime` derives most of its alpha from high-ATR trending legs, and pausing entry on ATR spikes removed the trades that contributed the majority of the strategy's profit (full-period net fell from $3,811 to $939 at the 1.6× threshold). The feature ships disabled by default (`volatility_breaker.enabled: false`) and is retained only as a flash-crash tail guard for genuinely abnormal ATR events (e.g. geopolitical shocks). The generalised lesson: risk overlays that reduce activity on volatility spikes are systematically anti-correlated with long-volatility strategies; their cost must be measured against the strategy's own volatility profile.

**Lesson 18: MT5 reconciliation strips signal metadata from positions, requiring a persistent sidecar.**
When the Python process reconciles open positions with MT5 on reconnect, the broker returns only price, size, and ticket data — not the strategy name, signal strength, or market regime that generated the trade. Without additional persistence, any trade open during a process restart would have no strategy attribution and could not be classified by exit reason (SL / TP / confidence-flip). A JSON sidecar keyed by MT5 ticket number now persists signal context between reconciliation cycles (commits `7d800d7`, `435290b`, `f3a8770`, 2026-05-30). The sidecar is written at entry and loaded during reconciliation; a missing sidecar entry is treated as an unknown-origin position and logged as such rather than raising an exception.

---

## 19. Limitations and Future Work

- **XAUUSD concentration.** The system is designed and validated exclusively on gold. Extension to other instruments (BTC/ETH/EURUSD) requires separate regime classifier training, independent audit periods, and instrument-specific risk limits. The $250 max-drawdown constraint of the $5k tier is too tight for BTC/ETH under Kalman sizing (see Lesson 16).

- **Walk-forward window is short.** Current walk-forward splits use 3-month training and 2-month test windows. A longer out-of-sample period (6–12 months) would give higher confidence in parameter stability, but data availability limits this.

- **Regime classifier refreshes only once per UTC day.** Intraday regime shifts (e.g. a trend-to-range transition mid-session) are not reflected until the next nightly run. An intraday lightweight classifier that triggers on significant ATR or ADX changes could reduce lag, but its interaction with the existing weight-smoothing logic would need careful design.

- **File-bridge latency.** The MT5 file bridge introduces 50–200ms round-trip latency per order, which is acceptable for 15-minute bar strategies but limits the system to strategies with bar intervals of at least 1 minute. A direct MT5 Python API integration would remove this ceiling but sacrifices cross-platform compatibility.

- **Manual override trades erode live performance attribution.** The 2026-05-18 weekly report showed a manual trade losing −$45.06 while the automated system earned +$16.56. Manual trades bypass all RiskEngine checks and corrupt per-strategy performance scoring for the regime classifier's feedback loop. Restricting the MT5 account to EA-only mode during evaluation periods is recommended.

- **No live options or hedging instruments.** Downside protection during high-impact news events relies entirely on the news blackout. Adding a systematic hedge (e.g. a gold inverse ETF) during risk-off periods would reduce drawdown path variance at the cost of implementation complexity.

---

## 20. Conclusion

The system demonstrates that a multi-strategy quantitative trading system can be built with strict prop-firm constraint compliance as a first-class design goal rather than an afterthought. The core architectural decisions — absolute-veto risk engine, stateless strategies, file-based MT5 bridge, nightly regime classifier, and crash-safe state management — collectively produce a system that is auditable, recoverable, and suitable for unattended overnight operation. The empirical lesson record in Section 18 documents that production insights do materially change the system: six of thirteen strategies have been disabled, key parameters have been retuned by walk-forward evidence, and several production bugs (symbol suffix matching, price-blind lot caps, metadata loss on reconciliation) were discovered only during live deployment. Continued auditing against live performance data is the primary mechanism for future improvement.

---

*End of document. Last lesson added: Lesson 18 (2026-05-30). Next scheduled review: 2026-07-01.*
