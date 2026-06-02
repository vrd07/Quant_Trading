"""Unit tests for the signal notifier formatting/filtering (no network send)."""
from src.sentiment.notify import format_signal, is_noteworthy


def _rec(decision="LONG", **kw):
    base = {
        "decision": decision, "confidence": "HIGH", "gss_total": 68,
        "regime": "Strong Bullish", "price": 4512.0,
        "entry_zone": {"min": 4505, "max": 4515}, "stop_loss": 4480,
        "take_profit_1": 4560, "take_profit_2": 4610, "position_size_pct": 1.0,
        "rationale": "dovish Fed + falling yields & dollar; price reclaimed 50EMA",
        "override_reason": None,
    }
    base.update(kw)
    return base


def test_noteworthy_filters_chop_flat():
    assert is_noteworthy(_rec("LONG"))
    assert is_noteworthy(_rec("SHORT"))
    assert is_noteworthy(_rec("REDUCE"))
    assert not is_noteworthy(_rec("FLAT"))
    # FLAT with an override IS worth a ping (e.g. dollar override).
    assert is_noteworthy(_rec("FLAT", override_reason="DXY + yields rising"))


def test_format_is_html_safe_and_has_key_fields():
    msg = format_signal(_rec("LONG"), reasons=["zone mid->bull", "GSS moved 58->68"])
    assert "XAUUSD SIGNAL — LONG" in msg
    assert "SL 4,480" in msg and "TP1 4,560" in msg
    assert "trigger:" in msg
    assert "not auto-executed" in msg.lower()


def test_format_escapes_html_in_rationale():
    msg = format_signal(_rec("SHORT", rationale="risk < 0 & dxy > 105"))
    assert "&lt;" in msg and "&amp;" in msg and "&gt;" in msg  # escaped, no raw <>&
