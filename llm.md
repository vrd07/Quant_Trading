You are a senior quantitative engineer and ex–prop firm trading systems architect.

Your task is to design and implement a professional-grade, production-quality algorithmic trading system with the following requirements:

GOAL:
Build a modular, secure, testable Python-based trading engine that connects to MetaTrader 5 (MT5) using ZeroMQ, trades XAUUSD (Gold) and BTC, and follows strict prop-firm style risk management rules.

ENVIRONMENT:
- User is on macOS
- Trading via MT5
- Python is the main strategy engine
- MT5 only acts as execution + data feed
- Communication via ZeroMQ bridge

ARCHITECTURE REQUIREMENTS:
- Follow clean architecture and SOLID principles
- Separate modules:
  - mt5_connector (ZeroMQ MQL5 server + Python client)
  - data_engine (candle store, session tagging, indicators)
  - strategy_engine (multiple strategies + regime filter)
  - risk_engine (max daily loss, drawdown, risk per trade, kill switch)
  - execution_engine (order routing, confirmations, retries)
  - portfolio_engine (position tracking, exposure control)
  - backtesting_engine (event-driven, walk-forward capable)
  - logging_engine (full audit trail)
  - config system (YAML-based)

SECURITY REQUIREMENTS:
- No credentials in code
- Environment variables for secrets
- Validate all messages between MT5 and Python
- Hard kill-switch if:
  - daily loss exceeded
  - drawdown exceeded
  - connection corrupted
- All orders must pass risk engine before execution
- Full logging of every decision

TRADING LOGIC REQUIREMENTS:
Implement:

1) Regime Filter:
   - Classify market as TREND or RANGE using volatility + ADX

2) If TREND:
   - Use Donchian or structure breakout strategy

3) If RANGE:
   - Use VWAP or Z-score mean reversion

4) Instruments:
   - XAUUSD
   - BTC

RISK RULES:
- Risk per trade: configurable (default 0.25%)
- Max daily loss: configurable
- Max drawdown: configurable
- One-direction exposure cap
- Kill switch must flatten all positions

BACKTESTING:
- Event-driven backtester
- Same strategy code used in live & backtest
- Metrics:
  - Sharpe
  - Max DD
  - Expectancy
  - Win rate
  - Monte Carlo

DEVELOPMENT PLAN:
1) Build project scaffold
2) Build ZeroMQ MT5 bridge
3) Build data engine
4) Build risk engine
5) Build one strategy
6) Build backtester
7) Add logging & monitoring
8) Add second strategy

CODE QUALITY:
- Type hints everywhere
- Docstrings
- Unit tests
- No monolithic files
- No hardcoded magic numbers

OUTPUT:
- Generate full folder structure
- Implement minimal working system first
- Then expand

CRITICAL:
- This is NOT a toy bot
- This is a production-grade trading engine
- Favor safety, correctness, and testability over speed or features
