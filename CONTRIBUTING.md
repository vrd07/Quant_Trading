# Contributing to Quant Trading System

Thanks for wanting to help. This started as a solo project and has grown big enough to need more hands, so bug reports, ideas and pull requests are all welcome.

> This is a research and engineering project. Nothing here is financial advice. Never test your changes on a live account.

## Where to start

- Look at issues labelled [`good first issue`](https://github.com/vrd07/Quant_Trading/labels/good%20first%20issue) or [`help wanted`](https://github.com/vrd07/Quant_Trading/labels/help%20wanted).
- Comment on an issue before you start, so two people don't build the same thing.
- Have an idea that isn't an issue yet? Open one first and describe it, especially if it touches the risk engine.

## Setup

You need Python 3.11+ and Git. You don't need MetaTrader 5 to run the unit tests or a backtest.

```bash
git clone https://github.com/vrd07/Quant_Trading.git
cd Quant_Trading
python -m venv venv             # use python3 if `python` isn't on your PATH
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt -r requirements-test.txt
```

This works as written on Linux and macOS too — nothing in the install is Windows- or MT5-specific.

## Running tests

```bash
pytest tests/unit               # no external services
pytest tests/integration        # needs a running MT5 terminal with the file bridge
```

The MT5 file bridge setup is in [`mt5_bridge/README_SETUP.md`](mt5_bridge/README_SETUP.md).

What to expect from `pytest tests/unit` on a fresh clone (verified on Linux, Python 3.12): **963 passed, 1 skipped**, and a few minutes of runtime — it is not instant. The one skip is `test_technical_feed_is_real_and_bounded` in `tests/unit/test_sentiment_engine.py`, which needs the canonical price history file (`data/historical/XAUUSD_5m_real.csv`); it starts running once you've fetched data as in "Run a backtest" below.

Before opening a PR, make sure `pytest tests/unit` passes, and add tests for any new behaviour.

## Run a backtest

You can run a backtest without MetaTrader 5. A fresh clone ships no usable price history (`data/historical/` only contains backup files), so fetch some first — `scripts/download_historical_data.py` pulls bars from Yahoo Finance and writes exactly the file the backtest runner looks for:

```bash
python scripts/download_historical_data.py --start 2026-11-03   # XAUUSD 5m by default
python scripts/run_backtest.py --strategy kalman_regime --symbol XAUUSD
```

Yahoo only serves about 60 days of 5-minute bars, so pass a recent `--start` date; older chunks come back empty. The run uses the config named in `config/ACTIVE_CONFIG` (currently `config/config_live_50000.yaml`) unless you pass `--config`, and writes its report under `data/backtests/`.

Verified end-to-end on a fresh Linux clone: 1,654 XAUUSD 5m bars (2026-11-03 → 2026-11-10) through `kalman_regime` — 174 trades, +11.06% total return, profit factor 1.29. (A one-week window is a smoke test, not a strategy verdict.)

Two gotchas worth knowing:

- If no data file exists, `run_backtest.py` tells you to run `scripts/generate_sample_data.py` — that script doesn't exist. Use `download_historical_data.py` above instead.
- Keep the download window inside a single UTC-offset period. A window spanning a daylight-saving change (e.g. late October into November) produces a CSV whose timestamps mix offsets like `-04:00` and `-05:00`, and the backtest engine then dies with `ValueError: Mixed timezones detected`.

## Project layout

The folders you'll touch most:

| Path | What's there |
| --- | --- |
| `src/strategies/` | Strategy classes, each a subclass of `BaseStrategy`, plus the strategy registry |
| `src/risk/` | Risk engine, kill switch, circuit breaker, drawdown tracker, position sizer |
| `src/backtest/` | Backtesting engine |
| `src/execution/` | Order execution |
| `config/` | Live configs per account size, and per-strategy parameter grids in `backtest_grids/` |
| `mt5_bridge/` | MetaTrader 5 file bridge (MQL5 EAs and the Python client) |
| `tests/` | `unit/` and `integration/` tests |

## Adding a strategy

1. Create `src/strategies/<name>_strategy.py` with a class that subclasses `BaseStrategy` and implements `on_bar(bars)` (returns a `Signal` or `None`) and `get_name()`.
2. Register it in the strategy registry in `src/strategies/strategy_manager.py`.
3. Add its settings under `strategies:` in the config, and a parameter grid in `config/backtest_grids/` (the README in that folder has the schema).
4. Backtest it with `scripts/run_backtest.py` and put the results in your PR, including out-of-sample results.
5. Add unit tests in `tests/unit/`.

Strategies are only merged if they hold up on data they weren't tuned on.

## Changes to the risk engine

The risk engine (`src/risk/`) can veto every order, so changes here get the closest review:

- Explain what the change protects against, or what it loosens and why.
- Add tests that show the limit triggering.
- Never remove or weaken a check without saying so in the PR.

## Pull requests

- Fork the repo, branch from `main`, and keep each PR to one change.
- Describe what changed and how you tested it.
- Never commit credentials, account numbers, broker logins or personal trade logs.
- Be kind in reviews and discussions.

## Questions

Open an issue with the `question` label.
