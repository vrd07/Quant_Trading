# MT5 Integration Tests - Quick Start Guide

## What Was Created

I've created a comprehensive integration test suite for the MT5Connector module:

### 📁 File Structure

```
tests/
├── __init__.py
└── integration/
    ├── __init__.py
    ├── conftest.py              # Pytest configuration & fixtures
    ├── README.md                # Detailed documentation
    └── test_mt5_connector.py    # Integration tests

src/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── constants.py             # Copied from algo_trading_system
│   ├── exceptions.py            # Copied from algo_trading_system  
│   └── types.py                 # Copied from algo_trading_system
└── connectors/
    ├── __init__.py
    ├── mt5_connector.py
    ├── heartbeat.py
    └── message_validator.py

pytest.ini                       # Pytest configuration
requirements-test.txt            # Testing dependencies
```

## 🚀 Quick Start

### 1. Install Testing Dependencies

```bash
pip install -r requirements-test.txt
```

Or manually:
```bash
pip install pytest pytest-cov
```

### 2. Ensure MT5 is Running

- Open MetaTrader 5
- Load the File Bridge EA (EA_FileBridge.mq5)
- Ensure it's active on any chart

### 3. Run the Tests

```bash
# Run all integration tests
pytest tests/integration/test_mt5_connector.py -v -s

# Run with detailed output
pytest tests/integration/test_mt5_connector.py -v -s --tb=short

# Run specific test class
pytest tests/integration/test_mt5_connector.py::TestMT5Connection -v -s

# Run specific test
pytest tests/integration/test_mt5_connector.py::TestMT5Connection::test_heartbeat -v -s
```

## 📋 Test Coverage

The test suite includes **8 test classes** with **20+ tests**:

### ✅ TestMT5Connection
- `test_connection_successful` - Verify MT5 connection works
- `test_heartbeat` - Test heartbeat mechanism
- `test_connection_health_check` - Verify health checking
- `test_disconnect_and_reconnect` - Test connection lifecycle

### ✅ TestAccountInfo
- `test_get_account_info` - Fetch and validate account data
- `test_account_info_validation` - Ensure proper validation

### ✅ TestPositions
- `test_get_positions` - Retrieve open positions
- `test_positions_validation` - Validate position data

### ✅ TestTickData
- `test_get_current_tick` - Get real-time tick data
- `test_tick_data_freshness` - Verify tick timestamps

### ✅ TestOrderPlacement (⚠️ SKIPPED BY DEFAULT)
- `test_place_market_order` - Place market orders (manual only)
- `test_close_position` - Close positions (manual only)

### ✅ TestHeartbeatMonitor
- `test_heartbeat_monitor_basic` - Basic monitoring functionality
- `test_heartbeat_monitor_callback` - Callback on connection loss
- `test_heartbeat_stop_start` - Monitor lifecycle

### ✅ TestSingletonPattern
- `test_singleton_connector` - Verify singleton pattern

### ✅ TestSymbolCache
- `test_symbol_cache` - Test symbol caching

### ✅ TestErrorHandling
- `test_invalid_symbol_tick` - Handle invalid symbols
- `test_connection_without_mt5` - Handle MT5 unavailable

## 🔒 Safety Features

1. **Order tests SKIPPED by default** - Prevents accidental trading
2. **Minimum position sizes** - Uses 0.01 lots in examples
3. **Proper cleanup** - Fixtures disconnect after tests
4. **Demo account recommended** - Never use live accounts
5. **Comprehensive logging** - Full audit trail

## 🎯 Example Output

When tests pass successfully:

```
tests/integration/test_mt5_connector.py::TestMT5Connection::test_connection_successful PASSED
tests/integration/test_mt5_connector.py::TestMT5Connection::test_heartbeat PASSED
tests/integration/test_mt5_connector.py::TestAccountInfo::test_get_account_info PASSED
tests/integration/test_mt5_connector.py::TestPositions::test_get_positions PASSED
tests/integration/test_mt5_connector.py::TestHeartbeatMonitor::test_heartbeat_monitor_basic PASSED

======================== 15 passed, 2 skipped in 12.34s =========================
```

## 🔧 Pytest Configuration

### pytest.ini
- Sets up Python path for imports
- Configures test discovery
- Defines custom markers (integration, unit)
- Enables verbose output by default

### conftest.py
- Auto-configures logging for all tests
- Automatically marks integration tests
- Sets up log capture

## 📝 Test Fixtures

### `connector` (module-scoped)
- Shared connection for all tests in a class
- Efficient for read-only operations
- Automatically connects/disconnects

### `fresh_connector` (function-scoped)
- New connection for each test
- Use when tests need isolation
- Good for connection lifecycle tests

## ⚠️ Important Notes

### Skipped Tests
Some tests are skipped by default to prevent real trading:
- `test_place_market_order` - Places real orders
- `test_close_position` - Closes real positions

To run manually (DEMO ACCOUNT ONLY):
```bash
pytest tests/integration/test_mt5_connector.py::TestOrderPlacement::test_place_market_order -v -s
```

### MT5 Must Be Running
All tests require:
1. MT5 running with demo/test account
2. File Bridge EA active
3. Accessible MT5 Common Files directory

### Continuous Integration
Skip integration tests in CI:
```bash
pytest -m "not integration"
```

## 🐛 Troubleshooting

### "MT5 not available"
- Ensure MT5 is running
- Check File Bridge EA is active
- Verify MT5 Common Files directory exists

### Connection Timeouts
- Check MT5 is responsive
- Verify EA is processing commands
- Review file permissions

### Import Errors
- Install test dependencies: `pip install -r requirements-test.txt`
- Ensure all `__init__.py` files are present
- Check `pytest.ini` pythonpath setting

## 📚 Additional Documentation

See `tests/integration/README.md` for:
- Detailed usage instructions
- Comprehensive troubleshooting guide
- Best practices
- Safety guidelines

## 🎓 Next Steps

1. **Install dependencies**: `pip install -r requirements-test.txt`
2. **Start MT5** with File Bridge EA
3. **Run tests**: `pytest tests/integration/test_mt5_connector.py -v -s`
4. **Review output** and ensure all pass
5. **Add more tests** as needed for your use cases

## 💡 Tips

- Use `-v` flag for verbose output
- Use `-s` flag to see print/log statements
- Use `--tb=short` for shorter tracebacks
- Use `-k` to run tests matching a pattern:
  ```bash
  pytest -k "heartbeat" -v -s
  ```
- Use `--collect-only` to see all tests without running:
  ```bash
  pytest tests/integration --collect-only
  ```

Happy Testing! 🚀
