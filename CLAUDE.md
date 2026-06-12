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

**As of 2026-05-14, raw strategy signals are post-filtered by `ConfluenceGate` (`src/strategies/confluence_gate.py`).** The gate implements the combo policy from `combine_startegy.md`: only `kalman_regime` and `london_breakout` fire solo; `sbr` / `vwap` only fire when their confluence legs agree (COMBO A in TREND, COMBO B in RANGE); `smc_ob` + `fibonacci_retracement` + `momentum` aligned in any regime emits a `combo_sniper` signal sized 1.5×.

**As of 2026-06-10, the six kill-list strategies were DELETED from the codebase** (no backtested edge — disabled live for weeks). Removed: `breakout`, `mean_reversion`, `supply_demand`, `descending_channel_breakout`, `mini_medallion`, `continuation_breakout` — files, tests, config blocks, and `STRATEGY_WEIGHTS` keys are gone. Their names remain in the gate's `KILL_LIST` as a defensive net so a stale config can't crash the registry lookup. Git history preserves the deleted code. The gate is governed by the `strategies.confluence_gate:` config block (`enabled`, `window_minutes`, `sniper_lot_multiplier`, `sniper_cooldown_minutes`, `exhaustion_filter`).

| # | Strategy | File | Combo role (post-gate) | Summary |
|---|----------|------|------------------------|---------|
| 1 | **KalmanRegime** | `kalman_regime_strategy.py` | **Solo (allowlist)** | **Reverted 2026-06-04 to v2** (commit 25ce0cd, 2026-05-06 tuning): two-sided trend/range regime-switching Kalman. TREND mode rides direction when close diverges from the Kalman line (ADX gate); RANGE mode fades OU z-score extremes. SELL-side gated by HTF 1H-EMA(50) filter + tighter RSI/strength to curb gold's bullish-drift bleed; 6-hour session mask `[[3,4],[20,23]]`. 15m bars, sl3.0/tp4.0. Chosen over v3 deep-dip after 6mo backtest (v2 PF 1.53 / +117% vs v3 PF 0.93 / −1%, risk-bypassed). ⚠️ v2's −22% DD breaches live caps when uncapped — see [[project_kalman_v2_revert]]. |
| 2 | **Momentum** | `momentum_strategy.py` | Filter-only (COMBO A confirm, COMBO C leg) | Short-term ROC with ADX confirmation |
| 3 | **VWAP** | `vwap_strategy.py` | COMBO B primary (RANGE) | Deviation from 30-period VWAP |
| 4 | **StructureBreakRetest** | `structure_break_retest.py` | COMBO A primary (TREND) | Donchian break → retest of broken level → rejection candle; tighter SL than raw breakout |
| 5 | **FibonacciRetracement** | `fibonacci_retracement_strategy.py` | Filter-only (COMBO A level, COMBO C leg) | Pullbacks into the 50%–61.8% Golden Zone with rejection-candle confirmation |
| 6 | **SMCOrderBlock** | `smc_ob_strategy.py` | Filter-only (COMBO B precision, COMBO C leg) | 5-phase ICT order-block state machine (OB formed → touched → sweep → waiting-entry → fire) |
| 7 | **AsiaRangeFade** | `asia_range_fade_strategy.py` | Filter-only (COMBO B session gate) | Range-fade for the UTC 09–14 low-volatility window with BB compression + RSI extremes |
| 8 | **LondonBreakout** | `london_breakout_strategy.py` | **Solo (allowlist)** | **Added 2026-06-11, USDJPY ONLY (hard symbol gate in code — loses on GBPUSD/AUDUSD).** Asia 00–07 UTC range; first 5m close beyond it 07:00–11:59 enters with the break (window widened from 09:59 on 2026-06-12, "variant B", user decision — marginal late entries ~break-even, see frequency research below); SL = 0.5×range; **NO TP** — exit is the per-strategy time stop (`risk.trailing_stop.strategy_overrides.london_breakout`: 360 min, BE/lock disabled). Research PF 1.23 IS / 1.44 OOS (2.5y Dukascopy); ⚠️ FAILED the strict-fill gate (PF ~1.0) — live by user decision; realistic-fill PF 1.39/+32%. **Enabled in ALL live configs since 2026-06-12** (user decision; the in-code USDJPY gate keeps it off other symbols). |

| 9 | **MondayDrift** | `monday_drift_strategy.py` | **Solo (allowlist)** | **Added 2026-06-12, GBPUSD+AUDUSD ONLY (hard symbol gate in code — EURUSD too weak, USDJPY inverted).** Long-only Monday anti-USD drift hold: first 15m bar 00:00–00:59 UTC Monday (NOT the Sunday open — that "edge" is a bid-spread artifact), gated on daily close > SMA(50) (regime kill-switch). SL = 1.0×dailyATR(14); NO TP — exit is `strategy_overrides.monday_drift` time stop (1230 min ⇒ flat ~20:45 UTC before rollover, BE/lock disabled). Research (`research_monday_drift.py`): GBPUSD PF 1.95 all / 1.54 IS / 4.17 OOS; AUDUSD 1.59/1.21/2.60; every year ≥ flat. ⚠️ **REGIME trade by user decision** — it harvests the 2025–26 dollar-weakness drift and will decay/reverse if USD strengthens; the SMA gate is the only protection. **PASSES the strict-fill gate** (full 2.5y strict: GBPUSD PF 1.96 +10.9% DD −2.9%, AUDUSD PF 1.84 +11.7% DD −2.7% — wide ATR stops + time exits shrug off stop-fill overshoot, unlike LBO). Enabled in `config_live_5000.yaml` only. Backtest needs `max_window` ≥ ~75 days of bars (run_backtest passes it automatically). New infra: `symbols.<ticker>.strategy_whitelist` keeps the rest of the roster (kalman = PF 0.98 loser on GBPUSD) off these pairs. |

