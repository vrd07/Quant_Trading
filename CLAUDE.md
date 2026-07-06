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
python scripts/volatility_monitor.py              # London/NY-open "Beast mode" scalp alerts (alert-only; auto-started headless by start_live.sh; EA WatchSymbols input feeds non-chart symbols)
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
→ StrategyManager (15 strategies emit Signals)
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

**As of 2026-06-30, the live SL/TP is "budget-SL + RR-TP" (user decision).** The start-script (`scripts/runtime_setup.py`) "SL" the user enters is a max-loss-USD → `risk.risk_per_trade_usd`. The execution engine's **BudgetSL** converts it to the actual SL distance: `sl_dist = risk_per_trade_usd / (lot × value_per_lot)` (e.g. $150 / (0.2 × 100) = 7.5 pts). **Immediately after the SL rewrite, the TP is set to `reward_risk_ratio × sl_dist`** (1:1 / 1:2 / 1:3) — so the realized R:R is exact regardless of which strategy fired. The ratio comes from `risk.reward_risk_ratio` (global default 2.0) and is overridable per strategy via `strategies.<name>.rr`; `RiskProcessor.calculate_stops()` resolves it into `signal.metadata['reward_risk_ratio']` and `execution_engine` applies it. A fixed-dollar `risk.take_profit_usd > 0` still overrides the RR-TP (BudgetTP), unless the strategy preserves its own TP. ⚠️ **Strategies that set `preserve_structural_sl` in their OWN code (squeeze_breakout = fixed 33pt, stoch_pullback = structural) bypass BOTH BudgetSL and the RR-TP** — they keep their validated native SL/TP. kalman_regime IS budget-governed (its ATR TP is overridden by the RR-TP). The 4 calendar strategies (`london_breakout`, `monday_drift`, `index_overnight`, `wednesday_drift`) have NO TP and a `preserve_structural_sl`-style precomputed stop — unaffected. (History: an earlier 2026-06-30 attempt added a fixed `sl_points` config branch that made strategies IGNORE the start-script $ SL — REVERTED; "inputted value" means the start-script max-loss-USD, not a config points value.)

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

