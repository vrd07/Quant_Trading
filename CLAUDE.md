# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A production-grade quantitative trading system targeting XAUUSD (Gold) on MetaTrader 5, designed for The5ers prop firm challenge ($5,000 account). Features institutional-grade risk management, multi-strategy signal generation, and crash recovery.

## Commands

**Setup:**
```bash
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
pip install -r requirements.txt
```

**Testing:**
```bash
pytest                                              # All tests
pytest tests/unit/ -v                              # Unit tests only (no MT5 needed)
pytest tests/unit/test_strategies.py::TestClass -v -s  # Single test class
pytest -m "not integration"                        # Skip MT5-dependent tests
pytest -m integration -v                           # Integration tests (requires MT5)
```

**Running:**
```bash
python src/main.py --config config/config_live_5000.yaml   # Live trading
python scripts/health_check.py --config config/config_live_5000.yaml  # Pre-flight check
python scripts/run_backtest.py --symbols XAUUSD --period 2y
python scripts/view_journal.py                    # Trade history
python scripts/mt5_dashboard.py                   # Live dashboard
```

## Architecture

### Entry Point

The system starts from `src/main.py`. `TradingSystem.setup()` wires the MT5 connector, DataEngine, StrategyManager, RiskEngine, ExecutionEngine, PortfolioEngine, and StateManager from the YAML config; `TradingSystem.run()` is the main event loop (tick ingestion → strategy evaluation → risk veto → execution → portfolio update). A nightly regime classifier and intraday regime-shift check can rewrite strategy weights while the loop is running.

```
python src/main.py --config config/config_live_10000.yaml
```

### Data Flow

```
MT5 Terminal → EA_FileBridge.mq5 (file I/O) → MT5Connector
→ DataEngine (ticks → multi-TF bars → indicators)
→ StrategyManager (12 strategies emit Signals)
→ RiskEngine (VETO POINT: kill switch, drawdown, sizing)
→ ExecutionEngine → MT5 → Market
→ PortfolioEngine → TradeJournal + StateManager
```

### Module Structure (`src/`)

| Module | Responsibility |
|--------|---------------|
| `connectors/` | MT5 file bridge wrapper, heartbeat monitoring |
| `core/` | Shared types (`Tick`, `Bar`, `Signal`, `Position`), enums, exceptions |
| `data/` | DataEngine orchestrates tick buffering, candle building (1m–1d), indicator calc |
| `strategies/` | Stateless signal generators; StrategyManager aggregates them |
| `risk/` | RiskEngine has final veto; PositionSizer uses ATR-based dynamic sizing |
| `execution/` | Order lifecycle, fill processing |
| `portfolio/` | Position tracking, P&L, MT5 reconciliation |
| `state/` | Crash recovery via periodic state serialization |
| `monitoring/` | JSON-structured logging, trade journal, performance dashboard |
| `backtest/` | Historical simulation framework |

### Strategies

All strategies live in `src/strategies/`, inherit `BaseStrategy`, and are orchestrated by `StrategyManager`. Each one is independently toggled via `strategies.<name>.enabled` in the active config, and regime-adaptive weights rewrite their influence per detected market regime.

| # | Strategy | File | Summary |
|---|----------|------|---------|
| 1 | **KalmanRegime** | `kalman_regime_strategy.py` | Kalman-filter trend-follow in trending regime, OU z-score mean-reversion in ranging regime |
| 2 | **Breakout** | `breakout_strategy.py` | Donchian channel breakout with multi-timeframe confirmation |
| 3 | **MeanReversion** | `mean_reversion_strategy.py` | OU z-score entries at extremes (|z| > 2.0) |
| 4 | **Momentum** | `momentum_strategy.py` | Short-term ROC with ADX confirmation |
| 5 | **VWAP** | `vwap_strategy.py` | Deviation from 30-period VWAP |
| 6 | **MiniMedallion** | `mini_medallion_strategy.py` | 10 weak alpha signals combined into a composite score (threshold ±3.0) |
| 7 | **StructureBreakRetest** | `structure_break_retest.py` | Donchian break → retest of broken level → rejection candle; tighter SL than raw breakout |
| 8 | **FibonacciRetracement** | `fibonacci_retracement_strategy.py` | Pullbacks into the 50%–61.8% Golden Zone with rejection-candle confirmation |
| 9 | **DescendingChannelBreakout** | `descending_channel_breakout_strategy.py` | Linear-regression channel + Higher-Low structure shift → breakout of upper trendline |
| 10 | **SMCOrderBlock** | `smc_ob_strategy.py` | 5-phase ICT order-block state machine (OB formed → touched → sweep → waiting-entry → fire) |
| 11 | **SupplyDemand** | `supply_demand_strategy.py` | First retest of a fresh ATR-sized S/D zone after an impulse candle (currently disabled live) |
| 12 | **AsiaRangeFade** | `asia_range_fade_strategy.py` | Range-fade for the UTC 09–14 low-volatility window with BB compression + RSI extremes |

Support modules alongside the strategies:
- `base_strategy.py` — abstract base class
- `strategy_manager.py` — per-symbol, per-strategy instance registry + aggregation
- `regime_filter.py`, `multi_timeframe_filter.py` — shared gating helpers

All strategies emit `Signal` objects; `RiskEngine` validates and sizes before execution.

### Configuration System

Config files in `config/` follow naming `config_live_{account_size}.yaml`. The active config is passed via `--config` when invoking `src/main.py`. Key risk parameters for the $5k account:
- `risk_per_trade_pct: 0.003` ($15/trade)
- `max_daily_loss_pct: 0.025` ($125)
- `max_drawdown_pct: 0.07` ($350)
- `max_positions: 2`
- Circuit breaker: pause 15 min after 3 consecutive losses, hard stop at 5

