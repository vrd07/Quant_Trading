# A Production-Grade Multi-Strategy Algorithmic Trading System for Prop-Firm Challenges

### Architecture, Strategies, and Risk Engineering on MetaTrader 5

**Author:** Varad Bandekar
**Repository:** `github.com/vrd07/Quant_Trading`
**Last revised:** 2026-05-03

---

## Abstract

This paper describes the design, implementation, and operational behaviour of a Python-based automated trading system targeting the spot gold (XAUUSD), Bitcoin (BTCUSD), Ethereum (ETHUSD), and EUR/USD markets through the MetaTrader 5 (MT5) terminal. The system was purpose-built to satisfy the strict capital-preservation rules of retail proprietary-trading ("prop firm") challenges, where a single rule violation forfeits the funded account. It combines a deterministic, single-threaded event loop with thirteen independently configured trading strategies, an institutional-style risk engine that exercises absolute veto over every order, a nightly machine-learning regime classifier that rewrites strategy weights once per UTC day, a file-based bridge to MT5 that runs cross-platform via Wine on macOS and Linux, and a state manager that survives crashes by serialising every critical mutation to disk. We document the full data path from raw broker tick to filled order, give plain-English derivations of each strategy and its parameters, walk through the sixteen-step risk-validation chain that gates every trade, and explain the empirical, audit-driven decisions that shape the production configuration. The paper aims to be accessible to a general technical reader while preserving enough engineering detail that the system could be reproduced or extended.

---

## 1. Introduction

### 1.1 The problem

A prop-firm challenge is a contractual arrangement under which a retail trader is given a small evaluation account (typically between $5,000 and $100,000) by a firm such as The5ers, FTMO, or Goat Funded Trader, and must hit a profit target without breaching two hard limits: a daily-loss cap (commonly 1–5% of the starting equity) and a maximum total or trailing drawdown (commonly 5–10%). A single breach, even by one cent, ends the challenge and forfeits the trader's deposit. Because the limits are absolute and asymmetric — a trader gains nothing from staying within them, but loses everything by exceeding them — the design problem is dominated by *capital preservation under uncertainty* rather than expected-return maximisation.

Most retail trading literature focuses on signal generation. The strategies that follow are interesting, but they are not the system's centre of gravity. The centre of gravity is the engineering scaffolding — risk gating, state recovery, deterministic execution, audit trails — that ensures that no signal, however confident, can violate a rule. A trader who survives the evaluation captures asymmetric upside via the firm's funded-account programme; a trader who does not survives only with reduced ego.

### 1.2 The instrument

The system focuses primarily on **XAUUSD** (spot gold quoted in U.S. dollars, written in MT5 as a continuous decimal price like 4583.82). Gold was chosen for three reasons. First, it has high intraday volatility (often $5–$20 per ounce per session), which is necessary to produce statistically meaningful trade frequency on small accounts where a $1 move equals one cent of P&L per 0.01-lot contract. Second, it has well-understood seasonal and session patterns: the London Open (07:00 UTC) and the London/New-York overlap (12:00–16:00 UTC) carry the bulk of liquidity, while the late-Asia / London-lunch window (09:00–14:00 UTC) is reliably range-bound. Third, gold reacts predictably to U.S. macroeconomic data (CPI, Non-Farm Payrolls, FOMC), allowing news blackouts to be encoded as a static rule rather than learned from data. Secondary symbols (BTC, ETH, EUR/USD) share the same code paths and benefit from the same regime classifier, but this paper concentrates on gold.

### 1.3 The system at a glance

The repository contains roughly 15,000 lines of Python organised into eleven top-level packages under `src/`, plus a Metatrader Expert Advisor (`mt5_bridge/EA_FileBridge.mq5`) written in MQL5. The runtime is a single process invoked from `src/main.py`. The process opens a connection to MT5, preloads two thousand bars of history per symbol, instantiates all enabled strategy classes, restores any prior state from disk, and enters a 250-millisecond polling loop that runs continuously until terminated. Every quarter-second, the loop pulls fresh ticks, builds and stores bars at five timeframes (1m, 5m, 15m, 1h, 4h), updates open positions with the latest mark prices, manages trailing stops, evaluates strategies that are scheduled for the current trading session, validates any signals through the risk engine, and forwards approved orders to MT5 via the file bridge. Once a minute, the loop logs aggregate metrics; once every five minutes, it renders a console dashboard; once every thirty seconds, it reconciles the in-memory portfolio against the broker's truth; once a day at 00:00 UTC, it triggers a nightly regime classifier and resets the daily counters.

The configuration is driven by a single YAML file selected via `--config`, with naming convention `config_live_{account_size}.yaml`. The active configuration for a $10,000 account, for example, sets a 1.5 % daily-loss cap ($150), a 7 % trailing drawdown cap ($700), an absolute per-trade USD risk of $15, a maximum of two open positions, and a tightened circuit breaker that pauses trading for thirty minutes after three consecutive losses. The same file enumerates the thirteen strategies with their per-strategy timeframes, parameters, regime preferences, and per-session whitelists.

### 1.4 What this paper covers

Section 2 describes the design philosophy that underpins each subsystem. Section 3 presents the high-level architecture and the data flow from broker tick to filled order. Section 4 details the typed domain model (Symbol, Bar, Tick, Order, Position, Signal). Section 5 documents the data pipeline and the indicator library. Section 6 is the longest single section: it describes each of the thirteen strategies in plain English, with motivation, signal logic, and the empirical results that drove their current parameters. Section 7 walks the sixteen-step risk-validation chain. Sections 8 through 12 cover position sizing, execution, portfolio reconciliation, state management, and the nightly regime classifier. Section 13 explains the MT5 file bridge and its cross-platform behaviour. Section 14 surveys the configuration system and how the same code runs across account sizes. Sections 15–17 cover backtesting, monitoring, and operational deployment. Section 18 collects the audit-driven lessons learned from running the system in production. Section 19 lists known limitations and future work.

---

## 2. Design Philosophy

The codebase makes its design lineage explicit. A file named `.agents/workflows/codinglegits.md` lists eleven engineers — John Carmack, ThePrimeagen, TJ Holovachuk, George Hotz, Jeff Dean, Linus Torvalds, Donald Knuth, Joe Armstrong, Niklaus Wirth, Dennis Ritchie, and Brian Kernighan — and assigns rules of thumb to each. Routine engineering decisions in this repository are explicitly framed against one or two of those legends. Several of the rules show up as inline comments and in commit history; they are summarised here because they explain otherwise non-obvious code patterns.

**Carmack: explicit state and worst-case awareness.** Mutable session state is collected into one visible `SessionState` dataclass (`src/core/types.py:323`) instead of being scattered across the trading loop. Daily counters reset atomically through `SessionState.reset_daily()`. The risk engine's cascaded checks are arranged so the worst case (kill-switch breach) short-circuits before any side effect can occur.

**Jeff Dean: design for failure and instrument everything.** Every trade decision, signal rejection, and risk-engine veto is logged as structured JSON via `monitoring/logger.py`. The trade journal (`monitoring/trade_journal.py`) writes a CSV row per closed trade. The live monitor (`monitoring/live_monitor_emitter.py`) writes a JSON snapshot of system state once per second so an external dashboard process can render the UI without contending for the trading loop.

**Linus Torvalds: explicit registry over magic.** The strategy manager (`src/strategies/strategy_manager.py:39`) holds a `STRATEGY_REGISTRY` dictionary mapping each canonical strategy name to its concrete class. Adding a new strategy requires four touch points (registry, regime classifier, configs, tests), enumerated in `CLAUDE.md` so they cannot be silently forgotten.

**Knuth: single source of truth.** The Average True Range (ATR) calculation in the regime classifier is implemented once in `_compute_atr()` and reused by feature extraction and label generation, instead of being duplicated. The risk-per-trade calculation uses the same `value_per_lot` multiplier in both the pre-trade budget gate and the per-trade-risk gate so the two checks always agree.

**TJ Holovachuk: small modules, single responsibility.** The risk engine, originally written as a single 200-line `validate_order()` method, has been refactored into sixteen numbered helper methods (`_check_01_kill_switch`, `_check_02_circuit_breaker`, … `_check_16_risk_per_trade`). Each helper has exactly one responsibility, and the parent function is now a flat sequence of calls. The inlined ordering is preserved exactly because each helper short-circuits the next.

**ThePrimeagen / Wirth: boring solutions ship.** The system uses no message broker, no shared-memory ring buffer, no asyncio, and no microservices. Inter-component communication inside the process is plain function calls. Inter-process communication with MT5 is a directory of small JSON files. The slowest part of the loop (the MT5 file poll) takes roughly two milliseconds in steady state.

**Geohot: own your stack.** The MT5 bridge is a Python file client and an MQL5 Expert Advisor that this project owns end-to-end. Historical-data preload is tried in three priority orders — first MT5's `CopyRates` directly, then a local CSV cache the system itself wrote on a previous run, and only as a last resort the third-party `yfinance` library. The system explicitly prefers controlled failure to controlled magic.

These rules are not a checklist applied uniformly. The code routes design decisions to the legend whose instinct best matches the layer: hot-loop and risk code are reviewed against Carmack and Jeff Dean; refactors of sprawling files are reviewed against TJ; backtest scripts are reviewed against ThePrimeagen and Wirth.

A second philosophical thread runs through the configuration: **almost every parameter you see is the product of an audit, not a guess.** Comments in `config_live_10000.yaml` such as “v3 loose: 2.0 → 1.5 — backtest shows 2× trades, 2× absolute return at same PF/Sharpe” trace the parameter genealogy. Many strategies were tuned, then disabled, then re-enabled with new parameters after a fresh backtest. The codebase carries this history rather than hiding it.

---

## 3. System Architecture

### 3.1 The process

The trading system is a single Python process. It is started from a thin shell script (`scripts/start_live.sh` on Unix or `start_live.bat` on Windows) which activates the virtual environment and invokes `python src/main.py --config config/config_live_10000.yaml`. The launcher prompts the operator for an explicit "YES" before going live; this gating is the cheapest possible defence against an accidental live run.

The process is deliberately single-threaded. The only operating-system concurrency is provided by the MT5 file bridge: the EA running inside MT5 polls the shared file directory at a configurable interval (default 100 ms), and the Python side polls the same directory in its main loop. The lack of shared mutable state across threads removes an entire category of concurrency bugs and matches the natural rhythm of the underlying market: gold trades around the clock but the strategies operate on bar closes (at 1m, 5m, or 15m boundaries), not on every tick.

### 3.2 The main loop

`TradingSystem.run()` in `src/main.py` is the heartbeat of the system. The loop body, simplified, looks like this:

```
while running:
    if kill_switch.is_active(): break
    data_engine.update_from_connector()           # pull ticks, append bars
    update_portfolio_prices()                     # mark-to-market open positions
    manage_trailing_stops()                       # 1 Hz throttle
    refresh_manual_position_tracker()             # detect manual MT5 trades
    process_strategies()                          # generate signals, execute
    process_fills()                               # update portfolio with fills
    if loop_iteration % 60 == 0: manual_trade_monitor.check_once()
    if should_save_state(): save_state()
    if should_reconcile():    reconcile_portfolio()
    if loop_iteration % 60 == 0:  log_metrics()
    if loop_iteration % 300 == 0: display_dashboard()
    live_monitor.write_snapshot(self)             # 1 Hz throttle internal
    time.sleep(0.25)
```

Every operation is wrapped in exception handlers. Critical risk violations (kill switch, daily-loss limit, drawdown limit) raise typed exceptions and break the loop. Connection-loss errors trigger a reconnect attempt. Every other unhandled exception is logged with full traceback and the loop sleeps five seconds before retrying. The intent is that no single bad bar, malformed broker response, or transient I/O failure can take the system down — only an explicit risk breach can.

