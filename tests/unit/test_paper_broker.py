"""Unit tests for the paper-trading bridge (no network, isolated state file)."""
import importlib

import pytest


@pytest.fixture()
def broker(tmp_path, monkeypatch):
    import src.sentiment.paper_broker as pb
    importlib.reload(pb)
    monkeypatch.setattr(pb, "_DIR", tmp_path)
    monkeypatch.setattr(pb, "_STATE", tmp_path / "paper_state.json")
    monkeypatch.setattr(pb, "_TRADES", tmp_path / "paper_trades.csv")
    return pb


def _snap(price, gss=68):
    return {"price": price, "gss": {"total_score": gss}}


def _long(entry_hint, sl, tp, size=1.0):
    return {"decision": "LONG", "stop_loss": sl, "take_profit_1": tp,
            "position_size_pct": size}


def test_opens_long_then_hits_tp_for_positive_r(broker):
    # entry ~4500, SL 4480 (risk 20), TP 4540 (reward 40 → +2R)
    broker.update(_snap(4500), _long(4500, 4480, 4540))
    st = broker.update(_snap(4541), None)        # price hits TP
    assert st["position"] is None
    assert st["trades"] == 1 and st["wins"] == 1
    assert st["realized_r"] == pytest.approx(2.0, abs=0.05)


def test_open_then_stop_loss_is_minus_one_r(broker):
    broker.update(_snap(4500), _long(4500, 4480, 4540))
    st = broker.update(_snap(4479), None)        # price hits SL
    assert st["position"] is None
    assert st["losses"] == 1
    assert st["realized_r"] == pytest.approx(-1.0, abs=0.05)


def test_only_one_position_at_a_time(broker):
    broker.update(_snap(4500), _long(4500, 4480, 4540))
    # a second decision while in a position must NOT open another
    st = broker.update(_snap(4501), _long(4501, 4485, 4560))
    assert st["position"]["entry"] == 4500.0     # still the original


def test_requires_valid_sl_tp_geometry(broker):
    # SHORT but TP above entry (wrong side) → must not open
    bad = {"decision": "SHORT", "stop_loss": 4520, "take_profit_1": 4560,
           "position_size_pct": 1.0}
    st = broker.update(_snap(4500), bad)
    assert st["position"] is None


def test_flat_decision_opens_nothing(broker):
    st = broker.update(_snap(4500), {"decision": "FLAT", "position_size_pct": 0.0})
    assert st["position"] is None