| 10 | **SqueezeBreakout** | `squeeze_breakout_strategy.py` | **Solo (allowlist)** | **Added 2026-06-22, XAUUSD ONLY (hard symbol gate in code).** Volatility-coil → expansion breakout on 15m gold. COIL = ATR(14) ≤ 20th pctile(100) **and** flat Kalman (|slope3| ≤ 0.5×ATR); BREAK = ATR expanding by ≥ `atr_expansion_ratio` (default **1.05×**, not a mere uptick) **and** close clears the coil's Donchian(20) hi/lo by ≥ `min_penetration_atr`×ATR (default **0.1**) → enter with the break. **SL = `sl_points` FIXED 33pts (NOT ATR — code default 33); TP = SL×`rr`** (rr 2.0). **Loser-profile filters (added 2026-06-22, `scripts/analyze_squeeze_losers.py`):** the bleed was "fakeout" breaks — shallow penetration (<0.1 ATR: WR 22%, −$1,246) and weak vol expansion (jump 1.02–1.05×: −$1,125; >1.10×: +$3,096). The two gates walk-forward-validate (improve BOTH IS+OOS, unlike the overfit hour-map): production engine full-span **PF 1.12→1.21, MaxDD −13.8%→−6.25%, +15.7%→+18.5%, 573→407 trades**; research-sim SL33/RR2.0 **2026 1.27→1.38 / 2025 OOS 1.05→1.42**. Params (`min_penetration_atr`/`atr_expansion_ratio`/`htf_ema_period`) now written into all 8 `squeeze_breakout` config blocks at their validated defaults (2026-06-23). ⚠️ SELL side is the residual structural bleed (gold drift, −$1,984 full-span) but a no-SELL gate is a regime bet, not shipped. **HTF-trend gate (added 2026-06-22, `scripts/research_squeeze_htf_gate.py`):** `htf_ema_period` (default **400** on 15m ≈ EMA100 1H) — only take breaks ALIGNED with the slow EMA (BUY above / SELL below); counter-trend breaks were the residual whipsaw bleed (the Apr–May 2026 12-loss streak). Walk-forward improves BOTH years monotonically in EMA length (2026 1.38→1.91, 2025 OOS 1.42→1.67; side-only — slope term overfits IS, rejected). **Production engine before→after: PF 1.21→1.44, return +18.5%→+24.1%, MaxDD −6.25%→−5.85%, 407→273 trades** (more $ on a third fewer trades). **BIGGEST win — it now SURVIVES `--enforce-risk`:** the pre-gate version throttled to flat (kill switch choked it); gated it gets a kalman-style kill-switch LIFT — $25k enforced PF **1.61** (+28.9%, DD −4.26%), $5k enforced PF **1.44** (+27.7%, was flat/dead). Set `htf_ema_period: 0` to disable. ⚠️ **The fixed stop IS the edge:** `sl_atr_multiplier`×ATR floats wider in high-vol stretches and kills it (2026 engine PF: fixed-33 **1.20** vs 3×ATR **0.99**). `cooldown_bars` 8; session filter REFUTED → trades **all hours**. Research (`research_squeeze_breakout.py`): SL33/RR2.0 → 2026 PF 1.27 / 2025 OOS 1.05; cost-robust strict 0.50/side (1.25/1.07). **Production-engine backtest (`run_backtest --timeframe 15m`, fixed-33) reproduces it: 2026 PF 1.20 (+27.6%), full-span 1.13 (+56%), risk-bypassed.** ⚠️ Under `--enforce-risk` the PRE-GATE version throttled to flat (573→38 trades, PF 1.00) — but the HTF-gated version above now gets the kill-switch LIFT (enforced PF 1.44–1.61), so this caveat is SUPERSEDED by the HTF gate. ⚠️ **MARGINAL when shipped, materially improved since** (posture as #8/#9): same instrument as kalman (~+0.13/+0.20 corr). **Enabled in ALL live configs** (in-code XAUUSD gate scopes it). **Live-path SL/TP fix (2026-06-22):** added a `squeeze_breakout` branch in `risk_processor.calculate_stops` (honors precomputed `stop_price`/`take_profit_price`) + a `preserve_structural_sl` metadata flag that exempts it from `execution_engine` BudgetSL (which would shrink the 33pt stop to the $-budget and break RR2.0); BudgetTP guarded the same way. Backtest path (`ensemble_engine`) uses the strategy's native SL/TP directly — unaffected. **Always backtest `--timeframe 15m`** (run_backtest default is 5m → over-fires 3.6×). |

