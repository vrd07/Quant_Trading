# MT5 Python Trading Bridge

A cross-platform communication bridge between **Python** and **MetaTrader 5** for algorithmic trading of XAUUSD (Gold) and BTC.

Two bridge modes are available:

| Mode | File | Communication | Platform | Dependencies |
|------|------|---------------|----------|-------------|
| **File Bridge** (Recommended) | `EA_FileBridge.mq5` | Shared JSON files | ✅ Windows + macOS | None |
| ZeroMQ Bridge | `EA_ZeroMQ_Bridge.mq5` | TCP sockets | ⚠️ Windows only | ZeroMQ, JAson.mqh |

> **The File Bridge is the recommended mode** — it works on both Windows and macOS (via Wine/CrossOver), requires zero external libraries, and is production-tested.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     MetaTrader 5 (EA_FileBridge.mq5)         │
│                                                              │
│  Reads: mt5_commands.json    Writes: mt5_status.json         │
│                                      mt5_responses.json      │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Risk Management Layer                                 │  │
│  │  • Max positions, daily loss/profit limits              │  │
│  │  • Kill switch, panic close, trading hours              │  │
│  │  • Order validation, exposure limits                    │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────┬────────────────────────────────────┘
                          │
              Shared JSON Files (MT5 Common Files Directory)
                          │
┌─────────────────────────┴────────────────────────────────────┐
│                  Python (mt5_file_client.py)                  │
│                                                              │
│  Writes: mt5_commands.json   Reads: mt5_status.json          │
│                                     mt5_responses.json       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Trading Engine                                        │  │
│  │  • Strategy Engine (Breakout + Mean Reversion)          │  │
│  │  • Risk Engine, Portfolio Engine, Data Engine            │  │
│  │  • State Management, Monitoring & Logging               │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### How the File Bridge Works

1. **Python** writes a command to `mt5_commands.json` in the MT5 Common Files directory
2. **EA** polls the file every 100ms, detects new commands, and executes them
3. **EA** writes the result to `mt5_responses.json`
4. **Python** reads the response file
5. **EA** also writes a live status file (`mt5_status.json`) every 1 second with account info, prices, and all Market Watch quotes

---

## Setup — Windows

### 1. Install MetaTrader 5

