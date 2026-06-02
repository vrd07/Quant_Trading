"""Unit tests for the deterministic Gold Sentiment Score core."""
import pytest

from src.sentiment.gss import (
    MAX_TOTAL,
    GSSComponents,
    compute_gss,
    regime_for_score,
    score_fundamental,
    score_institutional,
    score_news,
    score_retail,
    score_technical,
)


def test_all_missing_is_exactly_neutral():
    """No data → midpoint 50, regime 'Moderate Bullish' boundary, all 5 missing."""
    r = compute_gss(GSSComponents())
    assert r.total == 50.0
    assert len(r.missing) == 5
    assert r.regime == "Moderate Bullish"


def test_full_bull_caps_at_100():
    r = compute_gss(GSSComponents(
        fundamental=30, technical=25, institutional=20, retail=15, news=10))
    assert r.total == MAX_TOTAL == 100
    assert r.regime == "Extreme Bullish"
    assert r.missing == []


def test_full_bear_floors_at_zero():
    r = compute_gss(GSSComponents(
        fundamental=0, technical=0, institutional=0, retail=0, news=0))
    assert r.total == 0.0
    assert r.regime == "Extreme Bearish"


def test_determinism_same_input_same_output():
    c = GSSComponents(fundamental=25, technical=20, institutional=15, retail=8, news=4)
    assert compute_gss(c).total == compute_gss(c).total == 72.0


@pytest.mark.parametrize("score,expected", [
    (95, "Extreme Bullish"), (70, "Strong Bullish"), (55, "Moderate Bullish"),
    (40, "Neutral / Chop"), (25, "Moderate Bearish"), (10, "Strong Bearish"),
    (2, "Extreme Bearish"),
])
def test_regime_scale(score, expected):
    assert regime_for_score(score) == expected


def test_retail_is_contrarian():
    """Extreme retail longs = bearish (low pts); extreme retail shorts = bullish."""
    crowded_long = score_retail(85)
    crowded_short = score_retail(15)
    assert crowded_long is not None and crowded_short is not None
    assert crowded_short > crowded_long
    assert score_retail(None) is None  # missing → neutral handled upstream


def test_missing_inputs_return_none_not_a_direction():
    assert score_fundamental() is None
    assert score_technical() is None
    assert score_institutional() is None
    assert score_news() is None


def test_component_scores_stay_within_ceilings():
    assert 0 <= score_fundamental(fed_policy="dovish", real_yield_10y=1.2,
                                  real_yield_falling=True, dxy_level=97,
                                  dxy_falling=True, cpi_yoy=3.5) <= 30
    assert 0 <= score_technical(trend="bull_aligned", rsi_14=58,
                                macd_bullish=True, bb_state="upper_walk") <= 25
    assert 0 <= score_institutional(cot_net_long_wow_pct=12, etf_flow_3d="inflow") <= 20
    assert 0 <= score_news(news_sentiment_avg=0.5, geo_shock_48h=True) <= 10
