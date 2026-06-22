"""Correlation guard (risk check 18): correlated strategies share a slot.

squeeze_breakout × stoch_pullback correlate +0.34 (gold-coil breakouts) — the
guard caps concurrent open positions per correlation cluster so one bet can't
fill multiple position slots. See scripts/handcraft_weights.py.
"""

from types import SimpleNamespace

from src.risk.risk_engine import RiskEngine


def _engine(cfg=None):
    return RiskEngine({"risk": {"correlation_guard": cfg}} if cfg is not None else {})


def _order(strategy):
    return SimpleNamespace(metadata={"strategy": strategy})


def _positions(*strategies):
    return {f"p{i}": SimpleNamespace(metadata={"strategy": s})
            for i, s in enumerate(strategies)}


def test_default_cluster_blocks_correlated_peer():
    # squeeze already open → stoch (its +0.34 peer) is vetoed by default.
    eng = _engine()
    ok, reason = eng._check_18_correlation_cluster(
        _order("stoch_pullback"), _positions("squeeze_breakout"))
    assert not ok and "Correlation guard" in reason


def test_default_cluster_blocks_same_strategy_restack():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(
        _order("squeeze_breakout"), _positions("squeeze_breakout"))
    assert not ok                          # cluster shares one slot regardless


def test_uncorrelated_strategy_passes():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(
        _order("squeeze_breakout"), _positions("kalman_regime", "london_breakout"))
    assert ok                              # no cluster peer open


def test_no_open_positions_passes():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(_order("stoch_pullback"), {})
    assert ok


def test_strategy_not_in_any_cluster_passes():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(
        _order("kalman_regime"), _positions("kalman_regime"))
    assert ok                              # kalman isn't clustered


def test_order_without_strategy_passes():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(
        SimpleNamespace(metadata={}), _positions("squeeze_breakout"))
    assert ok                              # fail-open on missing tag


def test_manual_position_does_not_count():
    eng = _engine()
    ok, _ = eng._check_18_correlation_cluster(
        _order("squeeze_breakout"), _positions("manual", None))
    assert ok                              # non-cluster positions ignored


def test_disabled_is_noop():
    eng = _engine({"enabled": False})
    ok, _ = eng._check_18_correlation_cluster(
        _order("stoch_pullback"), _positions("squeeze_breakout"))
    assert ok


def test_max_per_cluster_two_allows_both():
    eng = _engine({"max_per_cluster": 2})
    ok, _ = eng._check_18_correlation_cluster(
        _order("stoch_pullback"), _positions("squeeze_breakout"))
    assert ok                              # one open < cap of 2
    ok2, _ = eng._check_18_correlation_cluster(
        _order("stoch_pullback"), _positions("squeeze_breakout", "stoch_pullback"))
    assert not ok2                         # two open == cap → vetoed


def test_custom_clusters_override_default():
    eng = _engine({"clusters": [["kalman_regime", "vwap"]]})
    # custom cluster active...
    ok, _ = eng._check_18_correlation_cluster(
        _order("vwap"), _positions("kalman_regime"))
    assert not ok
    # ...and the baked-in gold pair is no longer guarded.
    ok2, _ = eng._check_18_correlation_cluster(
        _order("stoch_pullback"), _positions("squeeze_breakout"))
    assert ok2