The 250-millisecond sleep is itself an engineering choice. A one-second loop misses meaningful price movements during news (gold can move $0.50–$2.00 in a single second around the FOMC announcement); a 50-millisecond loop multiplies the rate of MT5 file polls and therefore the rate of bridge errors without changing the SL behaviour because ATR and indicator updates are bar-aligned. 250 ms is the empirical sweet spot.

### 3.3 The data flow

A single trade decision flows through the system in this order:

1. **MT5 terminal** sees a new tick from the broker and writes it to its in-memory buffer.
2. **`EA_FileBridge.mq5`**, the MT5 Expert Advisor written in MQL5, publishes the tick into a shared file directory (the MT5 *Common/Files* directory) as a small JSON status file.
3. **`mt5_bridge/mt5_file_client.py`** (the Python side of the bridge) reads that file, parses it, and returns a structured dictionary.
4. **`MT5Connector`** (`src/connectors/mt5_connector.py`) wraps the file-client output and converts it into typed `Tick`, `Position`, and `Order` objects from `src/core/types.py`. The connector also auto-detects the broker's UTC offset on connect, by reading the broker's `server_time` field and rounding the difference to the nearest whole hour.
5. **`DataEngine`** (`src/data/data_engine.py`) ingests the tick, routes it to the appropriate `BarBuilder` for each timeframe, and rolls the bar over when the timeframe boundary is crossed. A `CandleStore` per (symbol, timeframe) holds the last 5,000 bars in a pandas DataFrame.
6. **`StrategyManager`** (`src/strategies/strategy_manager.py`) is asked for new signals. For each enabled strategy on each enabled symbol, it looks up the strategy's preferred timeframe (`config.strategies.<name>.timeframe`), pulls bars from the candle store, deduplicates so the same bar is never processed twice for the same strategy, and calls `strategy.on_bar(bars)`. Each strategy returns either a `Signal` object or `None`.
7. **`SessionManager`** (`src/core/session_manager.py`) gates the call: if the current UTC hour is not in any active session, no strategy is evaluated. Strategies are also filtered by per-session whitelist — the `kalman_regime` strategy fires only during London, overlap, and New York; `asia_range_fade` fires only during Asia; etc.
8. Each returned signal is decorated with the session's lot-size multiplier and passed to `_execute_signal()`.
9. **`RiskEngine.validate_order()`** runs sixteen sequential checks (Section 7). On any rejection, the signal is dropped. Critical breaches (kill switch, daily loss, drawdown) raise typed exceptions that propagate up and break the trading loop.
10. **`ExecutionEngine`** (`src/execution/execution_engine.py`) builds an MT5 order from the signal and risk-engine-computed lot size, sends it via the connector, and awaits the fill response.
11. **`PortfolioEngine`** (`src/portfolio/portfolio_engine.py`) records the new position, marks it to market on each subsequent tick, and writes a `TradeJournal` row when it closes.
12. **`StateManager`** (`src/state/state_manager.py`) periodically (default every ten seconds) serialises the full system state — open positions, equity high-water mark, kill-switch status, daily P&L — to a JSON file under `data/state/`. On the next startup, this file is loaded and the system resumes exactly where it left off.

The arrows in this flow are unidirectional except for two: the trade journal writes back into the risk engine (so consecutive losses can trigger the circuit breaker), and the portfolio engine writes back into the data engine (so the live monitor can render unrealised P&L per symbol).

---

## 4. The Domain Model

The system's data types live in `src/core/types.py` and are intentionally austere. There are nine dataclasses, every monetary value is a `Decimal`, every timestamp is a UTC-aware `datetime`, and every identifier is a `UUID`. The use of `Decimal` rather than `float` is non-negotiable: a lot size of 0.02 expressed as a binary float carries irrational rounding error that can compound across thousands of P&L updates and ultimately disagree with the broker's books by several cents. `Decimal` carries no such error.

**`Symbol`** is the immutable specification of a tradeable instrument. It holds the ticker, the broker name, the pip value, the minimum and maximum lot size, the lot step, the value per lot (100 for gold; 100,000 for EUR/USD), the per-lot commission, the maximum spread the system will tolerate, the minimum stop-loss distance the broker requires, and the symbol's leverage. A single instance is constructed per ticker at startup from the active YAML config and reused for the rest of the process lifetime; `__hash__` is defined on the ticker so symbols can be used as dictionary keys.

**`Bar`** is a candlestick with OHLCV fields and a metadata dictionary. The constructor validates that the high is at least max(open, close) and the low is at most min(open, close); broker bars that fail this check are rejected as `InvalidBarError`. Properties expose the typical price ((H+L+C)/3) and the bar range. Bars are immutable in spirit; the system does not patch them after the fact.

**`Tick`** carries a bid, ask, and last price plus the trade volume at that tick. The `mid` property is (bid+ask)/2; `spread` is ask−bid; `spread_pips` is spread divided by the symbol's pip value. The system never opens a position when `spread > symbol.max_spread`.

**`Order`** is the central trading object. It carries an ID, the symbol, the side (BUY or SELL), the order type (MARKET, LIMIT, STOP), the quantity, an optional price, an optional stop loss, an optional take profit, the lifecycle status (PENDING → SENT → ACCEPTED → FILLED, or REJECTED / CANCELLED / EXPIRED), several timestamps, the eventual fill price and quantity, the commission and slippage, and a metadata dictionary that always carries the originating strategy name. The `is_terminal()` and `is_active()` predicates collapse the eight-state machine into the two binary distinctions the executor cares about.

**`Position`** represents an open position. It holds an ID, the symbol, the side (LONG / SHORT / FLAT), the quantity, the entry price, the current price, the stop loss, the take profit, the unrealised and realised P&L, the open timestamp, and a metadata dictionary. The `update_price(price)` method is called by the portfolio engine on every loop iteration; it recalculates `unrealized_pnl` by multiplying the price difference by the quantity by the symbol's `value_per_lot`. The `total_pnl` property sums realised and unrealised.

**`Signal`** is what a strategy emits. It carries an ID, the strategy name, the symbol, the side, a strength in [0, 1], a timestamp, the detected market regime (TREND, RANGE, VOLATILE, UNKNOWN), an entry price, a stop loss, a take profit, and a metadata dictionary. The constructor validates the strength bounds. The strength is later mapped to a confidence percentile and used by the executor to decide whether to allow stacked positions: only signals above a per-strategy `high_confidence_threshold` may stack up to `risk.max_positions`; weaker signals are limited to one open position per strategy.

**`RiskMetrics`** is the per-tick snapshot the risk engine emits for monitoring. It carries the account balance, equity, total exposure, net exposure, daily P&L, the daily-loss limit and the remaining headroom, the maximum drawdown, the current drawdown, the open-positions count, and the kill-switch / circuit-breaker active flags.

**`SessionState`** is the most recently introduced type. It groups every piece of mutable intra-day trading state — daily wins date, max daily profit, consecutive losses today, loss-pause threshold, loss-pause duration, loss-pause expiry, last-close timestamps per side, a reversal-buffer minute count, and the current session's lot multiplier — into a single visible object with explicit mutators (`reset_daily`, `record_loss`, `record_win`, `is_loss_paused`). Before this refactor, that state was scattered across ten separate fields on the `TradingSystem` class, and a daily reset required manually setting each. Now a daily reset is one method call and all counters are visibly grouped.

**`SystemState`** is what gets serialised for crash recovery. It is a dictionary of positions, a dictionary of open orders, the account balance, equity, equity high-water mark, daily start equity, daily P&L, total P&L, consecutive-loss counter, daily-trade count, kill-switch flag, circuit-breaker flag, last-trade timestamp, and a metadata dictionary. The `to_dict()` method converts it to a JSON-serialisable form by stringifying every Decimal and UUID, since the standard `json` module cannot encode either.

The constants module (`src/core/constants.py`) supplements the types with enumerations for `OrderSide` (BUY, SELL), `OrderType` (MARKET, LIMIT, STOP, STOP_LIMIT), `OrderStatus` (the eight-state lifecycle), `PositionSide` (LONG, SHORT, FLAT), `MarketRegime` (TREND, RANGE, VOLATILE, UNKNOWN), `TradingSession` (ASIA, LONDON, OVERLAP, NEW_YORK, LATE_NY), and `Environment` (DEV, PAPER, LIVE).

The exceptions module (`src/core/exceptions.py`) defines a small typed hierarchy: `TradingSystemError` is the root, with `InvalidBarError`, `MissingDataError`, `DataValidationError`, `RiskLimitExceededError`, `DailyLossLimitError`, `DrawdownLimitError`, `ExposureLimitError`, `KillSwitchActiveError`, `PositionSizeLimitError`, `MT5ConnectionError`, `OrderRejectedError`, `OrderTimeoutError`, and `ConnectionLostError` as named subclasses. Catching, say, `DailyLossLimitError` separately from `Exception` is what allows the main loop to react differently to risk breaches than to transient I/O errors.

---

## 5. The Data Pipeline

Trading systems can only be as good as their data pipeline. This system's pipeline is deliberately conservative: it favours correctness and observability over latency, since none of the strategies operate on a sub-second timescale.

### 5.1 Tick ingestion

The `MT5Connector` exposes `get_quote(symbol)`, which reads the latest bid/ask/last quote from the MT5 status file. `DataEngine.update_from_connector()` is called every loop iteration; for each enabled symbol it requests a fresh quote, constructs a `Tick`, and feeds it into the `TickHandler` and the per-(symbol, timeframe) `BarBuilder` instances. The `TickHandler` keeps a 10,000-tick rolling buffer for diagnostics; the `BarBuilder` emits a closed `Bar` to the `CandleStore` whenever the timeframe boundary is crossed.

The connector also exposes `copy_rates(symbol, timeframe, count)` for historical backfill and `get_history_deals(days)` for trade history reconciliation. Both are used at startup: the data engine preloads 2,000 1-minute bars per symbol so that strategies with long indicator warmups (notably `kalman_regime`, which needs roughly 130 fifteen-minute bars before it can produce a signal) start firing immediately. Without preload, the kalman strategy would silently swallow signals for the first 22 minutes of every fresh start.

### 5.2 Bar building

A `BarBuilder` accumulates ticks until the timeframe boundary is crossed, then emits a `Bar` with the open (first tick), high (max of all ticks), low (min of all ticks), close (last tick), and volume (sum of all tick volumes). The boundary is computed in UTC: a 5-minute bar that opens at 14:00:00 closes at 14:04:59.999, etc. Because the broker reports bars in its own timezone (often UTC+2 or UTC+3), and the connector auto-detects that offset on connect (`MT5Connector._detect_broker_offset`), all bar timestamps are normalised to UTC before they reach the candle store. This single normalisation point is the reason the rest of the system can treat every timestamp as UTC without further translation.

### 5.3 Candle storage

Each `(symbol, timeframe)` pair owns a `CandleStore` (`src/data/candle_store.py`). The store wraps a pandas DataFrame and exposes `add_bars`, `get_bars`, `get_bar_at`, `get_latest_bar`, `from_csv`, `to_csv`, and `__len__`. It maintains a maximum size (default 5,000 bars) and evicts the oldest bar when full. Periodically the store is also written to disk under `data/logs/candle_store_{ticker}_{timeframe}.csv` so the nightly regime classifier (Section 12) can read it without re-querying MT5.

### 5.4 Indicators

The `Indicators` class in `src/data/indicators.py` is a stateless collection of pandas-vectorised functions. Each one takes a `DataFrame` (or a `Series`) of bars and returns a `Series` of the same length with the indicator value per bar. The library covers:

