"""Decay-floor allocator: weight computation + risk-engine veto."""

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts.strategy_allocator import compute_weights
from src.risk.risk_engine import RiskEngine


def _trades(rows):
    return pd.DataFrame(rows, columns=["strategy", "exit_time", "realized_pnl", "initial_risk"])


def _series(strategy, pnls, start="2026-06-01"):
    ts = pd.date_range(start, periods=len(pnls), freq="D", tz="UTC")
    return [{"strategy": strategy, "exit_time": t, "realized_pnl": p, "initial_risk": 100.0}
            for t, p in zip(ts, pnls)]


class TestComputeWeights:
    def test_negative_edge_defunded(self):
        rows = _series("kalman_regime", [-100] * 10)          # consistently losing
        w = compute_weights(_trades(rows), window_days=45, min_trades=8)
        assert w["kalman_regime"] == 0.0

    def test_positive_edge_kept(self):
        rows = _series("london_breakout", [120, -50, 130, -40, 140, -45, 125, -30, 110, -35])
        w = compute_weights(_trades(rows), window_days=45, min_trades=8)
        assert w["london_breakout"] == 1.0

    def test_sparse_never_starved(self):
        rows = _series("monday_drift", [-100, -100, -100])     # losing but < min_trades
        w = compute_weights(_trades(rows), window_days=45, min_trades=8)
        assert w["monday_drift"] == 1.0

    def test_outside_window_ignored(self):
        old = _series("kalman_regime", [-100] * 10, start="2025-01-01")
        recent = _series("kalman_regime", [120, -40] * 5, start="2026-06-01")
        w = compute_weights(_trades(old + recent), window_days=45, min_trades=8)
        assert w["kalman_regime"] == 1.0     # only recent (winning) window counts

    def test_manual_excluded(self):
        rows = _series("manual", [-100] * 10)
        w = compute_weights(_trades(rows), window_days=45, min_trades=8)
        assert "manual" not in w

    def test_empty(self):
        assert compute_weights(_trades([])) == {}


class TestRiskEngineVeto:
    def _engine(self, tmp_path, weights, enabled=True):
        wf = tmp_path / "weights.json"
        wf.write_text(json.dumps({"weights": weights}))
        cfg = {"risk": {"decay_floor": {"enabled": enabled, "weights_file": str(wf)}}}
        return RiskEngine(cfg)

    def _order(self, strategy):
        return SimpleNamespace(metadata={"strategy": strategy})

    def test_defunded_strategy_vetoed(self, tmp_path):
        eng = self._engine(tmp_path, {"kalman_regime": 0.0, "london_breakout": 1.0})
        ok, reason = eng._check_17_decay_floor(self._order("kalman_regime"))
        assert not ok and "defunded" in reason

    def test_funded_strategy_passes(self, tmp_path):
        eng = self._engine(tmp_path, {"kalman_regime": 0.0, "london_breakout": 1.0})
        ok, _ = eng._check_17_decay_floor(self._order("london_breakout"))
        assert ok

    def test_unknown_strategy_defaults_keep(self, tmp_path):
        eng = self._engine(tmp_path, {"kalman_regime": 0.0})
        ok, _ = eng._check_17_decay_floor(self._order("smc_ob"))
        assert ok                       # absent → weight 1.0

    def test_disabled_is_noop(self, tmp_path):
        eng = self._engine(tmp_path, {"kalman_regime": 0.0}, enabled=False)
        ok, _ = eng._check_17_decay_floor(self._order("kalman_regime"))
        assert ok                       # gate off → never vetoes

    def test_missing_file_fail_open(self, tmp_path):
        cfg = {"risk": {"decay_floor": {"enabled": True,
                                        "weights_file": str(tmp_path / "nope.json")}}}
        eng = RiskEngine(cfg)
        ok, _ = eng._check_17_decay_floor(self._order("kalman_regime"))
        assert ok                       # no file → no veto
