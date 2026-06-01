"""Unit tests for the VolatilityBreaker shock guard."""

import numpy as np
import pandas as pd
import pytest

from src.risk.volatility_breaker import VolatilityBreaker


def _bars(atr_like_range, n=600, base=4500.0):
    """Build OHLC bars whose per-bar range ≈ atr_like_range (a list or scalar)."""
    if np.isscalar(atr_like_range):
        rng = np.full(n, atr_like_range, dtype=float)
    else:
        rng = np.asarray(atr_like_range, dtype=float)
        n = len(rng)
    close = np.full(n, base)
    high = close + rng / 2
    low = close - rng / 2
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": 1.0}, index=idx)


def _cfg(**kw):
    base = {"enabled": True, "timeframe": "15m", "atr_period": 14,
            "baseline_window": 50, "trigger_mult": 2.5, "release_mult": 1.5}
    base.update(kw)
    return {"risk": {"volatility_breaker": base}}


def test_disabled_never_activates():
    vb = VolatilityBreaker(_cfg(enabled=False))
    assert vb.update(_bars(50.0)) is False
    assert vb.active is False


def test_fail_open_on_thin_data():
    """Not enough bars ⇒ stays inactive, does not block trading."""
    vb = VolatilityBreaker(_cfg())
    assert vb.update(_bars(10.0, n=20)) is False


def test_calm_market_stays_inactive():
    vb = VolatilityBreaker(_cfg())
    assert vb.update(_bars(10.0, n=400)) is False
    assert vb.active is False


def test_spike_activates_then_releases():
    # A SHORT spike vs a long baseline: a 18-bar range-100 burst against a
    # 60-bar baseline of calm range-10 bars. The baseline median stays ~10
    # (most of the window is calm) while current ATR jumps → ratio >> trigger.
    vb = VolatilityBreaker(_cfg(baseline_window=60, trigger_mult=2.5, release_mult=1.5))
    rng = [10.0] * 400 + [100.0] * 18 + [10.0] * 200
    bars = _bars(rng)

    # Right after the spike → active, one-shot edge flag set.
    assert vb.update(bars.iloc[:418]) is True
    assert vb.just_activated is True

    # A long calm tail lets ATR and baseline both return to ~10 → released.
    assert vb.update(bars) is False
    assert vb.just_activated is False


def test_stays_active_while_still_elevated():
    """Once active, an update with vol still above the release line holds state."""
    vb = VolatilityBreaker(_cfg(baseline_window=60, trigger_mult=2.5, release_mult=1.5))
    rng = [10.0] * 400 + [100.0] * 18
    bars = _bars(rng)
    assert vb.update(bars) is True          # activate on the spike
    # Append a few more elevated bars — still well above release → stays active.
    rng2 = rng + [80.0] * 4
    assert vb.update(_bars(rng2)) is True
    assert vb.active is True
