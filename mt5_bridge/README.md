# MT5 Python Trading Bridge

A cross-platform algorithmic trading system that connects **Python** (strategy engine) to **MetaTrader 5** (execution & data feed) for automated trading of XAUUSD (Gold) and BTC.

---

## 🔰 Quick Start (Windows — Beginner Friendly)

**If you're new to this, follow the [Windows Setup Guide (README_SETUP.md)](./README_SETUP.md)** — it has detailed step-by-step instructions with every click explained.

---

## How It Works

This system uses **two programs working together**:

1. **MetaTrader 5** — Your trading platform (connects to your broker, shows charts, executes trades)
2. **Python Script** — The "brain" that analyzes markets and decides when to buy/sell

They communicate through **shared files** — Python writes commands, MT5 reads and executes them:

```
┌──────────────────────────────────────────────────────────────┐
│                     MetaTrader 5 (EA_FileBridge)             │
│                                                              │
│  Reads: mt5_commands.json    Writes: mt5_status.json         │
│                                      mt5_responses.json      │
│                                                              │
│  Built-in Safety:                                            │
│  • Max 10 positions, daily loss/profit limits                │
│  • Kill switch, panic close, trading hours                   │
│  • Order validation, margin checks                           │
└─────────────────────────┬────────────────────────────────────┘
                          │
            Shared JSON Files (auto-created by MT5)
                          │
┌─────────────────────────┴────────────────────────────────────┐
│                     Python Trading Engine                     │
│                                                              │
│  Strategies: Breakout, Mean Reversion, VWAP, Momentum        │
│  Safety: Risk engine, position sizing, circuit breaker        │
│  Monitoring: Trade journal, performance dashboard             │
└──────────────────────────────────────────────────────────────┘
```

---

## Setup Overview

