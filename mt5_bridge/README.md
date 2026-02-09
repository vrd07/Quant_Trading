# MT5 ZeroMQ Bridge

A high-performance communication bridge between Python and MetaTrader 5 using ZeroMQ sockets.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         MetaTrader 5                                 │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                   EA_ZeroMQ_Bridge.mq5                         │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │  │
│  │  │  REP Socket │  │ PUSH Socket │  │  PUB Socket │            │  │
│  │  │   :5555     │  │    :5556    │  │    :5557    │            │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘            │  │
│  └─────────┼────────────────┼────────────────┼───────────────────┘  │
└────────────┼────────────────┼────────────────┼──────────────────────┘
             │                │                │
             │ TCP            │ TCP            │ TCP
             ▼                ▼                ▼
┌────────────────────────────────────────────────────────────────────┐
│                           Python                                    │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                    mt5_zmq_client.py                           │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │ │
│  │  │  REQ Socket │  │ PULL Socket │  │  SUB Socket │            │ │
│  │  │  Commands   │  │   Fills     │  │   Ticks     │            │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘            │ │
│  └───────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

## Sockets Overview

| Socket | Port | Type | Direction | Purpose |
|--------|------|------|-----------|---------|
| REP/REQ | 5555 | Request/Reply | Bidirectional | Send commands, receive responses |
| PUSH/PULL | 5556 | Push | MT5 → Python | Fill confirmations, error messages |
| PUB/SUB | 5557 | Publish/Subscribe | MT5 → Python | Real-time tick data stream |

## Installation

### 1. MetaTrader 5 Dependencies

Download and install the following MQL5 libraries into your `MQL5/Include/` folder:

#### ZeroMQ Bindings (mql-zmq)
```bash
# Download from: https://github.com/dingmaotu/mql-zmq
# Copy the following to MQL5/Include/:
#   - Zmq/Zmq.mqh
#   - Zmq/*.mqh files
# Copy DLLs to MQL5/Libraries/:
#   - libzmq.dll (or libzmq-v142-mt-4_3_5.dll)
#   - libsodium.dll
```

#### JSON Library (JAson.mqh)
```bash
# Download from: https://www.mql5.com/en/code/13663
# Copy to MQL5/Include/:
#   - JAson.mqh
```

### 2. Enable DLL Imports in MT5

1. Open MetaTrader 5
2. Go to **Tools → Options → Expert Advisors**
3. Check **Allow DLL imports**
4. Check **Allow automated trading**

### 3. Python Dependencies

```bash
pip install pyzmq
```

### 4. Deploy the EA

1. Copy `EA_ZeroMQ_Bridge.mq5` to `MQL5/Experts/`
2. Compile in MetaEditor
3. Attach to a chart (any symbol)
4. Verify logs show successful socket binding

## Usage

### Basic Python Client

```python
from mt5_zmq_client import MT5ZmqClient

# Connect to MT5
client = MT5ZmqClient()
if client.connect():
    # Check connection
    heartbeat = client.heartbeat()
    print(f"Status: {heartbeat['status']}")
    
    # Get account info
    account = client.get_account_info()
    print(f"Balance: {account.balance}")
    
    # Get positions
    positions = client.get_positions()
    for pos in positions:
        print(f"{pos.symbol}: {pos.unrealized_pnl}")
    
    # Disconnect
    client.disconnect()
```

### Place an Order

```python
result = client.place_order(
    symbol="XAUUSD",
    side="BUY",
    quantity=0.1,
    stop_loss=1990.0,
    take_profit=2010.0
)

if result.is_success:
    print(f"Order placed: {result.order_id}")
else:
    print(f"Order failed: {result.error}")
```

### Close a Position

```python
result = client.close_position(position_id="123456")

if result.is_success:
    print(f"Closed with PnL: {result.realized_pnl}")
```

### Subscribe to Tick Data

```python
def on_tick(tick):
    print(f"{tick.symbol}: Bid={tick.bid}, Ask={tick.ask}")

client.subscribe_ticks(on_tick)

# Keep running
import time
while True:
    time.sleep(1)
```

### Subscribe to Fill Confirmations

```python
def on_fill(fill):
    print(f"Filled: {fill.side} {fill.filled_quantity} @ {fill.filled_price}")

client.subscribe_fills(on_fill)
```

## API Reference

### Commands (REP/REQ Socket)

| Command | Payload | Response |
|---------|---------|----------|
| `HEARTBEAT` | None | `{"status": "ALIVE", "timestamp": "..."}` |
| `PLACE_ORDER` | `{symbol, side, quantity, order_type, stop_loss, take_profit}` | `{"order_id": "...", "status": "ACCEPTED"}` |
| `CLOSE_POSITION` | `{position_id}` | `{"status": "CLOSED", "realized_pnl": 123.45}` |
| `GET_POSITIONS` | None | `{"positions": [...], "count": N}` |
| `GET_ACCOUNT_INFO` | None | `{balance, equity, margin, free_margin, ...}` |
| `MODIFY_ORDER` | `{position_id, stop_loss, take_profit}` | `{"status": "MODIFIED"}` |

### Tick Data (PUB Socket)

```json
{
  "type": "TICK",
  "symbol": "XAUUSD",
  "timestamp": "2026-01-21T14:30:00.123Z",
  "bid": 2005.50,
  "ask": 2005.60,
  "last": 2005.55,
  "volume": 1250
}
```

### Fill Confirmation (PUSH Socket)

```json
{
  "type": "FILL",
  "order_id": "123456",
  "position_id": "789012",
  "symbol": "XAUUSD",
  "side": "BUY",
  "filled_quantity": 0.1,
  "filled_price": 2005.55,
  "commission": 0.0,
  "timestamp": "2026-01-21T14:30:00.456Z"
}
```

## Configuration

### EA Input Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `REP_PORT` | 5555 | REP socket port |
| `PUSH_PORT` | 5556 | PUSH socket port |
| `PUB_PORT` | 5557 | PUB socket port |
| `VERBOSE_LOGGING` | true | Enable detailed logging |
| `RECV_TIMEOUT` | 1 | Receive timeout (ms) |
| `BIND_ADDRESS` | 127.0.0.1 | Socket bind address |

### Python Client Parameters

```python
client = MT5ZmqClient(
    host="127.0.0.1",      # MT5 host
    rep_port=5555,         # REP socket port
    push_port=5556,        # PUSH socket port
    pub_port=5557,         # PUB socket port
    request_timeout=5000,  # Request timeout (ms)
)
```

## Troubleshooting

### EA won't compile

1. Ensure `Zmq/Zmq.mqh` is in `MQL5/Include/Zmq/`
2. Ensure `JAson.mqh` is in `MQL5/Include/`
3. Check for missing DLLs in `MQL5/Libraries/`

### Socket binding fails

1. Check if ports are already in use
2. Verify firewall allows localhost connections
3. Try different port numbers

### Python connection timeout

1. Verify EA is attached and running
2. Check EA logs for socket binding success
3. Ensure ports match between EA and Python client

### Orders rejected

1. Check MT5 terminal for detailed error
2. Verify symbol is valid and tradeable
3. Check margin requirements
4. Verify market is open

## File Structure

```
mt5_bridge/
├── EA_ZeroMQ_Bridge.mq5   # MetaTrader 5 Expert Advisor
├── mt5_zmq_client.py      # Python client library
└── README.md              # This file
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request
