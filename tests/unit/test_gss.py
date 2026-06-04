"""Unit tests for the deterministic Gold Sentiment Score core."""
import pytest

from src.sentiment.gss import (
    MAX_INSTITUTIONAL,
    MAX_NEWS,
    MAX_TECHNICAL,
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


def test_falling_dollar_scores_bullish_without_a_level():
    """Fix #1: dollar DIRECTION must count even when the absolute DXY level is
    unknown (FRED only gives the broad-dollar trend, not the ICE level)."""
    falling = score_fundamental(fed_policy="neutral", dxy_falling=True, dxy_level=None)
    rising = score_fundamental(fed_policy="neutral", dxy_falling=False, dxy_level=None)
    assert falling is not None and rising is not None
    assert falling > rising
    # known-only-direction must still produce a score (old guard returned None)
    assert score_fundamental(dxy_falling=True) is not None


def test_live_neutral_equals_missing_neutral_midpoint():
    """Fix #2: a live-but-neutral reading sits at the component midpoint, never
    below it (calm news used to score 3.33 vs a missing feed's 5.0)."""
    assert score_news(news_sentiment_avg=0.0) == pytest.approx(MAX_NEWS / 2)
    assert score_institutional(cot_net_long_wow_pct=0.0,
                               etf_flow_3d="flat") == pytest.approx(MAX_INSTITUTIONAL / 2)
    assert score_technical(trend="chop", rsi_14=35, macd_bullish=None,
                           bb_state="inside") == pytest.approx(MAX_TECHNICAL / 2)


def test_elevated_real_yields_are_penalized_not_neutral():
    """Fix #3: 2%+ real yields are gold-negative, and direction matters there."""
    elevated = score_fundamental(fed_policy="neutral", real_yield_10y=2.2,
                                 real_yield_falling=False)
    normal = score_fundamental(fed_policy="neutral", real_yield_10y=1.6,
                               real_yield_falling=False)
    assert elevated < normal
    falling = score_fundamental(fed_policy="neutral", real_yield_10y=2.2,
                                real_yield_falling=True)
    assert falling > elevated  # falling real yields are less bearish for gold
