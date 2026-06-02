"""Contract tests for the sentiment engine snapshot + technical feed.

These are smoke/contract tests: they assert the shapes the monitor and nightly
review depend on, and that a missing feed degrades to neutral rather than faking
a direction. The technical feed reads the canonical 5m CSV when present.
"""
from pathlib import Path

import pytest

from src.sentiment.gss import MAX_TOTAL


def test_build_snapshot_contract():
    from scripts.run_sentiment_engine import build_snapshot
    snap = build_snapshot("XAUUSD")
    # Keys the pop-up renders.
    for k in ("gss", "components", "market_structure", "macro_context",
              "risk_flags", "feeds", "price_levels", "recommendation",
              "missing_components", "asset"):
        assert k in snap, f"missing snapshot key: {k}"
    assert 0 <= snap["gss"]["total_score"] <= MAX_TOTAL
    assert set(snap["components"]) == {
        "fundamental", "technical", "institutional", "retail", "news"}
    # Every component reports score/max/live.
    for c in snap["components"].values():
        assert {"score", "max", "live"} <= set(c)
    assert len(snap["price_levels"]) == 6


def test_missing_feeds_are_flagged_not_faked():
    """Unbuilt feeds must be MISSING (neutral), not faked LIVE.

    retail (Myfxbook) and news (Alpha Vantage) are still stubs that always
    return None, so they must always show as missing regardless of network.
    """
    from scripts.run_sentiment_engine import build_snapshot
    snap = build_snapshot("XAUUSD")
    assert not snap["components"]["retail"]["live"]
    assert not snap["components"]["news"]["live"]
    assert "retail" in snap["missing_components"]
    assert "news" in snap["missing_components"]


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "data" / "historical"
         / "XAUUSD_5m_real.csv").exists(),
    reason="canonical 5m CSV not present",
)
def test_technical_feed_is_real_and_bounded():
    from src.sentiment.technical import compute_technical
    tech = compute_technical("XAUUSD")
    if tech["points"] is not None:           # enough history
        assert 0 <= tech["points"] <= 25
        s = tech["structure"]
        assert {"trend", "rsi_14", "macd_signal", "bb_state", "atr_14"} <= set(s)
        assert 0 <= s["rsi_14"] <= 100