### MT5 Bridge

File-based communication via the MT5 Common Files directory (auto-detected per OS). The MQL5 EA (`mt5_bridge/EA_FileBridge.mq5`) polls for command files and writes response files. `mt5_bridge/mt5_file_client.py` is the Python side.

### Testing Notes

- Unit tests in `tests/unit/` mock all MT5 dependencies — no live connection needed
- Integration tests in `tests/integration/` require MT5 running; see `tests/integration/README.md`
- `pytest.ini` sets `pythonpath = .` so imports resolve from repo root

### Coding Philosophy — Consult the Legends

Before writing or refactoring any non-trivial code, consult `.agents/workflows/codinglegits.md` and pick the legend whose instinct best matches the task. Don't apply all 11 at once — that is noise. Pick the one (or two) that match and let their rules drive the design.

Quick routing guide for this codebase:

| Kind of work | Legend(s) to channel | Why |
|---|---|---|
| New strategy / signal logic | **Carmack** + **geohot** | Pure functions for detection, mutable state visible at `on_bar()`; simplest implementation that could work |
| Refactoring a sprawling file | **TJ Holovachuk** | One module, one responsibility; delete mercilessly; API learnable in 5 min |
| Risk engine, execution, portfolio | **Carmack** + **Jeff Dean** | Absolute determinism, worst-case > average-case, idempotency on retries, design for failure |
| MT5 bridge, connectors, I/O | **Jeff Dean** + **geohot** | Back-of-envelope latency budgets, own the stack, instrument everything |
| Regime classifier / ML weights | **geohot** + **Carmack** | Understand every layer; make state explicit; no hidden magic in the nightly job |
| Backtest / optimisation scripts | **ThePrimeagen** + **TJ** | Boring solutions ship; small scripts over frameworks; no cognitive debt |
| Hot loop in `main.py` | **Carmack** + **Jeff Dean** | Explicit state mutations, latency awareness (tick-rate numbers), no synchronous blocking on the critical path |

Rules for using the legends file:
- **Before coding:** pick the legend, read that section, write one sentence on which rule is driving the design.
- **Before committing:** run the change against that legend's *Signature Question*. If you can't answer it cleanly, the change isn't ready.
- **When rules conflict** (e.g. Carmack says "inline for awareness," TJ says "split into modules"), the call depends on the layer: state-mutation code inlines (Carmack wins), pure helpers extract (TJ wins).
- Do **not** pepper code with comments naming legends ("# Carmack rule"). Some existing files do this — don't add more. The legend is the lens you use while writing, not a signature you leave behind.

### Key Design Constraints

- **Risk engine has absolute veto power** — no order reaches MT5 without passing `risk_engine.validate_signal()`
- **Strategies must be stateless** — all state lives in DataEngine or PortfolioEngine, not in strategy classes
- **State is persisted periodically** (`state/state_manager.py`) to allow crash recovery on restart
- **News blackout** around high-impact ForexFactory events suppresses all signals

### Propagating Strategy Changes (IMPORTANT)

A strategy is not "one file" — it is wired across the registry, the ML regime classifier, every live config, and tests. Whenever you **add, rename, enable/disable, or retune** a strategy, update ALL of the following in the same change. Leaving any of them stale will either crash the regime classifier's completeness test or silently drop the strategy at runtime.

**1. Registry & timeframe resolution**
- `src/strategies/strategy_manager.py` — add the `from .<file> import <Class>` import and register the class in the per-symbol instantiation block. The key used here is the canonical `strategy_name` everywhere else.
- `src/main.py` — timeframes are now pulled dynamically from config (`_strategy_timeframe` around `src/main.py:488`), so you do NOT need to hard-code a timeframe here. BUT: check for any strategy-name branches (e.g. `if signal.strategy_name == "kalman_regime"`) that may need extending.

**2. ML regime classifier / weights**
- `scripts/regime_classifier.py` — add the new name to the `STRATEGY_WEIGHTS` dict (around line 243). **Keys must be identical across `TREND` / `RANGE` / `VOLATILE`** — `test_weights_table_completeness` enforces this and will fail CI otherwise. Also extend `resolve_strategy_overrides` if the strategy should gate on regime.
- `data/config_override_XAUUSD.json` (and sibling `_BTCUSD.json`, `config_override.json`) — the runtime-emitted override consumed by `_apply_regime_override()` in `main.py`. These are rewritten nightly by `scripts/regime_classifier.py`, but manual edits here propagate immediately on next loop.

**3. Configs — every active account size**
All of `config/config_live_{100,1000,5000,10000,25000,50000}.yaml` (and `config_live.yaml`) need:
- A `strategies.<name>:` block with at least `enabled`, `timeframe`, and strategy-specific params.
- An entry in every `trading_hours.sessions[].strategies: [...]` whitelist where the strategy should fire — a strategy absent from the session list is silently skipped (`main.py:499`).

**4. Tests**
- `tests/unit/test_<strategy>.py` — unit tests for the strategy itself.
- `tests/unit/test_regime_classifier.py::test_weights_table_completeness` — the `required_core` set (around line 298) hard-codes every expected strategy key. Update it when adding/renaming.

**5. Memory & docs**
- Update the strategies table above in this `CLAUDE.md`.
- If the change is a policy decision (strategy disabled live, retuned, etc.), save a `project_*` memory note so it survives future sessions.

**Checklist for a new strategy** (all must be done together):
`strategy_manager.py` import/register → `STRATEGY_WEIGHTS` (3 regimes) → each `config_live_*.yaml` (`strategies.<name>` block + session whitelists) → `test_weights_table_completeness` `required_core` → unit test file → this CLAUDE.md table.
