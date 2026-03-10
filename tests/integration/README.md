# Integration Tests for MT5 Connector

This directory contains integration tests that verify the MT5Connector works correctly with an actual MT5 instance.

## Prerequisites

Before running these tests, ensure:

1. **MetaTrader 5 is running** with a demo or test account
2. **MT5 File Bridge EA is active** (the Expert Advisor that handles file-based communication)
3. **Python dependencies are installed**:
   ```bash
   pip install pytest
   ```

## Running the Tests

### Run All Integration Tests

```bash
pytest tests/integration/test_mt5_connector.py -v -s
```

- `-v`: Verbose output showing each test
- `-s`: Show print statements and logging output

### Run Specific Test Classes

```bash
# Test only connection functionality
pytest tests/integration/test_mt5_connector.py::TestMT5Connection -v -s

# Test only account info
pytest tests/integration/test_mt5_connector.py::TestAccountInfo -v -s

# Test heartbeat monitor
pytest tests/integration/test_mt5_connector.py::TestHeartbeatMonitor -v -s
```

### Run Specific Test

```bash
pytest tests/integration/test_mt5_connector.py::TestMT5Connection::test_connection_successful -v -s
```

### Run Only Integration Tests (Skip Unit Tests)

```bash
pytest -m integration -v -s
```

### Skip Integration Tests (Run Only Unit Tests)

```bash
pytest -m "not integration" -v
```

## Test Coverage

The integration tests cover:

### ✅ Connection Management
- Basic connection to MT5
- Heartbeat mechanism
- Health checking
- Disconnect/reconnect functionality

### ✅ Account Information
- Fetching account balance, equity, margin
- Data type validation
- Field presence validation

### ✅ Position Management
- Retrieving open positions
- Position data structure validation
- Empty positions handling

### ✅ Tick Data
- Real-time tick retrieval
- Bid/ask validation
- Data freshness checks

### ✅ Heartbeat Monitor
- Background monitoring
- Health status tracking
- Start/stop functionality
- Callback functionality

### ✅ Order Placement (Skipped by Default)
- Market order placement (manual only)
- Position closing (manual only)

**⚠️ WARNING**: Order placement tests are SKIPPED by default to prevent accidental real trading. Only run them manually on DEMO accounts!

## Skipped Tests

Some tests are skipped by default because they modify real positions:

- `test_place_market_order`: Places real orders
- `test_close_position`: Closes real positions

To run these tests manually on a **DEMO ACCOUNT ONLY**:

```bash
# Run specific order test
pytest tests/integration/test_mt5_connector.py::TestOrderPlacement::test_place_market_order -v -s
```

**NEVER run these tests on a live account!**

## Test Fixtures

### `connector` (module-scoped)
- Shared connection across all tests in a class
- More efficient for read-only tests
- Automatically connects and disconnects

### `fresh_connector` (function-scoped)
- New connection for each test
- Use when tests need isolation
- Useful for connection/disconnection tests

## Expected Output

Successful test run should show:

```
tests/integration/test_mt5_connector.py::TestMT5Connection::test_connection_successful PASSED
tests/integration/test_mt5_connector.py::TestMT5Connection::test_heartbeat PASSED
tests/integration/test_mt5_connector.py::TestAccountInfo::test_get_account_info PASSED
...
```

## Troubleshooting

### "MT5 not available" Error

If tests are skipped with "MT5 not available":

1. Ensure MT5 is running
2. Check that the File Bridge EA is active (should show in MT5 Experts tab)
3. Verify the MT5 Common Files directory is accessible
4. Check MT5 logs for any errors

### Connection Timeout

If heartbeat tests fail:

1. Check MT5 is responding (not frozen)
2. Verify File Bridge EA is processing commands
3. Check file permissions in MT5 Common Files directory

### Test Failures

If specific tests fail:

1. Check MT5 account status (demo account active?)
2. Verify internet connection (for demo account data)
3. Check MT5 logs for errors
4. Review test output for specific error messages

## Continuous Integration

To integrate with CI/CD:

1. **Skip integration tests** in CI by default:
   ```bash
   pytest -m "not integration"
   ```

2. **Run integration tests** only in specific environments:
   ```bash
   # Only if MT5_AVAILABLE environment variable is set
   if [ "$MT5_AVAILABLE" = "true" ]; then
     pytest -m integration
   fi
   ```

## Best Practices

1. **Always use DEMO accounts** for testing
2. **Review each test** before running manually skipped tests
3. **Monitor MT5** during test execution
4. **Clean up positions** after testing order placement
5. **Check logs** for any warnings or errors

## Safety Features

- All order tests are **skipped by default**
- Tests use **minimum position sizes** (0.01 lots)
- Proper **cleanup in fixtures** (disconnect after tests)
- **Comprehensive logging** for audit trail
- **Validation** of all data from MT5

## Support

If you encounter issues:

1. Check MT5 Expert Advisor logs
2. Review Python test output with `-v -s` flags
3. Enable debug logging in MT5Connector
4. Verify MT5 File Bridge EA configuration