| Platform | Difficulty | Guide |
|----------|-----------|-------|
| **Windows** | ✅ Easy | [README_SETUP.md](./README_SETUP.md) — Full step-by-step guide |
| **macOS** | ⚠️ Intermediate | See [macOS Setup](#setup--macos) below |

---

## Setup — Windows (Summary)

> **📖 For detailed instructions, see [README_SETUP.md](./README_SETUP.md)**

1. Install [Python 3.10+](https://www.python.org/downloads/) (check "Add to PATH")
2. Install [MetaTrader 5](https://www.metatrader5.com/en/download) and log in to your broker
3. Copy `EA_FileBridge.mq5` into MT5 → compile → attach to chart
4. Open Command Prompt in project folder → `pip install -r requirements.txt`
5. Run: `python src/main.py --env live --force-live`

---

## Setup — macOS

MT5 doesn't have a native macOS app. You run it via **Wine** or **CrossOver**.

### 1. Install MT5 via CrossOver (Recommended)

1. Download [CrossOver](https://www.codeweavers.com/crossover) (paid, free trial available)
2. Install MetaTrader 5 as a Windows application inside a CrossOver bottle
3. Log in to your broker account

### 2. Deploy the EA

1. Open MT5 inside CrossOver
2. Press `F4` → MetaEditor opens
3. Copy `EA_FileBridge.mq5` to `MQL5/Experts/`
   - Wine path: `~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/`
4. Compile with `F7` → should show **0 errors**

### 3. Enable Trading & Attach EA

1. Enable: **Tools → Options → Expert Advisors** → check **Allow automated trading**
2. Click **Algo Trading** button in toolbar (turns green)
3. Drag `EA_FileBridge` from Navigator onto any chart → click OK

### 4. Run Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 src/main.py --env live --force-live
```

### macOS Common Files Path

```
~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/
```

The Python client auto-detects this. If different, override:
```python
client = MT5FileClient(data_dir="/your/path/to/Common/Files")
```

---

## EA Configuration (EA_FileBridge.mq5)

When you attach the EA to a chart, you can configure these settings in the **Inputs** tab:

### Emergency Controls

| Setting | Default | What It Does |
|---------|---------|-------------|
| `EnableTrading` | ✅ ON | **Master switch** — turn OFF to stop all trading instantly |
| `PanicCloseAll` | ❌ OFF | Turn ON to close ALL positions immediately (emergency) |

### Position & Risk Limits

| Setting | Default | What It Does |
|---------|---------|-------------|
| `MaxOpenPositions` | `10` | Maximum trades open at once |
| `MaxPositionSizePercent` | `1.0%` | Max risk per single trade (% of balance) |
| `MaxTotalExposureLots` | `5.0` | Max total lot size across all trades |

### Daily Limits

| Setting | Default | What It Does |
|---------|---------|-------------|
| `MaxDailyLossPercent` | `3.0%` | Stop trading if daily loss exceeds this |
| `MaxDailyProfitPercent` | `10.0%` | Stop trading if daily profit exceeds this (lock in profits) |
| `MaxTradesPerDay` | `50` | Maximum trades per day |

### Trading Hours

| Setting | Default | What It Does |
|---------|---------|-------------|
| `UseTradingHours` | ❌ OFF | Enable to restrict trading to specific hours |
| `TradingStartHour` | `9` | Start hour (broker time) |
| `TradingEndHour` | `17` | End hour (broker time) |
| `AvoidFridayClose` | ✅ ON | Stop trading 2h before Friday market close |

---

## Trading Strategies

The system runs four strategies automatically based on market conditions:

| Strategy | When Active | What It Does |
|----------|------------|-------------|
| **Breakout** | Trending market | Buys/sells when price breaks above/below recent highs/lows |
| **Mean Reversion** | Ranging market | Buys when price is unusually low, sells when unusually high |
| **VWAP Deviation** | Ranging market | Trades based on price deviation from volume-weighted average |
| **Momentum** | Trending market | Follows strong price momentum using RSI and MACD |

A **Regime Filter** automatically detects whether the market is trending or ranging, and only activates the appropriate strategies.

---

## Commands Reference

### Python → MT5 Commands

| Command | What It Does |
|---------|-------------|
| `HEARTBEAT` | Check if EA is alive |
| `GET_ACCOUNT_INFO` | Get balance, equity, margin |
| `GET_POSITIONS` | Get all open trades |
| `PLACE_ORDER` | Open a new trade |
| `CLOSE_POSITION` | Close a specific trade |
| `GET_LIMITS` | Check current risk limits and usage |

### Status File (Updates every 1 second)

The EA writes a status file with live data including:
- Account balance, equity, margin
- All Market Watch symbol prices (bid/ask)
- Daily P&L and trade count
- Current open positions and exposure

---

## Project Structure

```
Quant_trading/
├── mt5_bridge/                    # MT5 ↔ Python communication
│   ├── EA_FileBridge.mq5          # ✅ Expert Advisor (recommended)
│   ├── mt5_file_client.py         # Python client for File Bridge
│   ├── README.md                  # This file
│   └── README_SETUP.md            # Detailed Windows setup guide
│
├── src/                           # Trading engine
│   ├── main.py                    # Main entry point
│   ├── strategies/                # Trading strategies
│   ├── risk/                      # Risk management
│   ├── execution/                 # Order execution
│   ├── portfolio/                 # Position tracking
│   ├── data/                      # Market data engine
│   └── monitoring/                # Logging & dashboard
│
├── config/                        # Configuration
│   ├── config.yaml                # Base config
│   ├── config_live.yaml           # Live trading config
│   └── config_paper.yaml          # Paper trading config
│
├── scripts/                       # Utilities
│   ├── run_backtest.py            # Run backtests
│   ├── view_journal.py            # View trade history
│   └── close_all_positions.py     # Emergency close all
│
├── requirements.txt               # Python dependencies
└── .gitignore                     # Git exclusions
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| EA won't compile | Make sure you're using MT5 (not MT4). EA_FileBridge has zero external dependencies. |
| Python can't find status file | Make sure the EA is attached to a chart and running. Check the Experts tab for logs. |
| "Trading not allowed" errors | Set `EnableTrading = true` in EA inputs. Click Algo Trading button (must be green). |
| Orders rejected | Check margin requirements, verify market is open, check daily limits. |
| System says "Waiting for data" | Normal — it needs ~10 bars (10 minutes on 1m timeframe) to start trading. |
| Python imports are slow | Normal on Python 3.14 — first launch takes 3-5 mins. Subsequent runs are faster. |

---

## License

MIT License