`opening_range_breakout_strategy.py` exists as a research artifact (registered nowhere, not loaded live).

**Researched and REJECTED 2026-06-11: dedicated GBPUSD strategy** (`scripts/research_gbpusd.py`). Twelve candidate families across two sessions all dead: Asia-sweep reverse / prev-day-level fade (PF < 1 every variant, despite 74% of GBPUSD breakouts failing — the fade doesn't monetize through stops), London-close fade (IS 1.18 → OOS 0.36–0.64), NY continuation (IS/OOS sign flip), weekend gap fade (looked PF 4+, exposed as Sunday-open **bid-spread artifact** — edge decays to PF 0.95 with 2h entry delay; data is BID candles), Monday-long seasonality (t=3.5 but identical on EURUSD/AUDUSD and inverted on USDJPY = 2025–26 anti-USD drift, not an edge). Plus session-1 kills: london_breakout PF 0.55–0.88, OU fade, Donchian, Asia BB-fade, EURUSD spread, kalman v2 PF 0.98. **Do not re-research these families.** See `project_gbpusd_no_edge` memory. **UPDATE 2026-06-12: user chose to harvest the Monday anti-USD drift anyway** → shipped as `monday_drift` (strategy #9 above) with the SMA(50) regime gate as kill-switch; the no-structural-edge verdict stands.

**Researched and REJECTED 2026-06-12: london_breakout frequency tuning** (`scripts/research_lbo_frequency.py`). Four ways to make LBO trade more than once/day, all dilutive on the same IS/OOS harness as the original research: wider entry window to 11:45 (OOS PF 1.44→1.38), re-entry after stop-out (marginal 2nd entries alone PF 0.76, −3.7p avg), reverse-on-failed-break (166 reversal trades PF 0.87, −1.9p avg), wide+re-entry combined (best IS t=1.93 but OOS 1.34 < baseline 1.44). The one-trade-per-day latch and tight 07:00–09:59 window ARE the edge — the first morning break carries all the OOS profit. Marginal trades at flat-cost PF<1.3 would only get worse under strict fills. **Do not loosen the latch; re-entry/reversal stay dead.** **UPDATE 2026-06-12: user chose to ship the wide window (variant B) anyway** — `entry_end_hour: 12` in all configs; YTD impact +8 trades, +$20, PF 2.04→1.90 (`reports/london_breakout_2026_varB_backtest.md`). Revert = one line back to 10 if live late entries bleed.

**Researched and REJECTED 2026-06-12: dedicated EURUSD strategy** (`scripts/research_eurusd.py`, `research_eurusd_daily.py`, `research_eurusd_streak.py`). EURUSD is the efficient major — nothing clears the gate. Intraday (2.5y Dukascopy): generic scan all dead (london_breakout 0.91/0.69, ou_fade 1.05/0.98, donchian 0.77/0.88, asia_fade 0.80/0.63); Breedon–Ranaldo session holds, London-open reversal, NY spike fade all PF < 0.9; the 22:00 UTC hourly drift (t=+5.79!) is a **rollover bid-spread artifact** — daily cousin of the Sunday-open trap. Daily (22y yfinance closes): best cell 4-down/up-streak fade hold-5d passed close-to-close (IS 1.39 / OOS 1.47, eras stable, both sides positive, pair-selective) but **died in the implementable form** — PF 1.14–1.24 with any real ATR stop on true OHLC (profit = unstopped recoveries only), entry-delay collapses IS to 1.06, and all 2024–26 true-data profit comes from 2025 alone. ⚠️ Data trap: yfinance `EURUSD_daily.csv` has **fake opens after ~2013** (open == same-day close snapshot) — only close-to-close logic is valid on it. **Do not re-research: streak/RSI2/IBS daily fades, session-drift holds, spike fades on EURUSD.** Prior EURUSD kills stand: monday_drift (PF 1.09), EURUSD/GBPUSD spread fade.

**Researched and REJECTED 2026-06-11: `session_vwap_reversion`** (NY-session 2σ VWAP fade). Looked promising in flat-cost research (PF ~1.15–1.25 causal) but **failed the official strict-fill gate: PF 0.94, negative Sharpe** — adverse stop-fill slippage kills the thin fade edge. Fully implemented then reverted (research scripts `scripts/research_vwap_*.py` + memory retained). Lesson: flat-cost research PF must clear ~1.3+ to survive strict fills. See `project_intraday_edge_research` memory.

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