- **SMA** (simple moving average), **EMA** (exponentially weighted moving average), **MACD** (12/26/9 default).
- **ATR** (Average True Range, 14-period Wilder smoothing) — used by every strategy for stop-loss sizing.
- **ADX** (Average Directional Index, 14-period) — used to gate trend-following strategies and detect range conditions.
- **RSI** (Relative Strength Index, 14-period) — overbought/oversold gate.
- **Bollinger Bands** (20-period, 2σ) and **Bollinger band width** (a volatility-compression detector used by `breakout` and `asia_range_fade`).
- **Donchian Channel** (high/low over N bars) — the breakout reference level.
- **VWAP** (volume-weighted average price) and **VWAP z-score**.
- **Z-score** (price relative to a rolling mean and standard deviation) — used by `mean_reversion`.
- **Stochastic oscillator** — supplementary confirmation for `kalman_regime`'s range mode.
- **Kalman filter** (`Indicators.kalman_filter(close, q, r)`) — a one-dimensional state-space model that estimates the unobserved "true" price level given the noisy observed close. The process noise `q` controls how quickly the filter adapts; the observation noise `r` controls how much it smooths. With `q=1e-5` and `r=0.01`, the filter behaves like a slow-adapting trend tracker; the `kalman_regime` strategy uses a price-vs-Kalman comparison as its directional bias.
- **OU z-score** (`Indicators.ou_zscore(close, kalman, window)`) — the deviation of the close from the Kalman-filtered "fair value", normalised by the rolling standard deviation. Under an Ornstein–Uhlenbeck assumption (mean-reverting random walk), large |z| values indicate temporary disequilibrium and predict reversion.
- **RV regime** (`Indicators.rv_regime(close, rv_window, rv_ma_window)`) — a regime classifier that returns 1 when the rolling realised volatility exceeds its long-run mean (trend) and 0 when it does not (range).

The indicator library is deliberately stateless. Strategies do not own indicator caches, and the pandas vectorisation is fast enough on 5,000-bar windows that recomputing the indicators on every bar is cheaper than maintaining a stateful incremental implementation. This decision sacrifices a little CPU for a large amount of correctness — there is no possibility of an indicator getting "stuck" in stale state.

### 5.5 News filter

The system suppresses signals around high-impact economic releases. `data/news_filter.py` reads a CSV downloaded once per day by `scripts/fetch_daily_news.py` from ForexFactory. The CSV contains, for each date, the time and currency of every scheduled high-impact event. A configurable buffer (default ±5 minutes) defines a blackout window; while the current UTC time is inside any blackout window, every strategy's signal is silently dropped. The news filter is currency-aware: USD events block XAUUSD, BTCUSD, ETHUSD, and EURUSD (all USD-quoted), while EUR events block only EURUSD.

### 5.6 Session tagging

`src/data/session_tagger.py` annotates bars with their trading session (ASIA, LONDON, OVERLAP, NEW_YORK, LATE_NY). The session boundaries are read from the YAML config, normalised to UTC, and applied to each bar. Strategies use this tag for session-dependent logic; the SessionManager uses it for per-session strategy whitelists.

### 5.7 Data validation

Every bar passes through `DataValidator` (`src/data/data_validator.py`), which checks for OHLC integrity, monotonic timestamps, suspicious price gaps (>5 % move from one bar to the next, which usually indicates a bad tick rather than real volatility), and zero-volume bars on a symbol that should have volume. Invalid bars are dropped and logged. This prevents a single broker glitch from poisoning the indicator window.

---

## 6. The Strategy Layer

### 6.1 The contract

Every strategy inherits from `BaseStrategy` (`src/strategies/base_strategy.py`) and implements two abstract methods: `on_bar(bars: pd.DataFrame) -> Optional[Signal]` and `get_name() -> str`. The lifecycle is dead simple: the strategy manager hands `on_bar` a fresh DataFrame of bars at the strategy's chosen timeframe, the strategy returns either a `Signal` object or `None`, and that is the entire contract. Strategies are stateless with respect to portfolio data — they cannot see open positions, account balance, or daily P&L. This is enforced by the absence of any such accessor on the base class. All position-aware logic lives in `StrategyManager`, the executor, or the risk engine.

The base class supplies four conveniences. `is_enabled()` checks the YAML toggle. `_log_no_signal(reason)` writes a structured log line every time a strategy decides not to fire, which allows the operator to review why a strategy was quiet during a session. `set_ml_regime(regime)` is a one-line setter that allows the nightly regime classifier to inject its prediction into every strategy at once. `_get_bar_hour(bars)` is a defensive helper that extracts the UTC hour from the last row of a DataFrame regardless of whether the index is a `DatetimeIndex`, a `RangeIndex` of integers, or a Unix-second timestamp — three forms the MT5 bridge has been observed to deliver, depending on the broker variant.

The thirteen strategies are described in the order they were added to the system. For each one we cover the motivation, the signal logic, the risk parameters, the regime preference, the live-config status, and the empirical results that drove its current parameters. Strategy weights from the regime classifier (Section 12) are quoted to give a sense of which regime each strategy is expected to perform in.

### 6.2 KalmanRegime — the keystone

`src/strategies/kalman_regime_strategy.py` is the most heavily-used strategy and the one most representative of the system's overall philosophy. It runs on a 15-minute timeframe and behaves differently in trending vs ranging regimes.

**Trend mode** (when `realised_volatility(20) > MA(realised_volatility, 100)`): the strategy goes long if the close is above the Kalman-filtered fair value, the 9-period EMA is above the 21-period EMA, the MACD histogram is positive, and the Kalman is accelerating upward. It goes short if all four conditions hold in the opposite direction. ADX must be above the trend gate (currently 17); the bar's UTC hour must be in the configured allowed-sessions list; and the signal strength (a function of how far Z is from the threshold) must exceed `min_signal_strength`.

**Range mode** (when realised volatility is at or below its long-run mean): the strategy uses the OU z-score instead. It goes long if z < −1.5, RSI < 42, and Stochastic < 25. It goes short if z > +1.5, RSI > 58, and Stochastic > 75.

**Risk management:** the stop loss is at `1.5 × ATR(14)` from entry; the take profit is at `8.0 × ATR(14)`. The 8-to-1 take-profit-to-stop ratio is asymmetric because gold trends well when it trends, and the strategy's whole edge comes from capturing those trends rather than scalping. A 35 % win rate at RR 5.3 is profitable; a 50 % win rate at RR 1 is not.

**Confidence gating:** signals with confidence ≥ 90 % are allowed to stack up to the global `risk.max_positions` cap. Signals with confidence < 90 % are limited to one concurrent position from this strategy. The intent is to allow a strong trend day to compound while preventing weak signals from layering risk.

**Live status:** `enabled: true` in every active config; nightly weights are 0.90 (TREND), 0.75 (RANGE), 0.90 (VOLATILE) — the highest set of any strategy. `long_only: false` (two-sided trading).

**Empirical history:** a 1,282-trade backtest revealed that hours 20–21 UTC averaged $19–$36 per trade, while hours 11–14 lost $2–$7 per trade. The session filter was tightened to `[1,7], [15,17], [20,22]` — thirteen hours per UTC day — with EMA and MACD confirmation added to filter low-conviction entries. A v3 loosening on 2026-04-19 cut the entry threshold from 2.0 to 1.5, the trend ADX gate from 20 to 17, and the cooldown from 2 to 1 bars; the backtest showed 2× trade count and 2× absolute return at the same Sharpe and profit factor, with a 2.4× larger maximum drawdown — accepted because account-level drawdown caps fire before strategy-level ones.

### 6.3 Breakout — the trend follower

`src/strategies/breakout_strategy.py` implements a Donchian-channel breakout with multi-timeframe confirmation. On the 5-minute timeframe, it watches a 12-period Donchian (60 minutes of price action). When the close exceeds the channel high, it goes long; when the close falls below the channel low, it goes short. The signal must pass several confluence gates:

- **ADX ≥ 20** — the breakout is only valid in a trending market.
- **Body-to-ATR ratio ≥ 0.30** — the breakout bar must be a real impulse, not a wick.
- **RSI within (22, 78)** — extreme RSI implies the move is already exhausted.
- **ATR spike ≥ 1.8 × ATR(14)** — confirms the breakout has volatility behind it.
- **Bollinger Band squeeze** — volatility was compressed before the breakout (the BB width must be in the bottom 70th percentile of the trailing 50-bar distribution).
- **Higher-timeframe trend alignment** — the close must be on the correct side of the 21-period EMA on the 1-hour chart.
- **EMA confirmation** — the 9-period EMA must have crossed the 21-period EMA in the breakout direction.
- **MACD confirmation** — the MACD histogram must agree with the breakout direction.
- **Session whitelist** — only the `[[4,4],[7,8],[22,22]]` hours fire; backtest data showed the rest were unprofitable.

**Risk management:** SL = `2.0 × ATR(14)`, TP at `3.0 × SL` (RR 3.0). The wider TP captures trends that breakout strategies typically truncate too early.

**Empirical history:** the v3 parameters reflect a careful re-tuning. The earlier v2 used a 1.5× ATR stop and an RR of 2.5; backtest showed too-tight stops were noise-tagged, so the v3 raised SL to 2.0× and tightened the entry by raising ADX from 18 to 20. The audit-v3 budget result reads: +1.23 %, PF 1.02, DD −5.60 %, 907 trades.

### 6.4 MeanReversion — currently disabled live

`src/strategies/mean_reversion_strategy.py` is the simplest strategy in the system. It computes a 20-period z-score of the close against its rolling mean; when |z| exceeds an entry threshold (default 2.0), it enters opposite to the deviation, with a stop at z = ±3.0 and a take-profit at z = ±0.5.

Backtest verdict: −1.63 %, PF 0.40, 15 % WR, 39 trades. The strategy is disabled in every live config; its weight in the regime classifier is 0.00 in TREND, RANGE, and VOLATILE. The reason is structural: gold has a persistent upward drift that defeats symmetric mean-reversion. The system kept the strategy in the codebase as a benchmark — when a new mean-reverting alpha is proposed, the burden of proof is to outperform `mean_reversion`, not to outperform zero.

### 6.5 Momentum — the primary daily driver

`src/strategies/momentum_strategy.py` is a session-filtered, RSI- and MACD-driven momentum strategy. Long if RSI > 53 with positive RSI slope, MACD histogram positive, ADX ≥ 22, and the 9-EMA > 21-EMA > 30-EMA stack. Short with the mirrored conditions. The signal strength must exceed 0.60 for longs and 0.65 for shorts (a one-sided long bias for gold).

**Risk management:** SL = `2.0 × ATR(14)`, TP at `2.0 × SL` (RR 2.0).

**Session whitelist:** hours `[1, 3, 4, 5, 7, 8, 10, 16, 17, 22]` UTC.

**Live status:** `enabled: true`, weight 0.80 in TREND. The audit-v3 budget result was +4.68 %, PF 1.10, DD −5.33 %, 2,023 trades — the highest trade frequency of any strategy and the second-highest absolute return.

### 6.6 VWAP — the institutional reversion

`src/strategies/vwap_strategy.py` measures the deviation of the close from the 30-period VWAP. Long entries fire when price is below the lower VWAP band by 1σ, RSI is below 45 (oversold), and CCI is below −60. Short entries mirror with RSI > 55 and CCI > 60. The strategy is restricted to the `RANGE` regime (`only_in_regime: RANGE`) and to four specific UTC hours: 02, 11, 15, 19, derived from a per-hour backtest that found these to be the only hours with positive expectancy.

**Risk management:** SL = `1.8 × ATR(14)`. The 1σ band (instead of the usual 2σ) was chosen because backtest data showed 2σ deviations rarely materialise on 5-minute gold.

**Live status:** `enabled: true`, weights 0.45 (RANGE) and 0.45 (VOLATILE), 0.00 (TREND).

