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


def test_live_missing_invariant_is_consistent():
    """A component is LIVE iff it is NOT in missing_components, and a missing one
    carries the neutral midpoint — never a faked directional value.

    All five feeds are implemented now, so which are live depends on creds/network;
    this checks the contract holds regardless of what is reachable.
    """
    from scripts.run_sentiment_engine import build_snapshot
    from src.sentiment.gss import _NEUTRAL
    snap = build_snapshot("XAUUSD")
    for name, c in snap["components"].items():
        assert c["live"] == (name not in snap["missing_components"])
        if not c["live"]:
            assert c["score"] == round(_NEUTRAL[name], 2)


def test_gss_history_appends_with_header():
    """append_gss_history writes a header once, then one row per call (no network)."""
    import csv as _csv
    from src.sentiment import store
    sym = "TESTSYM_HIST"
    path = store._SENTIMENT_DIR / f"gss_history_{sym}.csv"
    if path.exists():
        path.unlink()
    snap = {
        "generated_at": "2026-06-02T00:00:00+00:00", "price": 4500.0,
        "price_source": "mt5_live",
        "gss": {"total_score": 40.0, "regime": "Neutral / Chop",
                "breakdown": {"fundamental": 21.4, "technical": 7.5,
                              "institutional": 0.0, "retail": 7.5, "news": 3.3}},
        "missing_components": ["retail"], "recommendation": {"action": "FLAT / chop"},
    }
    try:
        store.append_gss_history(sym, snap)
        store.append_gss_history(sym, snap)
        rows = list(_csv.DictReader(open(path, encoding="utf-8")))
        assert len(rows) == 2                       # header written once
        assert rows[0]["gss_total"] == "40.0"
        assert rows[0]["missing"] == "retail"
        assert set(store._HISTORY_FIELDS) <= set(rows[0].keys())
    finally:
        if path.exists():
            path.unlink()


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
