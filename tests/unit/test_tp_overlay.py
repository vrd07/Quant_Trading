"""The bounded take-profit snap.

The overlay may decline to move a target; it may never move one on absent data, and
it may never produce a target on the wrong side of entry.
"""
import pandas as pd
import pytest

from src.microstructure.tp_overlay import SnapConfig, snap_take_profit

CFG = SnapConfig(band_pct=0.25, buffer_atr=0.05, min_rr=1.2, min_stops_distance=0.0)


def _pools(monkeypatch, prices):
    """Replace pool detection with a fixed price list."""
    monkeypatch.setattr("src.microstructure.tp_overlay._pool_prices",
                        lambda bars, params: list(prices))


def test_pool_inside_band_moves_the_target(monkeypatch):
    _pools(monkeypatch, [106.1])
    tp, reason = snap_take_profit(entry=100.0, side_is_buy=True, stop_loss=96.7,
                                  take_profit=106.6, bars=pd.DataFrame(), atr=2.0,
                                  cfg=CFG)
    assert reason == "snapped"
    assert tp == pytest.approx(106.0)  # 106.1 - 0.05 * 2.0


def test_pool_below_band_leaves_target_alone(monkeypatch):
    _pools(monkeypatch, [103.0])
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    assert reason == "no_pool"
    assert tp == 106.6


def test_pool_above_band_leaves_target_alone(monkeypatch):
    _pools(monkeypatch, [112.0])
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    assert reason == "no_pool"
    assert tp == 106.6


def test_earliest_pool_in_band_wins(monkeypatch):
    """Several pools qualify — the first wall price meets is the one that stops it."""
    _pools(monkeypatch, [107.9, 105.2, 106.4])
    tp, _ = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    assert tp == pytest.approx(105.1)  # 105.2 - 0.1


def test_sell_side_snaps_upward_from_the_pool(monkeypatch):
    _pools(monkeypatch, [93.9])
    tp, reason = snap_take_profit(entry=100.0, side_is_buy=False, stop_loss=103.3,
                                  take_profit=93.4, bars=pd.DataFrame(), atr=2.0,
                                  cfg=CFG)
    assert reason == "snapped"
    assert tp == pytest.approx(94.0)  # 93.9 + 0.1


def test_wrong_side_pools_are_ignored(monkeypatch):
    """For a BUY, pools below entry are never targets."""
    _pools(monkeypatch, [94.0, 93.0])
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    assert reason == "no_pool"
    assert tp == 106.6


def test_snap_breaching_min_rr_is_rejected(monkeypatch):
    _pools(monkeypatch, [105.0])
    cfg = SnapConfig(band_pct=0.5, buffer_atr=0.05, min_rr=2.0, min_stops_distance=0.0)
    tp, reason = snap_take_profit(100.0, True, 97.0, 106.0, pd.DataFrame(), 2.0, cfg)
    assert reason == "below_min_rr"
    assert tp == 106.0


def test_snap_inside_broker_minimum_is_rejected(monkeypatch):
    _pools(monkeypatch, [105.5])
    cfg = SnapConfig(band_pct=0.25, buffer_atr=0.05, min_rr=1.2,
                     min_stops_distance=6.0)
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, cfg)
    assert reason == "broker_min"
    assert tp == 106.6


def test_zero_atr_leaves_target_alone(monkeypatch):
    _pools(monkeypatch, [106.1])
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 0.0, CFG)
    assert reason == "no_bars"
    assert tp == 106.6


def test_detection_failure_leaves_target_alone(monkeypatch):
    def boom(bars, params):
        raise ValueError("malformed frame")
    monkeypatch.setattr("src.microstructure.tp_overlay._pool_prices", boom)
    tp, reason = snap_take_profit(100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    assert reason == "error"
    assert tp == 106.6


def test_deterministic(monkeypatch):
    _pools(monkeypatch, [106.1])
    args = (100.0, True, 96.7, 106.6, pd.DataFrame(), 2.0, CFG)
    first = snap_take_profit(*args)
    for _ in range(3):
        assert snap_take_profit(*args) == first
