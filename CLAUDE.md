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

### Data Flow

```
MT5 Terminal → EA_FileBridge.mq5 (file I/O) → MT5Connector
→ DataEngine (ticks → multi-TF bars → indicators)
→ StrategyManager (6 strategies emit Signals)
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

Six strategies, all configurable on/off per `config.yaml`:
1. **KalmanRegime** — Kalman filter trend-follow in trending regime, OU z-score mean-reversion in ranging regime
2. **Breakout** — Donchian channel breakout with multi-timeframe confirmation
3. **MeanReversion** — OU z-score entries at extremes (|z| > 2.0)
4. **Momentum** — Short-term ROC with ADX confirmation
5. **VWAP** — Deviation from 30-period VWAP
6. **MiniMedallion** — 10 weak alpha signals combined into a composite score (threshold ±3.0)

All strategies emit `Signal` objects; `RiskEngine` validates and sizes before execution.

### Configuration System

Config files in `config/` follow naming `config_live_{account_size}.yaml`. The active config is passed via `--config`. Key risk parameters for the $5k account:
- `risk_per_trade_pct: 0.003` ($15/trade)
- `max_daily_loss_pct: 0.025` ($125)
- `max_drawdown_pct: 0.07` ($350)
- `max_positions: 2`
- Circuit breaker: pause 15 min after 3 consecutive losses, hard stop at 5

### MT5 Bridge

File-based communication via the MT5 Common Files directory (auto-detected per OS). The MQL5 EA (`mt5_bridge/EA_FileBridge.mq5`) polls for command files and writes response files. `mt5_bridge/mt5_file_client.py` is the Python side. An alternative ZeroMQ bridge exists (`mt5_zmq_client.py` + `EA_ZeroMQ_Bridge.mq5`).

### Testing Notes

- Unit tests in `tests/unit/` mock all MT5 dependencies — no live connection needed
- Integration tests in `tests/integration/` require MT5 running; see `tests/integration/README.md`
- `pytest.ini` sets `pythonpath = .` so imports resolve from repo root

### Key Design Constraints

- **Risk engine has absolute veto power** — no order reaches MT5 without passing `risk_engine.validate_signal()`
- **Strategies must be stateless** — all state lives in DataEngine or PortfolioEngine, not in strategy classes
- **State is persisted periodically** (`state/state_manager.py`) to allow crash recovery on restart
- **News blackout** around high-impact ForexFactory events suppresses all signals