| 11 | **StochPullback** | `stoch_pullback_strategy.py` | **Solo (allowlist)** | **Added 2026-06-22, XAUUSD ONLY (hard symbol gate in code).** ACY "Trade Gold Using Stochastics 2R/3R" method — a trend-*continuation* pullback, NOT a reversal. TREND = EMA(50) slope + price side **AND price ≥ `min_ema_dist_atr`(1.0)×ATR from the EMA** (trend-extension filter, added 2026-06-22, `scripts/analyze_stoch_losers.py`: losers entered with price ON the EMA = chop, |ema_dist| 0.3-1.0 ATR was WR ~20%/−$781; walk-forward both yrs up, >1.25 overfits IS; prod full-span PF 1.10→1.19, +9.8%→+17%, DD −9.2%→−7.3%; set 0 to disable); PULLBACK = Stochastic(14,3) %K cools into 20-30 (long) / 70-80 (short) within last `arm_window`(10) bars; ENTRY = close breaks the prior `range_bars`(5) consolidation in trend dir with %K back above/below %D. **SL = STRUCTURAL (just behind the range ± `buffer_pts`); TP = `rr`×stop-dist** (rr 2.0 = the edge — RR3.0 marginal, RR1.5 lower). `cooldown_bars` 5. **Embedded session filter (`session_start_hour`7 / `session_end_hour`21 UTC = London→NY)** — additive: ~halves DD (2026 −26%→−14%) holding PF ~1.27/1.28 both yrs; set 0/24 for all-hours. Research (`research_stoch_pullback.py`, strict fills): 15m RR2.0 → 2026 PF 1.31 / 2025 OOS 1.19 (risk-bypassed; 5m noisier). **Production-engine backtest (`run_backtest --timeframe 15m`) reproduces it: full-span PF 1.10 (+$823/+82%), 711 trades, risk-bypassed.** ⚠️ **ACCOUNT-SIZE DEPENDENT:** under `--enforce-risk` the $5k 5% trailing-DD kill switch ($250 = ~3 min-lot losses, since min_lot 0.02 on gold's wide structural stops already risks ~$78/trade) HALTS the 2026 run after 10 trades → PF 0.44; the SAME signals SURVIVE enforcement at **$25k+** (2026 PF 1.13, DD −2.9%). ⚠️ **Even with the EMA filter the 7% cap is NOT guaranteed at $25k** (2026-06-22 check): 2026 $25k enforce still tripped the kill switch −7.01% in the Apr–May losing stretch (PF 0.62/0.58 those months) → flat; it's a slow 2-month regime-bleed + the min-lot floor blocks de-risking, NOT a fat SL. $50k holds the cap (DD −4.14%) but 2026 PF only 1.02 (near-breakeven; BUY side bled in spring's round-trip). Full-span $25k enforce PF 1.14/+10.9%/DD −7.18%. **Structurally the weakest gold strategy — can't filter under the $25k cap; wants $50k+.** ⚠️ **Shipped by user decision** as a diversifier (posture as #8/#9/#10), NOT because it cleared the $5k gate — loosely correlated with kalman/squeeze (same instrument). **Enabled in ALL live configs** (in-code XAUUSD gate scopes it). **Live-path SL/TP:** `stoch_pullback` branch in `risk_processor.calculate_stops` honors precomputed `stop_price`/`take_profit_price`; `preserve_structural_sl` flag exempts it from `execution_engine` BudgetSL (which would shrink the structural stop & break RR2.0). Backtest path (`ensemble_engine`) uses native SL/TP — unaffected. **Always backtest `--timeframe 15m`** (run_backtest default 5m over-fires). |

| 12 | **IndexOvernight** | `index_overnight_strategy.py` | **Solo (allowlist)** | **Added 2026-06-24; research-validated US30/NAS100/GER40 but LIVE = US30 ONLY (hard symbol gate in code, default `allowed_symbols:['US30']`).** ⚠️ **2026-06-24: NAS100 DROPPED everywhere — the broker offers no NASDAQ-100 index CFD** (its "NASDAQ" group is cash equities/warrants/ETFs, untradeable overnight where the drift lives); GER40 also not offered. US30 is the sole live index leg; NAS100 symbol blocks + `allowed_symbols` entries + the `symbol_reconciler._STRATEGY_SYMBOLS` map were stripped (8 configs + code + tests, 379 pass). First gold-UNCORRELATED edge found — the "Turnaround Tuesday" equity-index **overnight night drift**. Enter LONG at the Tuesday US cash-close window (`entry_hour_utc`19:`entry_minute_utc`45 UTC ≈ 20:00 close), hold overnight, exit Wed ~13:30 UTC at cash open. **One trade/week per symbol** (latch). **SL = WIDE 1.5×daily-ATR catastrophe guard only (research ran with NO stop at −3% DD); NO TP — exit is the per-strategy time stop** (`strategy_overrides.index_overnight`: 1050 min, BE/lock disabled — BE/lock tightening destroys an overnight drift hold, win-rate 38%→57% / DD −18.8%→−0.7% once disabled). Long-only: the overnight drift IS the edge. **NO regime gate** (SMA gate HURT — effect is trend-agnostic, unlike monday_drift). Research (`research_index_*.py`, 2.5y Dukascopy, 2bps cost+2bps/night financing): the everyday night hold nets to PF ~1.15 after CFD overnight FINANCING, but the drift localises to MIDWEEK — Tue-entry +ve & significant on US30/NAS100/GER40 *independently* (NAS t2.17/US30 t1.83/GER40 t2.72), and Tue-only pays financing ~1 night/wk. **Tue-only PF: NAS 1.74 (IS1.42/OOS3.31), US30 1.68 (1.51/2.19), GER40 2.29** — every year 2024/25/26 PF>1.3, maxDD −3%, cost-robust to ≥1.36 PF at 8bps all-in. **Production-engine backtest (`run_backtest --timeframe 15m`) reproduces it: US30 PF 1.68 / NAS100 PF 1.58, win ~58%, DD −0.7%, +2.4%/2.5y at $5k sizing.** ⚠️ **SURVIVES `--enforce-risk` PERFECTLY** — kill switch never trips (DD <0.75% leaves huge headroom under the 7% cap), unlike stoch/squeeze which throttle. ⚠️ This is a **calendar/SEASONAL anomaly** (monday_drift-class), NOT structural alpha; the 3 indices are correlated (~1–1.5 independent bets). GER40 strongest but **broker doesn't offer it**, and NAS100 is also unavailable — so **US30 only** lives. **Enabled in ALL live configs** (in-code US30 gate + `symbols.US30.strategy_whitelist:[index_overnight]` scope it). ⚠️ **Index-CFD contract specs (min_lot/value_per_lot/leverage) in the config symbol blocks are PLACEHOLDERS — VERIFY against the broker's MT5 symbol spec; they drive $5k sizing & the DD cap.** Live-path SL honored via the `index_overnight` branch in `risk_processor.calculate_stops` (shared with london_breakout/monday_drift: precomputed `stop_price`, NO TP). **Always backtest `--timeframe 15m`.** Report: `reports/index_overnight_tuesday_research.md`. |

| 13 | **WednesdayDrift** | `wednesday_drift_strategy.py` | **Solo (allowlist)** | **Added 2026-06-24, AUDJPY ONLY (hard symbol gate in code).** Mid-week JPY-weakness / risk-on carry drift — found by the index_overnight method applied to JPY crosses (`research_newinstruments_calendar.py`). Enter LONG at the Tuesday session-close window (`entry_hour_utc`19:`entry_minute_utc`45 UTC ≈ 20:00), hold the Wednesday session, exit ~Wed 20:00 UTC. **One trade/week** (latch). **SL = WIDE 1.5×daily-ATR guard only; NO TP — exit is the per-strategy time stop** (`strategy_overrides.wednesday_drift`: 1440 min, BE/lock disabled). Long-only, NO regime gate. Research (2.5y Dukascopy, Tue-close→Wed-close): **AUDJPY PF 1.57 (IS 1.38/OOS 2.46), EVERY year positive (2024 1.21/2025 1.97/2026 1.92), maxDD −5.1%, cost-robust ≥1.46 at 4bps**; EURJPY confirms the direction (JPY-weakness-Wednesday) but too weak to ship (OOS 1.15). **Production-engine backtest (`run_backtest --timeframe 15m`) reproduces it: PF 1.49, win 60.3%, +9.9%/2.5y, DD −3.5%, 126 trades; SURVIVES `--enforce-risk` perfectly** (identical, kill switch never trips). index_overnight-class quality on a **more diversifying driver** (carry/JPY/risk — uncorrelated to gold AND equities). ⚠️ Calendar/SEASONAL anomaly shipped by user decision; mechanism fuzzier than oil-EIA (mid-week risk-on / AUD-data?); only 3yr; AUDJPY-Monday also +ve but overlaps monday_drift's AUD-Monday. **Enabled in ALL live configs** (in-code AUDJPY gate + `symbols.AUDJPY.strategy_whitelist:[wednesday_drift]` scope it; auto-lot via GET_SYMBOL_SPEC). Live-path SL via the shared `wednesday_drift` branch in `risk_processor.calculate_stops`. **Always backtest `--timeframe 15m`.** |

| 14 | **BOSStructure** | `bos_structure_strategy.py` | **Solo (allowlist)** | **Added 2026-07-07, XAUUSD ONLY (hard symbol gate in code — US30 FAILED the same research, PF 1.05 raw / 0.73 enforced).** User-authored SMC break-of-structure sequence (`new_strategies.md` #1): CHOCH (close breaks last swing AGAINST prevailing trend) → BOS#1 (break in NEW direction, trend flips) → BOS#2 (armed) → **ENTRY on the next CONFIRMED pullback pivot** (higher-low long / lower-high short); each further BOS re-arms one entry. Pivots = `pivot_bars`(5)-bar fractals confirming N bars late (no lookahead); close-based breaks only. **SL = STRUCTURAL (just beyond the entry pivot ± `buffer_atr`(0.1)×ATR); TP = `rr`(2.0)×stop-dist**; `preserve_structural_sl` exempts it from BudgetSL/RR-TP (branch shared with stoch_pullback in `risk_processor.calculate_stops`). Research (`research_bos_structure.py`, strict fills, 15m): **N=5 positive BOTH years at ALL RRs (PF 1.41–1.60); best cell N=5/RR2.0 full PF 1.60 +$2,269 @0.02 lot, 2025 PF 1.56 / 2026 PF 1.64, cost-robust to 3× (PF 1.52)**, median stop 13.6pts (~$27 risk), ~4–5 trades/wk; N=3 pivots are noise (dead), N=7 weaker. SELL side stronger in 2026 (PF 2.37 vs BUY 1.12). **Production backtest (`run_backtest --timeframe 15m --slippage strict`): raw PF 1.06 +7.3% DD −23.5% (239 trades — engine takes marginal re-arm signals with 2 concurrent positions + ~2.5× research costs); --enforce-risk PF 1.36 +6.0% DD −5.87% (51 trades) — SURVIVES the risk engine.** Implementation parity verified: the class's sliding 1000-bar window reproduces research signals (windowed sim PF 1.59 vs full 1.60). ⚠️ $5k research-enforce (fixed-lot-only caps) tripped the $250 trailing switch in the Jun–Jul 2025 stretch — stoch_pullback posture, happier at $25k+. **Tuning pass 2026-07-07 (user asked fewer rules / ≥2 trades/day / $50k): ALL frequency relaxations (pivot 3/4, arm-after-1-BOS, multi-shot, break entries — now config knobs `arm_after_bos`/`one_shot`/`entry_on_break`, spec defaults) FAILED the production engine (PF 0.66–0.88, up to −99%; fixed-lot sim PF 1.25 collapses to 1.02 under equal-$-risk sizing) — ≥2 trades/day with edge intact does not exist here; do NOT re-relax.** Two fixes SHIPPED with the spec cell: `trailing_stop.strategy_overrides.bos_structure.disable_be_lock: true` (default BE/lock scratched the RR2.0 winners, prod WR 27→39%) and `single_position: true` (in-strategy VIRTUAL one-position latch — replays each signal's SL/TP over the window and suppresses new signals until resolved; the edge only exists one-trade-at-a-time). **$50k prod with fixes: full-span raw PF 1.24 +13.1% DD −10.5% (~2.4 trades/wk); 2026 raw PF 1.26 +7.3% (pre-fix 1.06/0.91). Enforced still problematic: full-span halts Jun–Jul 2025 (PF 0.56); 2026 enforce throttled to 6 trades — same pre-HTF-gate squeeze pattern; an HTF alignment filter is the proven next remedy.** **Enabled in ALL live configs** (in-code XAUUSD gate scopes it). **Always backtest `--timeframe 15m`.** Report: `reports/bos_structure_research.md`. |

| 15 | **EMA200Nasdaq** | `ema200_nasdaq_strategy.py` | **Solo (allowlist)** | **Added 2026-07-07, NASDAQ-100 ONLY — CONFIGURABLE ticker** (user-authored, `new_strategies.md` #2). ⚠️ **FAILED research AND production backtest — shipped by explicit user decision ("tune later"); treat as experimental, expect bleed outside strong trend years.** Rule: ANCHOR = the 5m candle at 19:10 IST = **13:40 UTC fixed** (`anchor_hour_utc`/`anchor_minute_utc`); anchor close above EMA(200) → the FIRST later 5m close above the anchor close by 15:40 UTC (`entry_end_*`) enters BUY (mirror below for SELL); **strictly one entry/day**; **SL = anchor candle's opposite extreme; TP = `rr`(2.0)×stop-dist** (`preserve_structural_sl`). Research (`research_ema200_nas.py`, 2.5y NAS100 Dukascopy, strict): **PF 1.04 full; 2024 0.90 / 2025 1.41 / 2026 0.79** — one good year = the one-way bull leg (regime beta); raw DD −31.5%; EOD-close variant identical. **Production backtest (`run_backtest --timeframe 5m --slippage strict`): raw PF 0.58 −42.9%; --enforce-risk PF 0.19, halted −5.2%** (and the strict fill tables charge ZERO spread on indices — optimistic). **Symbol is configurable:** broker lists NASDAQ-100 under its own ticker — `runtime_setup.py` Step 1's broker-ticker rename now also rewrites ANY gated strategy's `allowed_symbols` via a `strategies:` override in `runtime_overrides.yaml` (generic; fixes prefix-gate misses like NAS100→USTEC). `symbols.NAS100` blocks (PLACEHOLDER spec — VERIFY vs broker) + `strategy_whitelist:[ema200_nasdaq]` in all 8 configs; `symbol_reconciler._STRATEGY_SYMBOLS` maps it. **Backtest `--timeframe 5m`** (native spec TF). Report: `reports/ema200_nasdaq_research.md`. |

`opening_range_breakout_strategy.py` exists as a research artifact (registered nowhere, not loaded live).

**Researched and REJECTED 2026-06-11: dedicated GBPUSD strategy** (`scripts/research_gbpusd.py`). Twelve candidate families across two sessions all dead: Asia-sweep reverse / prev-day-level fade (PF < 1 every variant, despite 74% of GBPUSD breakouts failing — the fade doesn't monetize through stops), London-close fade (IS 1.18 → OOS 0.36–0.64), NY continuation (IS/OOS sign flip), weekend gap fade (looked PF 4+, exposed as Sunday-open **bid-spread artifact** — edge decays to PF 0.95 with 2h entry delay; data is BID candles), Monday-long seasonality (t=3.5 but identical on EURUSD/AUDUSD and inverted on USDJPY = 2025–26 anti-USD drift, not an edge). Plus session-1 kills: london_breakout PF 0.55–0.88, OU fade, Donchian, Asia BB-fade, EURUSD spread, kalman v2 PF 0.98. **Do not re-research these families.** See `project_gbpusd_no_edge` memory. **UPDATE 2026-06-12: user chose to harvest the Monday anti-USD drift anyway** → shipped as `monday_drift` (strategy #9 above) with the SMA(50) regime gate as kill-switch; the no-structural-edge verdict stands.

**Researched and REJECTED 2026-06-12: london_breakout frequency tuning** (`scripts/research_lbo_frequency.py`). Four ways to make LBO trade more than once/day, all dilutive on the same IS/OOS harness as the original research: wider entry window to 11:45 (OOS PF 1.44→1.38), re-entry after stop-out (marginal 2nd entries alone PF 0.76, −3.7p avg), reverse-on-failed-break (166 reversal trades PF 0.87, −1.9p avg), wide+re-entry combined (best IS t=1.93 but OOS 1.34 < baseline 1.44). The one-trade-per-day latch and tight 07:00–09:59 window ARE the edge — the first morning break carries all the OOS profit. Marginal trades at flat-cost PF<1.3 would only get worse under strict fills. **Do not loosen the latch; re-entry/reversal stay dead.** **UPDATE 2026-06-12: user chose to ship the wide window (variant B) anyway** — `entry_end_hour: 12` in all configs; YTD impact +8 trades, +$20, PF 2.04→1.90 (`reports/london_breakout_2026_varB_backtest.md`). Revert = one line back to 10 if live late entries bleed.

**Researched and REJECTED 2026-06-12: dedicated EURUSD strategy** (`scripts/research_eurusd.py`, `research_eurusd_daily.py`, `research_eurusd_streak.py`). EURUSD is the efficient major — nothing clears the gate. Intraday (2.5y Dukascopy): generic scan all dead (london_breakout 0.91/0.69, ou_fade 1.05/0.98, donchian 0.77/0.88, asia_fade 0.80/0.63); Breedon–Ranaldo session holds, London-open reversal, NY spike fade all PF < 0.9; the 22:00 UTC hourly drift (t=+5.79!) is a **rollover bid-spread artifact** — daily cousin of the Sunday-open trap. Daily (22y yfinance closes): best cell 4-down/up-streak fade hold-5d passed close-to-close (IS 1.39 / OOS 1.47, eras stable, both sides positive, pair-selective) but **died in the implementable form** — PF 1.14–1.24 with any real ATR stop on true OHLC (profit = unstopped recoveries only), entry-delay collapses IS to 1.06, and all 2024–26 true-data profit comes from 2025 alone. ⚠️ Data trap: yfinance `EURUSD_daily.csv` has **fake opens after ~2013** (open == same-day close snapshot) — only close-to-close logic is valid on it. **Do not re-research: streak/RSI2/IBS daily fades, session-drift holds, spike fades on EURUSD.** Prior EURUSD kills stand: monday_drift (PF 1.09), EURUSD/GBPUSD spread fade.

**Researched and REJECTED 2026-06-11: `session_vwap_reversion`** (NY-session 2σ VWAP fade). Looked promising in flat-cost research (PF ~1.15–1.25 causal) but **failed the official strict-fill gate: PF 0.94, negative Sharpe** — adverse stop-fill slippage kills the thin fade edge. Fully implemented then reverted (research scripts `scripts/research_vwap_*.py` + memory retained). Lesson: flat-cost research PF must clear ~1.3+ to survive strict fills. See `project_intraday_edge_research` memory.

**Researched and REJECTED 2026-07-01: EMA(20/50) trend + zone-retest** (user-supplied discretionary rule, `scripts/research_ema_retest.py`). Price above/below BOTH EMA20+EMA50 sets bias, no entries on the crossover (stack must hold `min_trend_bars`), a retest = wick into the EMA20/EMA50 zone + close back beyond EMA20 in trend direction, BUY needs the 3rd confirmed retest / SELL fires on the 1st (as specified), structural stop beyond EMA50, fixed R:R target (rule specified no stop/target). 2.5y Dukascopy, strict fills. **15m dies outright in 2026** (PF 0.62–0.90, DD −28% to −46%); **5m hovers at breakeven** (PF 0.94–1.12, nothing clears 1.10 OOS). Swept the retest-count asymmetry itself (1/1, 1/3, 3/1, 3/3 on 5m) — the specified 3-buy/1-sell combo was already the best of the four and still doesn't clear the gate (best cell RR3.0: 2026 PF 1.12, 2025 OOS only PF 1.04). **UPDATE same day: tried tightening the zone with an ATR-scaled buffer** (`--zone-mode atr`, replaces the far boundary EMA50 with `near ± zone_atr_mult*ATR` so a retest must be a shallow controlled dip, not any wick anywhere in the EMA20-50 gap) — swept `zone_atr_mult` 0.25/0.5/0.75/1.0 on both TFs. Tightening **shrinks max DD a lot** (15m: −46%→−18–26%; 5m: −22%→−11–21%) by filtering out deep failed pullbacks, but **PF still doesn't clear 1.10 on both years anywhere** — best cells are single-year-positive only (15m zone=1.0/RR1.5: 2026 PF 1.22 but 2025 OOS 0.99; 15m zone=1.0/RR3.0 and 5m zone=1.0/RR3.0: both years marginally >1.0 but net PnL negligible over 2.5y). **Verdict unchanged: no genuine edge, ATR-tightening only improves risk profile, not the underlying signal quality.** Do not re-research this exact EMA-zone-retest formulation (either zone mode).

**Researched and REJECTED 2026-07-03: daily-bar swing trend-follower** (`scripts/research_daily_swing_trend.py`, report `reports/daily_swing_trend_research.md`). Donchian(N) breakout + ATR-chandelier trail on daily XAUUSD (2016–2026 Dukascopy, two-stage walk-forward). Best cell — Donchian(55), ATR-mult 3.0, confirm_bars 1, + the squeeze_breakout-style HTF-EMA(200) alignment and 0.1-ATR min-penetration filters (both additive IS: PF 1.52→1.73) — **passed 3 of the 4 gate legs** (IS PF 1.73 / OOS PF 8.78, MaxDD −4.9%/−2.4% within the $5k cap, cost-robust at 2× cost PF 8.71) but **FAILED "positive/flat every calendar year"**: 2016 −$212 (0/3 trades), 2017 −$31 (PF 0.77), 2021 −$20 (PF 0.88). The huge OOS number is gold's 2024–26 bull run — a trend-follower's most favourable regime was deliberately made the untouched test slice, so it confirms the system harvests trends when they exist but doesn't prove alpha; flat/choppy years bleed slowly (~9 trades/yr, 49 trades full-span). Verdict is the mechanical 4-part gate: NOT shipped. ATR-expansion filter HURT at daily TF (all top-10 IS cells had it off), unlike on 15m squeeze_breakout.

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