1. Download and install [MetaTrader 5](https://www.metatrader5.com/en/download) from the official site
2. Log in to your broker account (demo or live)

### 2. Deploy the EA

1. Open MetaTrader 5
2. Press `F4` to open **MetaEditor**
3. Copy `EA_FileBridge.mq5` into your `MQL5/Experts/` folder
   - To find this folder: **File → Open Data Folder** in MT5, then navigate to `MQL5/Experts/`
4. In MetaEditor, open the file and press `F7` to compile
5. Verify: **0 errors, 0 warnings** in the output panel

### 3. Enable Automated Trading

1. In MT5, go to **Tools → Options → Expert Advisors**
2. Check **Allow automated trading**
3. Click the **Algo Trading** button in the toolbar (it should turn green)

### 4. Attach EA to Chart

1. Open the **Navigator** panel (`Ctrl+N`)
2. Find **EA_FileBridge** under Expert Advisors
3. Drag it onto any open chart (e.g., XAUUSD)
4. In the popup dialog, go to the **Inputs** tab to configure risk parameters (see [EA Configuration](#ea-configuration))
5. Click **OK**
6. Verify the EA is running: check the **Experts** tab in the Toolbox (`Ctrl+T`) for `EA_FileBridge v3.0 PRODUCTION` messages

### 5. Install Python Dependencies

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### 6. Common Files Directory (Windows)

The File Bridge communicates through the MT5 **Common Files** directory:

```
C:\Users\<YourUser>\AppData\Roaming\MetaQuotes\Terminal\Common\Files\
```

The Python client auto-detects this on Windows.

---

## Setup — macOS

MT5 does not have a native macOS client. You can run it via **Wine** or **CrossOver**.

### 1. Install MT5 via Wine/CrossOver

#### Option A: CrossOver (Recommended — easiest)

1. Download [CrossOver](https://www.codeweavers.com/crossover) (paid, free trial available)
2. Install MetaTrader 5 as a Windows application inside a CrossOver bottle
3. Log in to your broker account

#### Option B: Wine (Free)

1. Install Wine via Homebrew:
   ```bash
   brew install --cask wine-stable
   ```
2. Download the MT5 installer `.exe`
3. Run: `wine mt5setup.exe`
4. Follow the installation wizard

### 2. Deploy the EA

1. Open MT5 inside Wine/CrossOver
2. Press `F4` to open MetaEditor
3. Copy `EA_FileBridge.mq5` into the `MQL5/Experts/` folder
   - **Finding the folder**: In MT5, go to **File → Open Data Folder**, then `MQL5/Experts/`
   - The Wine path is typically:
     ```
     ~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/
     Program Files/MetaTrader 5/MQL5/Experts/
     ```
4. Compile with `F7` — should show **0 errors** (no external dependencies needed!)

### 3. Enable Automated Trading

Same as Windows — enable in **Tools → Options → Expert Advisors** and click **Algo Trading**.

### 4. Attach EA to Chart

Same as Windows steps 4.1–4.6 above.

### 5. Install Python Dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 6. Common Files Directory (macOS/Wine)

The MT5 Common Files directory under Wine is at:

```
~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/
```

The Python `MT5FileClient` auto-detects this path on macOS. If your Wine prefix is different, pass the path manually:

```python
client = MT5FileClient(data_dir="/path/to/your/Common/Files")
```

---

## EA Configuration

The EA has extensive input parameters organized by category:

### Emergency Controls

| Parameter | Default | Description |
|-----------|---------|-------------|
| `EnableTrading` | `true` | **Master kill switch** — set to `false` to stop all trading |
| `PanicCloseAll` | `false` | Set to `true` to immediately close all positions |

### Position Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MaxOpenPositions` | `10` | Maximum simultaneous open positions |
| `MaxPositionSizePercent` | `1.0` | Maximum risk per trade (% of balance) |
| `MaxTotalExposureLots` | `5.0` | Maximum total lots across all positions |

### Daily Limits

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MaxDailyLossPercent` | `3.0` | Stop trading if daily loss exceeds this % |
| `MaxDailyProfitPercent` | `10.0` | Stop trading if daily profit exceeds this % |
| `MaxTradesPerDay` | `50` | Maximum number of trades allowed per day |

### Trading Hours

| Parameter | Default | Description |
|-----------|---------|-------------|
| `UseTradingHours` | `false` | Enable/disable trading hours restriction |
| `TradingStartHour` | `9` | Trading start hour (broker time) |
| `TradingEndHour` | `17` | Trading end hour (broker time) |
| `AvoidFridayClose` | `true` | Stop trading 2 hours before Friday market close |

### Execution

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MaxSlippagePips` | `5` | Alert if slippage exceeds this value |
| `MaxRetries` | `3` | Maximum order retry attempts |
| `CommandCheckIntervalMs` | `100` | Poll for Python commands every N ms |
| `StatusUpdateIntervalMs` | `1000` | Write status file every N ms |

---

## Python Client Usage

### Basic Connection Test

```python
from mt5_bridge.mt5_file_client import MT5FileClient

client = MT5FileClient()

# Check if EA is alive
status = client.get_status()
print(f"Status: {status['status']}")
print(f"Balance: ${status['balance']}")
print(f"Symbol: {status['symbol']}")
print(f"Bid: {status['bid']}, Ask: {status['ask']}")
```

### Heartbeat

```python
response = client.heartbeat()
print(f"EA Status: {response['status']}")  # "ALIVE"
```

### Account Info

```python
account = client.get_account_info()
print(f"Balance:     ${account['balance']}")
print(f"Equity:      ${account['equity']}")
print(f"Free Margin: ${account['free_margin']}")
print(f"Daily P&L:   ${account['daily_pnl']}")
```

### Place an Order

```python
result = client.place_order(
    symbol="XAUUSD",
    order_type="BUY",
    volume=0.01,
    sl=1990.0,   # Stop Loss price
    tp=2010.0    # Take Profit price
)

if result.get("status") == "SUCCESS":
    print(f"Order placed! Ticket: {result['ticket']}")
    print(f"Fill price: {result['price']}")
    print(f"Slippage: {result['slippage_pips']} pips")
else:
    print(f"Order failed: {result.get('message')}")
```

### Close a Position

```python
result = client.close_position(ticket=123456)

if result.get("status") == "SUCCESS":
    print(f"Closed with P&L: ${result['pnl']}")
```

### Get Open Positions

```python
positions = client.get_positions()
for pos in positions.get("positions", []):
    print(f"#{pos['ticket']} | {pos['symbol']} | "
          f"{'BUY' if pos['type'] == 0 else 'SELL'} | "
          f"{pos['volume']} lots | P&L: ${pos['profit']}")
```

### Multi-Symbol Quotes

The status file includes live quotes for **all symbols in your Market Watch**:

```python
status = client.get_status()
quotes = status.get("quotes", {})

for symbol, data in quotes.items():
    print(f"{symbol}: Bid={data['bid']} Ask={data['ask']}")
```

---

## API Reference

### Commands (Python → EA)

| Command | Parameters | Response |
|---------|------------|----------|
| `HEARTBEAT` | — | `{"status": "ALIVE", "server_time": "..."}` |
| `GET_ACCOUNT_INFO` | — | `{balance, equity, margin, free_margin, currency, leverage, daily_pnl, daily_trades}` |
| `GET_POSITIONS` | — | `{"positions": [{ticket, symbol, type, volume, price_open, price_current, profit, sl, tp, comment}]}` |
| `PLACE_ORDER` | `symbol, order_type, volume, sl?, tp?` | `{"status": "SUCCESS", "ticket": N, "price": X, "slippage_pips": Y}` |
| `CLOSE_POSITION` | `ticket` | `{"status": "SUCCESS", "pnl": X}` |
| `GET_LIMITS` | — | Current risk limits and usage (positions, exposure, daily stats) |

### Status File Format (EA → Python, every 1s)

```json
{
  "status": "ALIVE",
  "timestamp": "2026.02.26 10:30:00",
  "symbol": "XAUUSD",
  "bid": 2005.50,
  "ask": 2005.60,
  "balance": 10000.00,
  "equity": 10050.00,
  "margin": 200.00,
  "free_margin": 9850.00,
  "trading_enabled": true,
  "daily_pnl": 50.00,
  "daily_trades": 3,
  "open_positions": 2,
  "total_exposure": 0.05,
  "quotes": {
    "XAUUSD": {"bid": 2005.50, "ask": 2005.60, "time": 1740561000},
    "BTCUSD": {"bid": 95000.00, "ask": 95050.00, "time": 1740561000}
  }
}
```

---

## Project Structure

```
Quant_trading/
├── mt5_bridge/                    # MT5 ↔ Python communication
│   ├── EA_FileBridge.mq5          # ✅ File-based EA (recommended)
│   ├── EA_ZeroMQ_Bridge.mq5       # ZeroMQ-based EA (Windows only)
│   ├── mt5_file_client.py         # Python client for File Bridge
│   ├── mt5_zmq_client.py          # Python client for ZMQ Bridge
│   ├── test_connection.py         # Connection test script
│   └── README.md                  # This file
│
├── src/                           # Core trading engine
│   ├── main.py                    # Main entry point & orchestrator
│   ├── connectors/                # MT5 connector abstraction
│   ├── core/                      # Types, constants, exceptions
│   ├── data/                      # Data engine & indicators
│   ├── strategies/                # Breakout + Mean Reversion strategies
│   ├── risk/                      # Risk engine & position sizing
│   ├── execution/                 # Order execution engine
│   ├── portfolio/                 # Portfolio & position tracking
│   ├── state/                     # State persistence & recovery
│   ├── monitoring/                # Logging, metrics, dashboard
│   └── backtest/                  # Event-driven backtester
│
├── config/                        # Configuration files
│   ├── config.yaml                # Base configuration
│   ├── config_dev.yaml            # Development overrides
│   ├── config_paper.yaml          # Paper trading config
│   └── config_live.yaml           # Live trading config
│
├── scripts/                       # Utility scripts
│   ├── run_backtest.py            # Run backtests
│   ├── live_trade.py              # Start live trading
│   ├── paper_trade.py             # Start paper trading
│   ├── view_journal.py            # View trade journal
│   ├── check_bridge.py            # Check MT5 bridge status
│   ├── close_all_positions.py     # Emergency close all
│   └── ...                        # Diagnostic & data scripts
│
├── tests/                         # Test suite
│   ├── integration/               # MT5 integration tests
│   └── unit/                      # Unit tests
│
├── data/                          # Runtime data (gitignored except historical/)
│   └── historical/                # Historical price data for backtesting
│
├── requirements.txt               # Python dependencies
├── requirements-test.txt          # Testing dependencies
├── pytest.ini                     # Pytest configuration
├── run_live.sh                    # Shell script to start live trading
└── .gitignore                     # Git exclusions
```

---

## Running the System

### Paper Trading (Safe — no real orders)

```bash
source venv/bin/activate  # or venv\Scripts\activate on Windows
python scripts/paper_trade.py
```

### Live Trading

> ⚠️ **Ensure MT5 is running with EA_FileBridge attached before starting.**

```bash
source venv/bin/activate
python src/main.py --env live --force-live
```

Or use the shell script:
```bash
./run_live.sh
```

### Backtesting

```bash
source venv/bin/activate
python scripts/run_backtest.py
```

---

## Troubleshooting

### EA won't compile

| Error | Solution |
|-------|----------|
| `EA_FileBridge.mq5` shows errors | This EA has **zero external dependencies** — ensure you're using MT5 (MQL5), not MT4 |
| `EA_ZeroMQ_Bridge.mq5` shows errors | Install ZeroMQ (`Zmq/Zmq.mqh`) in `MQL5/Include/` and DLLs in `MQL5/Libraries/` |

### Python can't find status file

| Platform | Check |
|----------|-------|
| **Windows** | Verify `C:\Users\<You>\AppData\Roaming\MetaQuotes\Terminal\Common\Files\mt5_status.json` exists |
| **macOS** | Verify `~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/mt5_status.json` exists |
| **Both** | Ensure the EA is attached to a chart and running (check Experts tab) |

### Orders rejected

1. Check the **Experts** tab in MT5 for detailed error messages
2. Verify the symbol is valid and tradeable (market hours)
3. Check margin requirements — `ValidateOrder` logs detailed reasons
4. Ensure `EnableTrading` is `true` in EA inputs
5. Check daily limits haven't been reached (`GET_LIMITS` command)

### Connection timeouts

1. Verify the EA is actively processing (status timestamp should update every second)
2. Check file permissions on the Common Files directory
3. On macOS, ensure Wine/CrossOver is running and MT5 is not frozen

### Performance issues

- Reduce `StatusUpdateIntervalMs` to write status less frequently (e.g., 2000ms)
- The default `CommandCheckIntervalMs` of 100ms provides ~10ms effective latency

---

## ZeroMQ Bridge (Alternative — Windows Only)

If you're on Windows and prefer socket-based communication, see the `EA_ZeroMQ_Bridge.mq5` and `mt5_zmq_client.py` files. This requires:

1. [mql-zmq](https://github.com/dingmaotu/mql-zmq) library installed in `MQL5/Include/Zmq/`
2. ZeroMQ DLLs (`libzmq.dll`, `libsodium.dll`) in `MQL5/Libraries/`
3. [JAson.mqh](https://www.mql5.com/en/code/13663) in `MQL5/Include/`
4. `pip install pyzmq` on the Python side

See `README_SETUP.md` for detailed ZeroMQ setup instructions.

---

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request