### 6.7 MiniMedallion — the ten-signal composite

`src/strategies/mini_medallion_strategy.py` is the codebase's homage to Jim Simons' Medallion Fund philosophy: combine many weak alpha signals into a single composite score and trade only when many of them agree. The strategy computes ten sub-signals on each 15-minute bar:

1. **Mean-reversion** (z = (price − 30-period VWAP) / σ; weight 1.0).
2. **Momentum burst** (5-bar return; weight 1.2).
3. **Volatility expansion** (Bollinger Band width rate-of-change; weight 1.3).
4. **VWAP reversion** (institutional pull-back signal; weight 0.9).
5. **Order-flow imbalance** (bid-volume vs ask-volume ratio; weight 1.1).
6. **Liquidity-sweep detection** (previous high/low broken then immediately rejected; weight 1.5).
7. **BTC → Gold lead-lag** (BTC moves > 1 % in 5 minutes are predictive of gold reaction; weight 0.7).
8. **Market regime detection** (ADX-based trend-vs-range classifier; weight 1.2).
9. **Session volatility** (London Open / NY Open boost; weight 0.6).
10. **Volatility spike reversal** (ATR spikes are exhaustion signals; weight 0.8).

Each sub-signal returns −1 (bearish), 0 (neutral), or +1 (bullish). The composite alpha score is the weighted sum. A score above +3.5 is a long entry; a score below −3.5 is a short entry; everything in between is no-trade.

The strategy also computes three **Smart Money Concept** (SMC) sub-signals — Break of Structure (BoS), Change of Character (CHoCH), and Fair Value Gap (FVG) — but these are currently logged with weight 0.0 (they contribute to metadata but not to the score) pending backtest validation.

**Risk management:** SL = `2.2 × ATR(14)`, TP at `1.2 × SL` (RR 1.2). The wider stop and shorter TP reflect a v5 backtest that found 77 % of trades hit SL with the older 1.5× ATR stop.

