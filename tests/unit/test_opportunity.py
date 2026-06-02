"""Unit tests for the opportunity gate (pure, no network)."""
from datetime import datetime, timedelta, timezone

from src.sentiment.opportunity import evaluate_opportunity


def _snap(gss, regime="Neutral / Chop", flags=None):
    return {"gss": {"total_score": gss, "regime": regime},
            "risk_flags": flags or {}}


def _prev(gss, regime="Neutral / Chop", flags=None, minutes_ago=120):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return {"generated_at": ts, "gss_total": gss, "regime": regime,
            "risk_flags": flags or {}}


def test_no_prior_decision_always_fires():
    trip, reasons = evaluate_opportunity(_snap(41), None)
    assert trip and reasons == ["initial decision"]


def test_cooldown_blocks_even_on_change():
    # Big move but only 5 min since last decision → blocked by cooldown.
    trip, reasons = evaluate_opportunity(
        _snap(70, "Strong Bullish"), _prev(41, minutes_ago=5), cooldown_min=20)
    assert not trip
    assert "cooldown" in reasons[0]


def test_chop_to_chop_does_not_fire():
    # Small drift inside the mid zone, cooldown elapsed → still nothing to decide.
    trip, reasons = evaluate_opportunity(_snap(43), _prev(41), cooldown_min=20)
    assert not trip and reasons == []


def test_zone_breakout_fires():
    trip, reasons = evaluate_opportunity(
        _snap(67, "Strong Bullish"), _prev(58, "Moderate Bullish"), cooldown_min=20)
    assert trip
    assert any("zone" in r for r in reasons)


def test_large_gss_move_fires():
    trip, reasons = evaluate_opportunity(_snap(52), _prev(40), cooldown_min=20)
    assert trip
    assert any("GSS moved" in r for r in reasons)


def test_risk_flag_flip_fires():
    trip, reasons = evaluate_opportunity(
        _snap(41, flags={"dxy_surging": True}),
        _prev(41, flags={"dxy_surging": False}), cooldown_min=20)
    assert trip
    assert any("dxy_surging ON" in r for r in reasons)


def test_refresh_only_in_actionable_zone():
    # Standing bull setup, long time passed, no change → refresh fires.
    trip, reasons = evaluate_opportunity(
        _snap(70, "Strong Bullish"), _prev(70, "Strong Bullish", minutes_ago=120),
        cooldown_min=20, refresh_min=90)
    assert trip and any("refresh" in r for r in reasons)
    # Same idle time but in chop → no refresh.
    trip2, _ = evaluate_opportunity(
        _snap(45), _prev(45, minutes_ago=120), cooldown_min=20, refresh_min=90)
    assert not trip2
