"""Unit tests for the MT5Connector quote-staleness guard.

A frozen MT5 feed repeats the same bid/ask while the bridge stamps every tick
with now(), so flat O=H=L=C bars get built and NaN-out indicators. The guard
flags this so callers can skip evaluation. These tests drive the pure logic via
a fake monotonic clock, bypassing the MT5 client.
"""
import pytest

from src.connectors.mt5_connector import MT5Connector


@pytest.fixture
def conn(monkeypatch):
    """A connector with only the staleness state wired (no MT5 client)."""
    c = MT5Connector.__new__(MT5Connector)
    c.stale_quote_seconds = 120.0
    c._last_quote = {}
    c._last_quote_change = {}
    c._stale_symbols = set()
    c._last_stale_warn = {}

    clock = {"t": 1000.0}
    monkeypatch.setattr(
        "src.connectors.mt5_connector.time.monotonic", lambda: clock["t"]
    )
    return c, clock


def test_changing_quote_is_never_stale(conn):
    c, clock = conn
    for i in range(10):
        clock["t"] += 30.0
        assert c._check_quote_staleness("XAUUSD", 4339.0 + i, 4339.2 + i) is False
        assert c.is_quote_stale("XAUUSD") is False


def test_frozen_quote_flags_after_threshold(conn):
    c, clock = conn
    # First sighting establishes the baseline; not yet stale.
    assert c._check_quote_staleness("XAUUSD", 4339.0, 4339.2) is False
    # Still within threshold.
    clock["t"] += 60.0
    assert c._check_quote_staleness("XAUUSD", 4339.0, 4339.2) is False
    assert c.is_quote_stale("XAUUSD") is False
    # Cross the 120s threshold with the same quote.
    clock["t"] += 65.0
    assert c._check_quote_staleness("XAUUSD", 4339.0, 4339.2) is True
    assert c.is_quote_stale("XAUUSD") is True


def test_feed_resume_clears_stale(conn):
    c, clock = conn
    c._check_quote_staleness("XAUUSD", 4339.0, 4339.2)
    clock["t"] += 200.0
    assert c._check_quote_staleness("XAUUSD", 4339.0, 4339.2) is True
    # A new quote clears the stale flag.
    clock["t"] += 5.0
    assert c._check_quote_staleness("XAUUSD", 4340.0, 4340.2) is False
    assert c.is_quote_stale("XAUUSD") is False


def test_staleness_is_per_symbol(conn):
    c, clock = conn
    c._check_quote_staleness("XAUUSD", 4339.0, 4339.2)
    c._check_quote_staleness("USDJPY", 160.0, 160.01)
    clock["t"] += 130.0
    # XAUUSD freezes, USDJPY keeps moving.
    assert c._check_quote_staleness("XAUUSD", 4339.0, 4339.2) is True
    assert c._check_quote_staleness("USDJPY", 160.05, 160.06) is False
    assert c.is_quote_stale("XAUUSD") is True
    assert c.is_quote_stale("USDJPY") is False


def test_warn_is_rate_limited(conn, caplog):
    c, clock = conn
    c._check_quote_staleness("XAUUSD", 4339.0, 4339.2)
    clock["t"] += 130.0
    with caplog.at_level("WARNING"):
        c._check_quote_staleness("XAUUSD", 4339.0, 4339.2)  # warns
        clock["t"] += 10.0
        c._check_quote_staleness("XAUUSD", 4339.0, 4339.2)  # suppressed
    frozen_warns = [r for r in caplog.records if "FROZEN" in r.message]
    assert len(frozen_warns) == 1