**Live status:** `enabled: true`, weights 0.65–0.70 across regimes. v5 backtest: 51 % WR, PF 1.31, 6.9 % annualised. `long_only: true` (gold's upward bias defeats symmetric trading).

**Session whitelist:** only UTC hours `[5, 7, 8]` — the only hours with net-positive expectancy in the per-hour backtest.

### 6.8 Structure-Break Retest (SBR) — the textbook setup

`src/strategies/structure_break_retest.py` codifies the classic price-action pattern: price breaks a structural level, retraces to retest the broken level, and the retest is rejected. On the 15-minute timeframe, the strategy first identifies a 20-bar high or low (the "structural level"). When price closes through the level, the strategy waits up to 20 bars for a retest. If price returns to within `0.4 × ATR(14)` of the broken level and the retest bar is rejected (a wick-to-body ratio above 0.55), the strategy enters in the breakout direction.

**Risk management:** SL = `1.8 × ATR(14)` beyond the retest level (wider than the v1's 1.2× because retests often wick past), TP at `2.0 × SL` (RR 2.0).

**Filters:** ADX in (22, 55), RSI not extreme, EMA-50 confirms trend, signal strength ≥ 0.65, session in `[5, 6, 7, 13, 16, 17, 22]`.

**Live status:** `enabled: true`. v2 backtest: +5.30 %, PF 1.88, DD −1.61 %. Weights 0.80 (TREND), 0.40 (RANGE), 0.55 (VOLATILE).

### 6.9 Fibonacci Retracement Golden Zone

`src/strategies/fibonacci_retracement_strategy.py` watches for pullbacks into the 50–61.8 % retracement of a swing impulse. A swing is detected by a 5-bar pivot with a swing magnitude of at least `3.5 × ATR(14)` (the v2 `big_trend` tuning, raised from 2.0 to filter for genuinely large impulses). When price retraces into the Golden Zone (50 %–61.8 % of the swing), the strategy waits for a rejection candle (wick-to-body ratio ≥ 0.55) and enters in the original swing direction.

**Filters:** ADX in (25, 70), EMA-50 confirms, RSI not extreme.

**Risk management:** SL = `1.5 × ATR(14)` beyond the swing pivot, RR 2.5.

**Live status:** `enabled: true`. Weights 0.70 (TREND), 0.50 (RANGE), 0.55 (VOLATILE) — the RANGE weight was raised from 0.20 to 0.50 on 2026-04-22 as a trial (not backtest-validated) to let the strategy fire across all regimes. `big_trend` 6-month sweep: PF 1.75, WR 36.8 %, DD 9.2 %, 152 trades.

### 6.10 Descending Channel Breakout (DCB)

`src/strategies/descending_channel_breakout_strategy.py` detects descending channels via linear regression and trades breakouts of the upper trendline. The channel is identified by fitting a regression line through the last 60 5-minute bars; the slope must be ≤ −0.0003 (a gentle but unmistakable downtrend). A "structure shift" — a higher-low formation in the swing pivots — must precede the breakout, signalling that bears are losing control. When the close exceeds the upper trendline by `0.20 × ATR`, the strategy enters long.

**Risk management:** SL = `1.5 × ATR(14)` below the breakout, RR 2.0.

**Live status:** `enabled: true`. Weights 0.70 (TREND), 0.45 (RANGE), 0.55 (VOLATILE). 6-month backtest: +0.91 %, PF 1.57, WR 55.6 %, DD −1.27 %, 18 trades — the lowest trade count of any enabled strategy, but the highest win rate.

### 6.11 SMC Order Block (smc_ob)

`src/strategies/smc_ob_strategy.py` is the most procedurally complex strategy. It implements a five-phase state machine drawn from Inner Circle Trader (ICT) Smart Money Concepts:

- **Phase 1 — OB formation.** A bearish impulse from a swing high marks the last bullish candle before the impulse as the *order block* (OB) zone. The impulse must be ≥ `0.5 × ATR` (a v2 looser gate that produces 5× more setups than the original 2.5×).
- **Phase 2 — OB touched.** Price returns to within `1.0 × ATR` of the OB.
- **Phase 3 — Liquidity sweep.** A bar wicks below the OB low and closes back above it. The wick must occupy ≥ 40 % of the candle range (the rejection-wick gate, adapted from PDF Module 5/6 of the original ICT material).
- **Phase 4 — First buying candle.** A bullish candle appears after the sweep.
- **Phase 5 — Entry trigger.** The next candle breaks the high of the first buy candle. Entry is on that break; SL is the first-buy-candle low; TP is the OB high plus the sellers' stop-loss cluster (the "liquidity premium").

**Additional gates:** ADX ≥ 8 (a low floor to allow more trades in lower-trend conditions), EMA-50 trend filter, FVG (Fair Value Gap) confluence — when a Fair Value Gap exists within `2.0 × ATR` of the OB and is younger than 50 bars, the win rate jumps from 8.6 % to 16.7 % on long-only configurations. Equal-pivots liquidity targeting requires the sweep to take out a cluster of equal swing pivots within `0.15 × ATR` of each other; without this, the sweep is treated as low-conviction.

**Live status:** `enabled: true`, `long_only: true`. Weights 0.70 (TREND), 0.50 (RANGE), 0.60 (VOLATILE).

### 6.12 Supply/Demand — disabled live

`src/strategies/supply_demand_strategy.py` detects fresh supply/demand zones formed by impulse candles and trades the first retest of each zone. Backtest verdict: PF 1.07, DD 8.6 %, no tunable edge. The strategy is disabled in every live config and was folded into `mini_medallion` as a sub-signal with weight 0.8, but that fold-in also failed to add measurable edge. The strategy is retained in the codebase for reproducibility of the audit.

### 6.13 Asia Range Fade

`src/strategies/asia_range_fade_strategy.py` fills the dead window between the Tokyo close and the New York open. During UTC 09:00–14:00, gold typically trades in a narrow range. The strategy detects this with three conditions: ADX < 28 (no trend), Bollinger Band width in the bottom 60th percentile of the last 200 bars (volatility compressed), and RSI extreme (< 35 for long, > 65 for short). It fades with SL = `1.5 × ATR`, RR 1.5, cooldown 12 bars (3 hours).

**Live status:** `enabled: true`, `long_only: false`. Weights 0.15 (TREND), 0.70 (RANGE), 0.30 (VOLATILE) — the highest RANGE weight in the system. Backtest: +2.81 %, PF 1.31, WR 45.3 %, DD −1.58 %, 214 trades.

### 6.14 Continuation Breakout — the Wyckoff stair-step

`src/strategies/continuation_breakout_strategy.py` detects a "stair-step" breakout pattern: tight range → impulse → re-accumulation → second breakout in the same direction. The state machine tracks a 60-bar lookback window; when consolidation occupies 5–25 bars and an impulse bar with body ≥ `1.2 × ATR` follows, a re-accumulation cluster of height ≤ `2.0 × ATR` is searched for. A continuation breakout fires when the entry bar's body exceeds `0.5 × ATR` in the original impulse direction.

**Risk management:** SL at the cluster boundary plus `1.0 × ATR` buffer, RR 2.0.

**Live status:** enabled live 2026-04-30 against the backtest's recommendation; backtest PF 1.18 was on the borderline of what the system normally accepts. A cluster-anchored SL was added to `risk_processor.py` as part of the enablement to prevent the strategy from emitting a stop loss too close to the cluster boundary. Weights 0.75 (TREND), 0.30 (RANGE), 0.55 (VOLATILE).

### 6.15 The shared filters

Three modules sit alongside the strategies and are reused by several of them:

- **`regime_filter.py`** computes the rule-based regime (TREND vs RANGE) using ADX and an optional Hurst-exponent gate. Strategies that declare `only_in_regime: TREND` consult this filter at the top of `on_bar()` and bail out if the regime does not match. The filter is overridden by the ML regime classifier when the override is fresher than 24 hours.
- **`multi_timeframe_filter.py`** allows a strategy to require alignment with a higher timeframe trend (e.g. only fire long if EMA-21 on the 1-hour timeframe is rising). Most strategies use a lighter version of this internally.
- **`base_strategy.py`** provides the abstract base class and the helpers described above.

### 6.16 Strategy weights and the regime classifier

The thirteen strategies do not all fire all the time. Each has a per-strategy `enabled` flag in the YAML config; each has a per-session whitelist; each respects its own regime preference. On top of those static toggles, the nightly regime classifier (Section 12) writes an override file (`data/config_override_XAUUSD.json`) that contains a per-regime weight in [0, 1] for every strategy. The trading loop reads this file at startup and at midnight UTC; strategies whose weight in the current ML-detected regime is below the confidence threshold (0.40) are silently disabled until the next override.

This two-layer gating (static config + dynamic override) means the live system can adapt to a regime shift overnight without code changes. If gold flips into a strong trend, the classifier lifts `breakout`, `momentum`, and `kalman_regime` toward 0.85 and lowers `asia_range_fade` toward 0.15; the next bar evaluated in the morning fires only the trend-suited strategies.

---

## 7. The Risk Engine

### 7.1 Veto power

The risk engine has absolute veto power over every order. `RiskEngine.validate_order()` is the sole gateway between strategy signals and order execution; the design intent is that a strategy can never bypass it, accidentally or intentionally. The function takes the candidate order, the current account balance, the current account equity, the dictionary of currently-open positions, and the day's running P&L. It returns either `(True, "OK")` or `(False, rejection_reason)`. Critical breaches (kill-switch trip, daily-loss limit, drawdown limit) raise typed exceptions that propagate out of the trading loop.

The validation runs sixteen checks in a fixed order. The order is not arbitrary: cheaper checks come first so an obviously-blocked order short-circuits before the engine touches expensive computations, and dependent checks come after the checks they depend on. Every check is implemented as a small, pure helper named `_check_NN_<descriptor>()` so the validation flow is one flat sequence of calls and the parent function remains readable.

### 7.2 The sixteen checks, in order

**Check 01 — Kill switch.** If the kill switch is active, raise `KillSwitchActiveError`. The kill switch is a one-way latch: once tripped, only manual intervention (deleting `data/state/kill_switch_alert.json` and restarting) can clear it. This is intentional. If the switch was tripped for a legitimate reason (corrupt state, bad broker data, drawdown breach), auto-resetting would repeat the catastrophe.

**Check 02 — Circuit breaker.** If the consecutive-loss circuit breaker is active, reject. The circuit breaker (`src/risk/circuit_breaker.py`) is a soft gate: it pauses trading for a configurable cooldown (default 30 minutes) after a configurable number of consecutive losses (default 3). Unlike the kill switch, it is automatic and self-clearing.

**Check 03 — Hour blackout.** If the current UTC hour is in `risk.trading_windows.blocked_hours_utc`, reject. The blackout list is journal-driven: the audit of the first 145 production trades found that hours 14–16 UTC lost $196 of $400 net; those hours were added to the blackout, and net daily P&L improved measurably the next month.

**Check 04 — Account balance.** If account balance ≤ 0, reject. Defence against a malformed status read from MT5.

**Check 05 — Absolute daily loss.** If today's USD loss has hit `risk.absolute_max_loss_usd`, raise `DrawdownLimitError` and trigger the kill switch. This is the prop-firm guard. The check is reactive — it fires after the breach — and serves as a hard backstop for the proactive Check 06.

**Check 06 — Pre-trade daily-loss budget.** If the current trade's worst-case stop-loss hit would push the day's loss past 85 % of the absolute cap (configurable via `daily_loss_budget_safety_pct`), reject. This is the proactive gate. The risk engine computes `worst_case_trade_loss = |entry − SL| × quantity × value_per_lot` and compares `daily_loss_so_far + worst_case_trade_loss` against the budget. The 15 % safety margin protects against slippage and concurrent fills that would land just inside the absolute limit and just outside it on the next tick.

**Check 07 — Daily-loss percentage.** If `daily_loss_pct` of account balance has been exceeded, raise `DailyLossLimitError` and trigger the kill switch. This duplicates Check 05 in the percentage domain because not every operator configures an absolute USD cap; one of the two checks is always active.

**Check 08 — Daily-profit target.** If the day's P&L has reached `risk.max_daily_profit_usd`, reject. The intent is to lock in the day's profit and stop trading, since most prop-firm challenges are won by sustained moderate profitability rather than blow-out days.

**Check 09 — Manual loss cap.** If the order is manual-tagged and today's manual loss exceeds `risk.manual_guard.daily_loss_cap_usd`, reject. Audit data showed 89 % of all losses came from manual-clicked trades, not bot trades; this gate caps the daily bleed from manual.

**Check 10 — Drawdown limit.** If the equity drawdown from the high-water mark exceeds `risk.max_drawdown_pct`, raise `DrawdownLimitError` and trigger the kill switch. The drawdown is calculated by `DrawdownTracker` (`src/risk/drawdown_tracker.py`) as `(HWM − current_equity) / HWM`. The HWM is updated only when current equity exceeds the previous HWM; it never decreases on its own.

**Check 11 — Max daily trades.** If today's trade count has hit `risk.max_daily_trades` (default 12), reject. Caps overtrading on volatile sessions.

**Check 12 — Max open positions.** If the open-positions count has hit `risk.max_positions` (default 2), reject. The cap forces the strategy mix to compete: at most two strategies can be in the market at once, so the executor must choose the highest-confidence signals.

**Check 13 — Position size.** If the order quantity is ≤ 0, reject. Defence against a misconfigured strategy or a sizing bug.

**Check 14 — Symbol exposure.** If the order would push the symbol's exposure (sum of position values) past `risk.max_exposure_per_symbol_pct` of equity, reject. Caps single-symbol concentration.

**Check 15 — Stop loss present.** If the order has no stop loss, reject. Every order must have a stop loss; this is non-negotiable.

**Check 16 — Risk per trade.** If the order's worst-case loss (SL distance × quantity × value-per-lot) exceeds the per-trade cap (the explicit USD figure from `risk_per_trade_usd` if set, else `risk_per_trade_pct × balance`), reject. An exception is made for the symbol's minimum lot — if the smallest possible position would still violate the cap, the order is allowed through with a warning so a too-tight risk percent does not starve the system.

### 7.3 Sub-components

The risk engine delegates to five sub-components, each implementing one focused concern:

- **`PositionSizer`** (`src/risk/position_sizer.py`) computes the optimal position size given the entry price, stop loss, and risk percentage. The default method is fixed-fractional (risk a fixed percentage of equity per trade). A "fixed-lot" mode is available and is the prop-firm-favoured mode: when `position_sizing.method: fixed_lot`, every trade uses the same lot size regardless of equity, which simplifies psychological discipline and avoids the equity-curve compounding that can over-leverage during a hot streak.
- **`KillSwitch`** (`src/risk/kill_switch.py`) is a one-shot latch backed by a JSON file (`data/state/kill_switch_alert.json`). Once tripped, `is_active()` returns true on every subsequent call until the file is manually deleted.
- **`CircuitBreaker`** (`src/risk/circuit_breaker.py`) is a sliding-window consecutive-loss counter. `record_trade(pnl)` increments or resets the counter; `is_trading_allowed()` returns `(False, reason)` if the counter is at the threshold and the cooldown has not elapsed.
- **`DrawdownTracker`** (`src/risk/drawdown_tracker.py`) keeps the equity high-water mark and computes the current drawdown relative to it. The HWM is intentionally not reset on a daily basis — it follows the equity curve over the lifetime of the system.
- **`ExposureManager`** (`src/risk/exposure_manager.py`) computes the current exposure per symbol and gates new orders that would breach the cap.

A separate `KellyCriterion` module (`src/risk/kelly.py`) is shipped but not currently used in the live path. The Kelly fraction (`f* = (p × b − q) / b`, where p is win rate, b is the reward-to-risk ratio, and q = 1 − p) is the optimal fraction of equity to risk per bet to maximise long-run growth — but it assumes accurate inputs, and the variance of small-sample win-rate estimates makes it unsafe for production. The module is kept for research backtesting.

### 7.4 Trailing stops

`src/risk/trailing_stop_manager.py` runs at 1 Hz (every fourth main-loop iteration; the throttle was added because per-tick polling multiplied bridge-error rates without changing SL behaviour). It implements a two-stage trailing model:

1. **Breakeven move.** When unrealised P&L on a position exceeds `breakeven_atr_mult × ATR(14)` of profit (default 1.2× ATR), the stop loss is moved to the entry price plus a token tick, locking in zero risk.
2. **Lock fraction.** When unrealised P&L exceeds `lock_atr_mult × ATR(14)` (default 2.0× ATR), the stop is moved to capture `lock_fraction` (default 50 %) of the unrealised gain.

The trailing manager also exposes an `ml_exhaustion_factor` knob: when the ML regime classifier reports a regime shift away from the position's bias, the trailing stop tightens by that factor. This lets the system protect profits when the regime turns against an open position.

### 7.5 Risk processor

`src/risk/risk_processor.py` is a thin layer between the signal generator and the risk engine. Its job is to take a raw `Signal`, build a candidate `Order`, and run it through `RiskEngine.validate_order()`. It also handles cluster-anchored stop-loss substitution for `continuation_breakout` (added 2026-04-30 alongside that strategy's enablement) — when the strategy emits a stop too close to the cluster boundary, the risk processor widens it to the cluster boundary plus an ATR buffer, so a bar wick into the cluster does not stop the trade out before the continuation has confirmed.

---

## 8. Position Sizing and Lot Allocation

The system supports three position-sizing modes:

**Fixed-fractional.** `risk_per_trade_pct × balance / SL_distance × value_per_lot` is the formula used by most retail systems. The lot size is whatever satisfies the equation. This mode is the default and is used in the smaller account configurations (`config_live_100.yaml`, `config_live_1000.yaml`).

**Fixed-lot.** Every trade uses a literal lot size from a per-symbol table (`risk.position_sizing.fixed_lots`). The active $10,000 config sets XAUUSD = 0.02, BTCUSD = 0.01, ETHUSD = 0.01, EURUSD = 0.01, default = 0.01. This mode trades simplicity for compounding: a winning streak does not increase the lot size, and a losing streak does not decrease it. The prop-firm advantage is that worst-case daily loss is a deterministic function of the lot size and the maximum SL distance, independent of equity.

**User-fixed lot from runtime_setup.** When the operator runs `scripts/runtime_setup.py` and types in a literal lot size, that value is written to the YAML config as `symbols.<TKR>.{min_lot, max_lot}` set to the same value. The position sizer treats this case as authoritative — the lot is used verbatim, with no exposure-cap substitution and no silent scaling. If a downstream check disagrees with the user's size, it must reject the order entirely; it cannot quietly resize it. This rule was added after a regression in which a user-selected 0.05-lot trade was silently downsized to 0.01 by the exposure cap, producing a much smaller stop than the user had budgeted for.

A **manual size multiplier** (default 0.5) halves the lot size for orders tagged `strategy: manual` in metadata. This was a journal-driven gate: the audit found that manual-clicked trades produced 89 % of total losses, so the system halves their size while the operator audits whether manual has any real edge.

---

## 9. Execution and Portfolio

### 9.1 The execution engine

`ExecutionEngine` (`src/execution/execution_engine.py`) takes a validated, sized order and sends it to MT5. It uses three sub-components:

- **`OrderManager`** (`src/execution/order_manager.py`) maintains the in-memory dictionary of active orders and transitions them through the eight-state lifecycle as fill notifications arrive.
- **`FillHandler`** (`src/execution/fill_handler.py`) parses fill notifications from MT5 and applies them to the portfolio.
- The `MT5Connector.place_order()` method itself, which constructs an MQL5 order payload as a JSON file and waits for the broker's response.

The execution engine is idempotent: if a network blip causes the system to re-send the same order, the MT5 EA recognises the order's UUID-based magic number and refuses to duplicate it. This is critical for prop-firm survival, where a duplicate order can blow through risk caps without warning.

### 9.2 The portfolio engine

`PortfolioEngine` (`src/portfolio/portfolio_engine.py`) owns the in-memory dictionary of open positions. It exposes:

- `add_position(position)` / `remove_position(position_id)` — lifecycle.
- `update_prices(prices)` — mark-to-market every position with the latest mid price; this is called every loop iteration.
- `get_total_pnl()` and `get_daily_pnl()` — aggregate P&L over the day or all-time.
- `reconcile(mt5_positions)` — compare the in-memory portfolio against MT5's truth and reconcile any drift.

Reconciliation runs every 30 seconds. The function compares the system's position dictionary against `MT5Connector.get_positions()` and logs any discrepancy. Three reconciliation cases are handled: a position the system thinks is open but MT5 has closed (resolves by closing it in-memory and recording the missed P&L), a position MT5 reports but the system does not know about (logged as a manual-trade event), and a position both know about but with diverging quantities or prices (logged and the MT5 truth is taken). The journal-CSV state and the MT5 state should converge to identical snapshots within at most one reconciliation cycle.

### 9.3 The trade journal

`TradeJournal` (`src/monitoring/trade_journal.py`) writes a CSV row per closed trade with timestamp, ticker, side, entry, exit, P&L, strategy, signal strength, and the metadata dictionary. The CSV is the system's analytical ground truth: backtest comparisons, per-strategy P&L attribution, and the audits that produced most of the recent parameter changes all read this file. A `view_journal.py` script renders the CSV in a terminal-friendly summary.

### 9.4 The PnL calculator

`PnLCalculator` (`src/portfolio/pnl_calculator.py`) is a pure-function helper that converts a position and a current price into realised and unrealised P&L. It is reused by the portfolio engine, the reconciler, and the live monitor. Centralising this calculation prevents the kind of subtle disagreement that arises when multiple modules reimplement the same Decimal-arithmetic formula.

---

## 10. State Management and Crash Recovery

The system can crash. Power can fail. The MT5 terminal can be closed by mistake. The OS can decide to install an update and reboot at 03:00 UTC. None of these can be allowed to leave the operator with an unknown position state. The state manager exists to make recovery from any of these scenarios deterministic.

`StateManager` (`src/state/state_manager.py`) periodically writes the `SystemState` dataclass to a JSON file under `data/state/{environment}/state.json`. The default save interval is 10 seconds; the saver runs in the main loop, so the worst-case state-loss window is ten seconds plus the in-flight tick. The file is written atomically (write to a `.tmp` sibling, then rename) so a crash during write cannot leave a partially-written state file.

`StateStore` (`src/state/state_store.py`) handles the JSON serialisation. Decimals are encoded as strings; UUIDs are encoded as strings; datetimes are encoded as ISO-8601 strings. The deserialiser reverses all three. This is the same encoding the trade journal uses, which means a state file is human-readable and a journal CSV row can be reconstructed from it (and vice versa).

On startup, the trading system always tries to restore. If `data/state/{env}/state.json` exists, the system reads it, populates the portfolio engine with the open positions, restores the equity high-water mark, the daily start equity, and the kill-switch / circuit-breaker flags. The system then immediately reconciles against MT5 — if the file says position X is open but MT5 has closed it, the close is recorded and the in-memory copy is removed. The audit log records every reconciliation discrepancy with full context.

A separate flag, `--reset-hwm`, is required to start the system after intentionally switching account size. Without it, a stale equity HWM from a $5K run would aborts startup on a $10K run, because otherwise the drawdown circuit breaker would silently start trading with the wrong reference point.

---

## 11. The Nightly Regime Classifier

### 11.1 The shape of the problem

Strategies have regime preferences, but markets switch regimes on irregular schedules. A pure rules-based regime detector (ADX > 25 → trend) flips back and forth on noisy days and cannot capture the higher-order structure (e.g. a multi-day VOLATILE regime around an FOMC week). The system handles this with a nightly machine-learning classifier (`scripts/regime_classifier.py`) that:

1. Aggregates 5-minute bars into daily OHLCV.
2. Computes a feature set (ADX, ATR%, BB-width ratio, close-vs-EMA20, 1- and 5-day momentum, range-to-ATR ratio, volume ratio, and a Parkinson-volatility z-score).
3. Auto-labels each historical day as TREND, RANGE, or VOLATILE based on the *next* day's price action: TREND if the next-day move exceeds 1.2 × today's ATR; VOLATILE if today's range exceeded 2 × ATR but the next-day net move was below 0.8 × ATR; RANGE otherwise.
4. Trains a `RandomForestClassifier` on the (features, label) pairs.
5. Applies the classifier to today's features to get a prediction probability over the three regimes.
6. Smooths the prediction with a 3×3 Markov transition matrix learned from the historical label sequence.
7. Combines the smoothed probability with the static `STRATEGY_WEIGHTS` table to produce a per-strategy weight in the predicted regime.
8. Adjusts the weights based on each strategy's recent live P&L (an "RL-lite" feedback loop): strategies that have been profitable in the last 30 days get a small upward weight nudge; strategies that have been losing get a small downward nudge.
9. Writes the resulting weights to `data/config_override_{TICKER}.json` with a 24-hour validity window.

The trading loop reads this override at startup and at every UTC midnight via `_apply_regime_override()`. Strategies whose weight is below the confidence threshold (0.40) are silently disabled until the next override.

### 11.2 The Markov smoother

A pure RandomForest classifier trained on daily features tends to flip-flop on borderline days. To dampen this, the classifier learns a 3×3 Markov transition matrix from the historical label sequence (with Laplace smoothing to avoid zero probabilities) and blends today's prediction with the smoothed prior:

```
P_smooth(today) = α × P_RF(today) + (1 − α) × Σ_y P_RF(yesterday=y) × P(today | yesterday=y)
```

with α = 0.7 by default. This makes the classifier resistant to single-day outliers without locking it into yesterday's regime.

### 11.3 The intraday shift check

A nightly classifier is too slow for the case where a regime shifts mid-session (a sudden FOMC dovish surprise, for example). The trading loop also runs an `_check_intraday_regime_shift()` every 4 hours; this is a lighter-weight rules-based check that compares the current ADX, ATR%, and close-vs-EMA20 against simple thresholds and updates the in-memory regime if the current session looks materially different from the nightly prediction. The intraday check can override the nightly one for the rest of the session but does not write to the override file — that file is rewritten only at midnight.

### 11.4 Performance feedback

`scripts/strategy_scorer.py` computes a per-(strategy, regime) score from the trade journal — typically a simple expectancy or profit factor computed over the trades that fired in each regime. The classifier reads this score and applies a small adjustment to the static weights: strategies whose live performance over the last 30 days was profitable get a +0.05 nudge; strategies that were losing get a −0.05 nudge. This is intentionally a small adjustment — the system trusts the backtest priors more than the noisy 30-day production sample, but allows live data to nudge the weights when the signal is consistent.

---

## 12. The MT5 File Bridge

### 12.1 Why a file bridge?

MetaTrader 5 has no public Python API on macOS or Linux. The official `MetaTrader5` Python module exists only on Windows. To run on a Mac, the system must either (a) require the user to run MT5 in a Wine-hosted Windows environment and use the Windows Python module from inside Wine, or (b) run native Python on the host OS and communicate with MT5 across the OS boundary. The system chose (b) and implemented the cross-OS bridge as a directory of small JSON files.

The MT5 *Common/Files* directory is the same on every install and is shared by every running terminal on the same machine. The Python side writes a command JSON file (e.g. `cmd_<uuid>.json` containing a `place_order` payload), the MT5 EA running in MT5 polls this directory at 100 ms intervals, executes the command via the standard MQL5 trading functions, and writes a response JSON file (`resp_<uuid>.json`). The Python side polls for the response and parses it. The latency of a round-trip is consistently 200–500 ms in steady state.

### 12.2 The components

**`mt5_bridge/EA_FileBridge.mq5`** is the Expert Advisor running inside MT5. It is approximately 1,200 lines of MQL5 code. Its responsibilities:

- Heartbeat: write a `status.json` file every 100 ms with the current bid/ask quotes for every subscribed symbol, plus the broker's server time, the current account balance and equity, the list of open positions, and a UTF-16-LE-encoded "ALIVE" tag. The Python side uses this file to confirm the EA is responsive.
- Command polling: scan the *Common/Files* directory for any `cmd_*.json` file, parse it, dispatch to the corresponding handler (`heartbeat`, `get_quote`, `get_positions`, `get_history_deals`, `place_order`, `modify_position`, `close_position`, `copy_rates`), and write a `resp_*.json` response file.
- Safety controls: respect the MT5 trading-allowed flag, refuse orders during news blackouts (an EA-side defence in depth, since the Python side also enforces them), and emit a panic-close command if a hard kill-switch flag file appears.

**`mt5_bridge/mt5_file_client.py`** is the Python client. It is a few hundred lines of synchronous, file-system-driven RPC. The class exposes methods like `heartbeat()`, `get_quote(symbol)`, `place_order(...)`, etc.; each method writes a command file, polls for the response, and times out after a configurable interval (default 3 seconds). On timeout, the method raises `OrderTimeoutError`, which the connector translates into a typed exception that the trading loop can catch.

**`src/connectors/mt5_connector.py`** is the wrapper that converts file-client output into the system's typed objects (`Tick`, `Position`, `Order`). It also handles the broker-timezone detection mentioned in Section 3, a heartbeat-based reconnect loop, and a symbol cache.

### 12.3 Cross-platform path resolution

The MT5 *Common/Files* directory lives in different places on different operating systems:

- Windows: `%APPDATA%\MetaQuotes\Terminal\Common\Files`
- macOS (Wine via CrossOver): `~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files`
- Linux (native Wine): `~/.wine/drive_c/users/<user>/AppData/Roaming/MetaQuotes/Terminal/Common/Files`

`mt5_file_client.py` exposes `_get_default_mt5_path()` that auto-detects the directory by OS. The YAML config can override it (`file_bridge.data_dir`), but the default is correct on every supported platform. This is the reason the same code base, with no environment variables, can run on a $1,000 Mac and a $200 Windows mini-PC.

### 12.4 Encoding quirks

The status file is written by MQL5's `FileWriteString`, which emits UTF-16-LE by default. The Python side reads it with `open(..., encoding='utf-16-le')`. A bug existed for a brief period in which the Python side assumed UTF-8 and silently dropped every other character; a regression test now hashes the first kilobyte of the status file and asserts the expected encoding, so a future MQL5 update cannot reintroduce the bug.

---

## 13. The Configuration System

### 13.1 The YAML files

Every parameter the system needs to run live is in a single YAML file under `config/`. The active file is selected by the `--config` flag at startup. The naming convention is `config_live_{account_size}.yaml`, and the repository ships seven of them (100, 1000, 5000, 10000, 25000, 50000) plus the unsuffixed `config_live.yaml`. Risk parameters are tuned per account size: a $100 account uses 0.10-lot equivalents and a 25 % daily-loss budget, while a $50,000 account uses 0.10-lot equivalents and a 1 % daily-loss budget. The strategies are the same; only the dollar caps and lot sizes scale.

The configuration covers, in order: `environment` (live, paper, dev), the `account` block (initial balance, currency, leverage), the `symbols` block (one entry per tradeable instrument with all the `Symbol` fields), the `risk` block (every cap and threshold), the `strategies` block (one entry per strategy with `enabled`, `timeframe`, and strategy-specific parameters), the `trading_hours` block (the per-session whitelists of which strategies fire when), the `monitoring` block (log level, alerts), the `data` block (timeframes to track, history length), the `portfolio` block (reconciliation interval), the `file_bridge` block, and the `shutdown` block.

### 13.2 Runtime overrides

Two override files modify behaviour without rewriting the active config:

- **`config/runtime_overrides.yaml`** is written by `scripts/runtime_setup.py`, an interactive wizard that prompts the operator for which symbols to trade, what lot size to use, and what daily-profit and per-trade-risk caps to apply. The overrides deep-merge into the loaded config at startup. This lets an operator change the lot size for a single session without permanently modifying the YAML.
- **`data/config_override_{TICKER}.json`** is written by the nightly regime classifier and contains the per-strategy weights for the predicted regime. This file is read only by `_apply_regime_override()` and only affects strategy weights, not other configuration.

### 13.3 Per-session whitelists

The `trading_hours.sessions` array is the most operationally important section of the config. Each session entry has `name`, `start`, `end`, `enabled`, `lot_size_multiplier`, and `strategies`. The `strategies` list is a whitelist: a strategy not present in the current session's list is skipped silently in the trading loop, even if its `enabled: true` flag is set. The Asia session whitelists `[asia_range_fade, vwap, fibonacci_retracement, mini_medallion]` — a quiet handful suited to the low-volatility window. The London/NY overlap whitelists eleven strategies — every active strategy fires during peak liquidity. The late-NY session is disabled entirely (`enabled: false`) because empirical results showed wide spreads and thin liquidity producing negative expectancy.

### 13.4 What the configs are not

The YAML files do not contain credentials, API keys, broker passwords, or any secret. The system does not authenticate to MT5 — that is the operator's responsibility, performed once when the MT5 terminal logs in. The bot communicates with MT5 over the local file bridge, which inherits the operator's broker login. This means the bot cannot be a shared cloud service; it must run on the same machine as MT5. The trade-off is that secret leakage is an operational concern at the OS level, not a code concern.

---

## 14. Backtesting and Validation

### 14.1 The backtest engine

`src/backtest/backtest_engine.py` is an event-driven backtester that uses the *same strategy code* that runs in production. The engine ingests historical bars in chronological order, calls `strategy.on_bar(bars_so_far)` once per bar, validates each emitted signal through a backtest-mode `RiskEngine`, simulates fills with a configurable slippage model, updates a virtual portfolio, and emits a `BacktestResult` at the end. The result includes the equity curve, total return, annualised Sharpe ratio, profit factor, maximum drawdown, win rate, total trade count, average win/loss, and a per-strategy P&L attribution.

The engine handles cross-day boundaries by resetting daily counters at midnight UTC, which mirrors the live behaviour. In the audit-v2 "Enforced Regime" mode, the regime classifier is allowed to write per-strategy weights as if the backtest were a real production run; this is how the system measures whether the regime classifier itself adds value. In the audit-v3 "Budget" mode, the per-trade USD risk is held constant across all strategies, isolating the strategy's contribution from the position-sizer's choices.

### 14.2 The simulation layer

`src/backtest/simulation.py` handles the broker-simulation primitives: bar-by-bar fill detection (a stop-loss is hit if the bar's low ≤ SL; a take-profit is hit if the bar's high ≥ TP, with the more conservative of the two assumed first when both are touched on the same bar), spread modelling (a constant spread of 30 points by default; per-symbol overrides supported), and slippage modelling (a normal distribution with configurable mean and standard deviation). These primitives are unit-tested separately from the engine.

### 14.3 Walk-forward validation

`src/validation/walk_forward.py` and `src/backtest/walk_forward.py` implement walk-forward optimisation: split the historical data into a training window and a holdout window, optimise parameters on the training window, evaluate on the holdout, then slide both windows forward and repeat. The result is a sequence of out-of-sample backtest results that approximate the parameter set's true performance, free from the in-sample overfit that single-window optimisation produces. Most strategy parameters in production were chosen by walk-forward grid search on 2 years of 5-minute gold data.

### 14.4 Monte-Carlo validation

`src/validation/monte_carlo.py` runs N (default 1,000) bootstrap resamples of a backtest's trade sequence to estimate the distribution of total returns. The 5th- and 95th-percentile of the resampled returns are reported as the lower and upper bounds. A strategy whose 5th-percentile is positive is genuinely robust; a strategy whose 5th-percentile is negative is on the edge and one bad streak away from a losing period. This check is currently advisory; it is not a deployment gate.

### 14.5 Optimisation

The optimisation package supports two methods:

- **Bayesian** (`src/optimization/bayesian.py`) wraps `scikit-optimize`'s Gaussian-Process minimiser. It is used when the parameter space is small (<10 dimensions) and each backtest is cheap (<30 seconds).
- **Genetic** (`src/optimization/genetic.py`) implements a lightweight evolutionary search with crossover and mutation. It is used when the parameter space is larger or includes categorical choices.

Both optimisers minimise a configurable objective (negative Sharpe, negative profit factor, maximum drawdown, etc.) and report the best parameter set plus the full optimisation trace.

### 14.6 The audit pipeline

A particularly load-bearing piece of the validation workflow is the *audit pipeline*: a script that reads the live trade journal, attributes each trade to its originating strategy, computes per-strategy and per-(strategy, regime) statistics, and emits a Markdown report. The reports under `reports/audit_v2_*` and `reports/audit_v3_*` are how the system catches misbehaving strategies before they cause real damage. The Mini-Medallion v1 was disabled in production after the audit revealed a 0.84 profit factor; the v2 was re-enabled after backtest-validated parameter changes lifted the profit factor to 1.31 in a fresh 12-month sample.

---

## 15. Monitoring and Journaling

### 15.1 Structured logging

`src/monitoring/logger.py` configures a `structlog`-based logger that emits one JSON document per log event. Every log line carries the timestamp, the log level, the calling module, the message, and any keyword arguments. The output is consumed both by tail-following operators (the JSON is colourised by an `analyze_logs.py` companion script) and by the daily audit pipeline (which parses the JSON for trade events and signal rejections).

The system uses log levels conventionally: DEBUG for noisy per-tick events, INFO for trade events and per-bar strategy outputs, WARNING for risk vetoes and bridge errors, ERROR for crashes and failed reconciliations, and CRITICAL for kill-switch trips and drawdown breaches.

### 15.2 The trade journal

`src/monitoring/trade_journal.py` (covered above in Section 9) writes a CSV row per closed trade. The CSV is the single source of truth for analytical queries. The companion `view_journal.py` script provides a terminal UI that summarises win rate, profit factor, max drawdown, and per-strategy attribution.

### 15.3 The performance dashboard

`src/monitoring/performance_dashboard.py` renders a console summary every five minutes. The summary shows the day's running P&L, the current open positions with their entry prices and unrealised P&L, the drawdown from the high-water mark, the kill-switch status, and the last few signals (whether they were accepted or vetoed). The dashboard is text-only; it does not require a graphical environment.

### 15.4 The live monitor

The live-monitor pop-up (`src/monitoring/live_monitor_emitter.py`) is a more polished UI that runs as a separate process. The emitter writes a JSON snapshot of the system state to `data/metrics/live_monitor_state.json` once per second; the consumer (`scripts/live_monitor.py`) reads the snapshot and renders a Rich-based terminal application with scrollable news, scrollable positions, a P&L sparkline, and a status banner. The two processes communicate only via the file; they share no memory and no Python interpreter. If the consumer crashes, the trading loop is unaffected. If the trading loop crashes, the consumer surfaces the last snapshot and a "STALE" badge.

### 15.5 Manual position tracker

`src/monitoring/manual_position_tracker.py` watches MT5 for positions that the bot did not open. Manual trades are detected by checking whether the position's `magic` field matches any of the bot's strategy magics; if not, the position is flagged as manual and the directional-lock logic in the executor refuses to open a bot position in the opposite direction. This prevents the operator from accidentally hedging against the bot.

### 15.6 Manual trade monitor

`src/monitoring/manual_trade_monitor.py` runs once every 60 main-loop iterations (~15 seconds). It applies the *same* risk gates to manual trades that the risk engine applies to bot orders: manual SL must be present, manual position size must respect the per-trade USD cap, manual trades during a blackout hour are flagged. If `auto_close_violations: true` is set, the monitor closes the violating manual position; by default, it only logs and emits a warning to the live monitor. This module is the operational expression of the rule that *every* trade — bot or manual — must respect the prop-firm caps.

---

## 16. Testing Strategy

### 16.1 Unit tests

`tests/unit/` contains around forty unit-test files, one per major module. Every test mocks the MT5 bridge so the suite runs without a live MT5 connection. The conventional test list:

- `test_strategies.py` and the per-strategy test files (`test_breakout.py`, `test_kalman_regime.py`, `test_mini_medallion.py`, `test_continuation_breakout.py`, etc.) construct synthetic OHLCV data and assert that the strategy emits the expected signal under specific conditions.
- `test_risk_engine.py` walks the sixteen risk checks, asserting that each one rejects the order it should reject and accepts the order it should accept.
- `test_indicators.py` checks every indicator against a hand-computed reference value on a small synthetic series.
- `test_position_sizer.py` checks that the sizing math is correct in fixed-fractional, fixed-lot, and user-fixed-lot modes.
- `test_state_manager.py` round-trips a full `SystemState` to JSON and back.
- `test_data_engine.py` feeds a sequence of ticks and asserts that the resulting bars are correctly aggregated across timeframes.
- `test_regime_classifier.py` includes a `test_weights_table_completeness` check that hard-codes every expected strategy key in a `required_core` set; the test fails CI if a new strategy is added without a corresponding entry in the `STRATEGY_WEIGHTS` table.

The unit suite runs in roughly thirty seconds and is intended to be run before every commit.

### 16.2 Integration tests

`tests/integration/` contains tests that talk to a live MT5 instance. The test entry points include `test_mt5_direct.py` (round-trip a heartbeat through the bridge) and `test_mt5_connector_integration.py` (open and close a position on a demo account). These tests are gated by a pytest marker (`pytest -m integration -v`) so the unit suite does not require MT5.

### 16.3 The pytest configuration

`pytest.ini` sets `pythonpath = .` so imports resolve from the repo root; `conftest.py` provides shared fixtures for synthetic OHLCV data, mocked MT5 connectors, and a fast `risk.engine` instance with sensible defaults. The conftest also marks integration tests automatically based on file location.

### 16.4 Coverage philosophy

The codebase does not target a specific test-coverage percentage. The bias is toward testing the parts where bugs would be expensive: the risk engine, the position sizer, the state manager, and the strategies. Pure data-pipeline plumbing (the candle store, the tick handler) has fewer tests on the assumption that bugs there manifest as obvious data corruption visible in the live monitor.

---

## 17. Deployment and Operations

### 17.1 The pre-flight check

`scripts/health_check.py` is the operator's pre-session ritual. The script runs a sequence of assertions and prints `✅ PASS` or `❌ FAIL` for each:

1. The active YAML config is parseable and contains the required keys.
2. The MT5 *Common/Files* directory exists and is writable.
3. The MT5 EA is running and responding to a heartbeat.
4. The configured symbols are tradeable on the broker (the EA returns valid quotes).
5. The required Python packages are importable.
6. The most recent state file is readable, and the equity HWM in the file is consistent with the current MT5 equity (within 0.1 %).
7. The news-filter CSV for today is present and parseable.
8. The drawdown HWM and the daily-loss cap are consistent (HWM × max_drawdown_pct ≥ daily_loss_cap, otherwise the daily-loss cap is unreachable).

If any assertion fails, the operator is expected to fix the underlying issue before starting the bot. The health check is the cheapest defence against a misconfiguration that would otherwise blow up at the first signal.

### 17.2 The launch script

`scripts/start_live.sh` (Unix) and `scripts/start_live.bat` (Windows) wrap the `python src/main.py` invocation with a few defensive layers:

- Activate the virtual environment.
- Run the nightly regime classifier (so the override file is fresh on first launch of the day).
- Print a confirmation banner including the active config, the account size, and the per-trade USD risk.
- Prompt the operator for an explicit "YES" before continuing.
- Log the start event to a launch log so post-mortem analyses can correlate the trade journal with operator actions.
- Forward `Ctrl+C` to the Python process so the trading system gets a clean SIGINT and can save state and close positions cleanly.

The Windows launchers also double as one-click installers when the venv does not yet exist; they create it, install requirements, and launch in a single double-click.

### 17.3 Cleanup utilities

`scripts/force_cleanup.py` is the nuclear option for stuck Python processes. It uses `psutil` (or a POSIX `pgrep` fallback when psutil is unavailable) to find any `python.*main.py` process and terminate it. The script is invoked when a previous run did not clean up and the new run cannot acquire the state-file lock.

`scripts/clean_state.py` deletes the state file under `data/state/`. This is a destructive operation and is only invoked when the state file is corrupted; after running it, the next launch starts from a blank slate, but with the kill switch still tripped if the alert file exists.

`scripts/reset_state.py` is a softer reset: it leaves the kill-switch alert in place, but resets daily counters and the equity HWM to current values. This is what the operator runs after deliberately switching account size.

### 17.4 The cron schedule

The system relies on a small set of OS-level cron jobs:

- `0 0 * * *` — `python scripts/regime_classifier.py`. Writes the override file at midnight UTC.
- `30 0 * * *` — `python scripts/fetch_daily_news.py`. Downloads today's ForexFactory CSV at 00:30 UTC (after the day's events are published).
- `0 23 * * *` — `python scripts/strategy_scorer.py --update`. Refreshes the per-(strategy, regime) score from the trade journal at 23:00 UTC.

These three jobs are the only out-of-process automation in the system; everything else runs inside `main.py`.

---

## 18. Empirical Lessons

This section collects the lessons that production has taught the system. Each one corresponds to a code change that exists in the current repository.

**Lesson 1 — Hour blackouts are real.** A 145-trade audit revealed that hours 14–16 UTC lost $196 of $400 net P&L. Every strategy that fired in those hours was a net loser. The hour-blackout config (`risk.trading_windows`) was added; net daily P&L improved measurably the next month. This has since been generalised into per-strategy session whitelists, which collectively drop ~40 % of would-be signals.

**Lesson 2 — Manual trades are 89 % of losses.** The same audit found that of the −$400 net, −$358 was attributable to operator-clicked manual trades. The manual-guard logic (size-halving, daily loss cap, directional lock vs bot positions) was added. Manual trading has not been disabled outright because it is operationally useful for emergencies; the guard ensures it cannot bleed the account.

**Lesson 3 — Mean reversion does not work on gold.** Pure mean-reversion (`mean_reversion_strategy.py`) was disabled in every config after a fresh backtest produced PF 0.40, 15 % WR, 39 trades. The strategy is retained as a baseline benchmark.

**Lesson 4 — Tighter circuit breakers improve outcomes.** The original 5-loss / 60-minute breaker was tightened to 3-loss / 30-minute after a simulation showed that 3 consecutive losses on gold has a 70 % probability of being followed by a 4th, and that the 60-minute cooldown was operationally indistinguishable from "ignore the breaker for the rest of the session." The 30-minute cooldown is short enough to come back online during the active session.

**Lesson 5 — Pre-trade budgets beat reactive limits.** The reactive daily-loss cap (Check 05) is essential as a final backstop, but it fires after the breach. The pre-trade budget (Check 06) computes "if this trade hits its SL, will we breach?" and rejects orders prospectively. This converts the daily-loss limit from a prop-firm fail condition into a non-event in normal operation: the budget gate denies the trade that would have caused the breach, the risk engine logs the rejection, and the day continues.

**Lesson 6 — Decimal everywhere or nothing works.** A bug in an early version used `float` for price arithmetic in the position sizer. After 200 trades, the in-memory P&L disagreed with the broker by $4.17 — well within tolerance for a hobby project, but unacceptable for a system that has to respect a $145 daily-loss cap exactly. Every monetary field in the codebase is now `Decimal`.

**Lesson 7 — The MT5 broker timezone is not UTC.** Most MT5 brokers run their server clock at UTC+2 or UTC+3, but stamp bar timestamps as if the server clock were UTC. A daylight-saving change can move the offset without warning. The connector now auto-detects the offset on every connect by reading the broker's `server_time` field and computing `broker_time - utc_now` rounded to the nearest hour. This eliminated an entire class of "the bar is one hour off" bugs.

**Lesson 8 — Fixed-lot beats fractional for prop-firm survival.** Fixed-fractional sizing produces a feedback loop: a winning streak grows the equity, the next position sizes up, a small drawdown from the bigger position eats the streak's gain, and the operator's psychology is destroyed. Fixed-lot sizing breaks the loop: every trade risks the same dollar amount regardless of equity. This decouples the lot size from the streak and from the operator's emotional state. The five live configs above $5,000 all use `fixed_lot`.

**Lesson 9 — Regime classification beats binary on/off.** The pre-classifier era had each strategy enabled or disabled in the config. The classifier replaced this with continuous weights in [0, 1] per regime, and a confidence threshold (0.40) that disables a strategy in a regime where the model has low conviction. This added ~12 % to annualised return in the audit period without raising drawdown.

**Lesson 10 — News blackouts must be cheap.** The first news filter pulled the full ForexFactory calendar on every loop iteration; the resulting HTTP latency stalled the loop. The current filter loads the day's CSV once at startup (and once per day at 00:30 UTC) and does an O(1) interval lookup per signal. The filter is now negligibly cheap and can be invoked at every signal without performance impact.

**Lesson 11 — Re-enabling a strategy requires walk-forward, not in-sample.** Mini-Medallion v1 produced a beautiful 1.4 PF in single-window backtest and lost money in production. The current re-enablement criterion is a walk-forward Sharpe > 1.0 across at least four sliding windows of 6+ months each. Every strategy currently `enabled: true` in the live config has cleared this bar.

**Lesson 12 — The kill switch is one-way for a reason.** An auto-clearing kill switch was tried in a March 2026 experiment. The first time it tripped (a malformed broker quote that briefly reported a $4,000 gold price), the system auto-cleared the switch and immediately tried to open another position based on the same garbage data. The auto-clear was reverted permanently. Recovery now requires manually deleting the alert file, which forces the operator to read the log and confirm the trip was spurious before re-enabling.

**Lesson 13 — A test gate beats a code review.** The `test_weights_table_completeness` test was added after a regression where a new strategy was added to the live config but its `STRATEGY_WEIGHTS` entry was forgotten in the regime classifier; the strategy ran with weight 0 in every regime and never fired. The test now hard-codes the expected strategy keys and fails CI if any are missing. This is one of the cheapest, highest-value tests in the suite.

---

## 19. Limitations and Future Work

### 19.1 Single-instance constraint

The system is a single Python process and cannot be sharded. Running two copies on the same MT5 terminal would cause both to issue overlapping orders, since the file-bridge protocol uses UUIDs as deduplication keys but does not coordinate between Python instances. A future migration to a small SQLite (or DuckDB) state store with row-level locking would allow two instances on the same machine, useful for running independent strategy stacks (e.g. one trend-following and one mean-reverting) without code changes. This is on the roadmap but not yet a priority — the practical alternative is to run multiple symbols in the same instance, which the current architecture already supports.

### 19.2 No live order-book

The system has access only to top-of-book quotes (best bid, best ask). Many of the alpha signals (Mini-Medallion's order-flow imbalance, smc_ob's liquidity sweep) would benefit from depth-of-market data. MT5 exposes a Level-II feed for some symbols, but the file-bridge protocol does not currently surface it. Adding L2 support would require an EA-side handler and a Python-side parser; the latency budget is already thin (200–500 ms round-trip) and L2 streams would push it.

### 19.3 No futures or options

The system trades spot only. Adding futures would require a different position model (margin requirements, contract rollovers, settlement). Adding options would require a Greeks engine (delta, gamma, vega, theta) and a different risk framework — option position sizing is dominated by the Greeks rather than the underlying price.

### 19.4 The classifier has no online learning

The nightly classifier retrains from scratch every midnight. A streaming classifier (e.g. an online RandomForest or a simple Bayesian update over the existing tree) would converge faster on regime shifts, at the cost of a more complex code path and the new failure mode of "the model has overfit on the last week of bad data." The current daily retrain has been adequate; this is on the roadmap as a research experiment, not an operational change.

### 19.5 No automatic post-trade review

The audit pipeline is run manually. Automating it (a cron job that emits a Markdown report to a Slack channel every Sunday) is straightforward and on the roadmap. The reason it has not yet been done is that the operator currently runs the audit interactively after each prop-firm session; the friction of the manual run is itself useful as a forcing function for review.

### 19.6 No multi-broker support

The system speaks only to MT5. Migration to MT4 (the still-popular older terminal) or to native broker APIs (e.g. cTrader, IBKR) would require a new connector. The connector interface (`MT5Connector`) is simple — quote, position, order, history-deal — and an alternative implementation would be a few hundred lines per broker. This has not been done because the prop firms targeted by the system all use MT5.

### 19.7 The strategy mix is gold-centric

Every strategy was tuned on gold (XAUUSD). The same code runs on BTC, ETH, and EUR/USD because the strategy interface is symbol-agnostic, but the parameters are not optimal for those symbols. Per-symbol parameter tuning (a separate config block per symbol per strategy) is on the roadmap; it would require rerunning the walk-forward optimisation pipeline on each symbol.

### 19.8 No GPU

The Kalman filter, the OU mean-reversion model, and the RandomForest classifier all run on CPU in single-thread mode. None is currently a bottleneck; the trading loop is dominated by the 250 ms sleep, not by compute. If the strategy mix grows to 50+ concurrent strategies, GPU acceleration would matter; at 13 strategies, it does not.

---

## 20. Conclusion

This system is, at its core, a piece of conservative engineering. It is not a research vehicle for new alphas. It is not a low-latency arbitrage box. It does not attempt to predict markets. It is an operational layer that takes a small number of empirically-validated trading strategies, runs them under a defensive risk regime that makes prop-firm rule violations close to impossible, and persists every state mutation so a power outage cannot lose trading state. The system's competitive advantage is that it works the same way every day, on the same hardware, with the same logs, and that every decision it makes — every trade, every veto, every regime flip — is auditable after the fact.

The strategies themselves are interesting but ordinary; their parameters were chosen by walk-forward backtest, not by intuition. The risk engine is the part of the system that creates persistent value: by gating every order through sixteen sequential, single-purpose checks, and by making the kill switch a one-way latch, the system makes a category of catastrophic outcomes operationally impossible. The state manager is the part that makes the system survivable: a crash at 03:00 UTC produces a recoverable state file at 03:00 UTC, and the system resumes at 03:01 UTC with no human intervention. The MT5 bridge is the part that makes the system portable: the same code runs on a Mac, a Windows mini-PC, and a Linux server, because the bridge speaks file system, and file system is the same everywhere.

The system has been in continuous live operation since early 2026. The first 145-trade audit drove ten of the parameter changes documented above. The second audit, running concurrently with this paper, will drive the next ten. The pattern is the same one that runs through the codebase: ship, observe, audit, change, and write down what was learned so the next change is easier than the last.

---

## 21. References (selected)

This list is not exhaustive; it covers the primary sources whose ideas appear in the system.

- Box, G. E. P., Jenkins, G. M., & Reinsel, G. C. (2008). *Time Series Analysis: Forecasting and Control* (4th ed.). Wiley. [The autoregressive backbone of the OU mean-reversion model.]
- Hamilton, J. D. (1994). *Time Series Analysis*. Princeton University Press. [Markov-switching regime models, used in the regime classifier.]
- Kalman, R. E. (1960). "A New Approach to Linear Filtering and Prediction Problems." *Journal of Basic Engineering*, 82(1), 35–45. [Foundational Kalman filter; used directly in `kalman_regime_strategy`.]
- Kelly, J. L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4), 917–926. [Kelly criterion; implemented in `src/risk/kelly.py` for research only.]
- Knuth, D. E. (1997). *The Art of Computer Programming, Volume 1: Fundamental Algorithms* (3rd ed.). Addison-Wesley. [General-purpose engineering reference; influences much of the codebase.]
- Lo, A. W. (2002). "The Statistics of Sharpe Ratios." *Financial Analysts Journal*, 58(4), 36–52. [The annualisation logic in the backtest engine.]
- Parkinson, M. (1980). "The Extreme Value Method for Estimating the Variance of the Rate of Return." *Journal of Business*, 53(1), 61–65. [The Parkinson volatility estimator used as a regime feature.]
- Pedregosa, F., et al. (2011). "Scikit-learn: Machine Learning in Python." *Journal of Machine Learning Research*, 12, 2825–2830. [The RandomForest implementation used in the regime classifier.]
- Simons, J. (n.d.). The Medallion Fund (Renaissance Technologies). [Multi-signal-composite philosophy informed Mini-Medallion.]
- Shvartsman, M. (n.d.). *Smart Money Concepts* (Inner Circle Trader). Online materials. [The order-block / FVG / liquidity-sweep state machine in `smc_ob_strategy`.]
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*. Trend Research. [Wilder's smoothing for ATR, ADX, and RSI; used throughout the indicator library.]

---

*End of paper.*
