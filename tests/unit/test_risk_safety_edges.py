"""Offline regression coverage for persistent halts and cooldown boundaries."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.risk import circuit_breaker
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.kill_switch import KillSwitch


def test_kill_switch_survives_restart_until_explicit_reset(tmp_path, monkeypatch):
    state_file = tmp_path / "kill_switch.json"
    monkeypatch.setattr(KillSwitch, "KILL_SWITCH_FILE", state_file)

    original = KillSwitch()
    assert original.is_active() is False
    original.trigger("synthetic test halt")
    triggered_at = original.get_status()["triggered_at"]

    restarted = KillSwitch()
    assert restarted.is_active() is True
    assert restarted.get_status()["reason"] == "synthetic test halt"
    assert restarted.get_status()["triggered_at"] == triggered_at
    assert state_file.exists()

    restarted.reset()
    assert restarted.is_active() is False
    assert not state_file.exists()
    assert KillSwitch().is_active() is False


@pytest.mark.parametrize("elapsed,allowed", [
    (timedelta(minutes=30, microseconds=-1), False),
    (timedelta(minutes=30), True),
    (timedelta(minutes=30, microseconds=1), True),
])
def test_circuit_breaker_cooldown_boundary(monkeypatch, elapsed, allowed):
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class Clock(datetime):
        current = started

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(circuit_breaker, "datetime", Clock)
    breaker = CircuitBreaker(max_consecutive_losses=3, cooldown_minutes=30)
    for _ in range(2):
        breaker.record_trade(Decimal("-1"))
        assert breaker.is_trading_allowed()[0] is True
    breaker.record_trade(Decimal("-1"))
    assert breaker.is_trading_allowed()[0] is False

    Clock.current = started + elapsed
    assert breaker.is_trading_allowed()[0] is allowed
    assert breaker.consecutive_losses == (0 if allowed else 3)
    assert breaker.get_status()["tripped"] is (not allowed)

    if allowed:
        breaker.record_trade(Decimal("-1"))
        assert breaker.consecutive_losses == 1
        assert breaker.is_trading_allowed()[0] is True
