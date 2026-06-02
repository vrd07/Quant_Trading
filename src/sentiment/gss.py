"""
Gold Sentiment Score — pure deterministic scoring (market_sentiment.md §4).

Every function here is pure: no I/O, no clock, no globals. Given the same
component inputs you always get the same score. That determinism is the whole
point — it is what lets the score be backtested and, only if it earns it,
consume-able by the (deterministic) risk engine. An LLM never sets these numbers.

Component weights (max points), summing to 100:
    Fundamental 30 · Technical 25 · Institutional 20 · Retail 15 · News 10
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# Component ceilings (market_sentiment.md §4.1). The sum is the 0-100 scale.
MAX_FUNDAMENTAL = 30
MAX_TECHNICAL = 25
MAX_INSTITUTIONAL = 20
MAX_RETAIL = 15
MAX_NEWS = 10
MAX_TOTAL = MAX_FUNDAMENTAL + MAX_TECHNICAL + MAX_INSTITUTIONAL + MAX_RETAIL + MAX_NEWS

# A missing component falls back to the NEUTRAL midpoint of its range, never to a
# directional value. "No data" must not push the score bullish or bearish.
_NEUTRAL = {
    "fundamental": MAX_FUNDAMENTAL / 2.0,
    "technical": MAX_TECHNICAL / 2.0,
    "institutional": MAX_INSTITUTIONAL / 2.0,
    "retail": MAX_RETAIL / 2.0,
    "news": MAX_NEWS / 2.0,
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ── per-component sub-scorers (market_sentiment.md §4.2) ─────────────────────
# Each returns points in [0, component_max]. Inputs are pre-classified signals,
# kept simple and explicit so the mapping is auditable against the spec.

def score_fundamental(
    fed_policy: Optional[str] = None,        # "dovish" | "neutral" | "hawkish"
    real_yield_falling: Optional[bool] = None,
    real_yield_10y: Optional[float] = None,
    dxy_falling: Optional[bool] = None,
    dxy_level: Optional[float] = None,
    cpi_yoy: Optional[float] = None,
) -> Optional[float]:
    """30-pt Fundamental Bias. Returns None if nothing is known (→ neutral)."""
    if all(x is None for x in (fed_policy, real_yield_10y, dxy_level, cpi_yoy)):
        return None
    # FedScore (-10..+10) → shifted to 0..max contribution handled by sum/clamp.
    fed = {"dovish": 10, "neutral": 5, "hawkish": -10}.get((fed_policy or "").lower(), 0)
    if real_yield_10y is None:
        yld = 0
    elif real_yield_10y < 1.5:
        yld = 10 if real_yield_falling else 5
    elif real_yield_10y <= 2.0:
        yld = 5
    elif real_yield_10y > 2.5:
        yld = -10
    else:
        yld = 0
    if dxy_level is None:
        dxy = 0
    elif dxy_level < 100:
        dxy = 10 if dxy_falling else 5
    elif dxy_level <= 103:
        dxy = 5
    elif dxy_level > 105:
        dxy = -10
    else:
        dxy = 0
    if cpi_yoy is None:
        infl = 0
    elif cpi_yoy > 3:
        infl = 5
    elif cpi_yoy >= 2:
        infl = 0
    else:
        infl = -5
    raw = fed + yld + dxy + infl  # spec range roughly -35..+35
    # Map raw [-35, +35] onto [0, 30] linearly (neutral 0 → 15).
    pts = (raw + 35) / 70.0 * MAX_FUNDAMENTAL
    return _clamp(pts, 0, MAX_FUNDAMENTAL)


def score_technical(
    trend: Optional[str] = None,      # "bull_aligned"|"recovering"|"chop"|"bear_aligned"
    rsi_14: Optional[float] = None,
    macd_bullish: Optional[bool] = None,
    bb_state: Optional[str] = None,   # "upper_walk"|"inside"|"lower_breach"
) -> Optional[float]:
    """25-pt Technical Bias. Returns None if nothing is known (→ neutral)."""
    if all(x is None for x in (trend, rsi_14, macd_bullish, bb_state)):
        return None
    tr = {"bull_aligned": 10, "recovering": 5, "chop": 0, "bear_aligned": -10}.get(
        (trend or "").lower(), 0)
    if rsi_14 is None:
        mom = 0
    elif 50 <= rsi_14 <= 65:
        mom = 10
    elif 40 <= rsi_14 < 50 or 65 < rsi_14 <= 70:
        mom = 5
    elif 30 <= rsi_14 < 40:
        mom = 0
    else:
        mom = -5
    macd = 0 if macd_bullish is None else (5 if macd_bullish else -5)
    bb = {"upper_walk": 5, "inside": 0, "lower_breach": -5}.get((bb_state or "").lower(), 0)
    raw = tr + mom + macd + bb  # roughly -20..+30
    pts = (raw + 20) / 50.0 * MAX_TECHNICAL
    return _clamp(pts, 0, MAX_TECHNICAL)


def score_institutional(
    cot_net_long_wow_pct: Optional[float] = None,
    etf_flow_3d: Optional[str] = None,   # "inflow"|"flat"|"outflow"
) -> Optional[float]:
    """20-pt Institutional Sentiment. Returns None if nothing is known."""
    if cot_net_long_wow_pct is None and etf_flow_3d is None:
        return None
    if cot_net_long_wow_pct is None:
        cot = 10  # neutral midpoint of 0..20
    elif cot_net_long_wow_pct > 10:
        cot = 20
    elif cot_net_long_wow_pct >= 5:
        cot = 15
    elif cot_net_long_wow_pct > 0:
        cot = 10
    elif cot_net_long_wow_pct == 0:
        cot = 5
    else:
        cot = 0
    etf = {"inflow": 5, "flat": 0, "outflow": -5}.get((etf_flow_3d or "").lower(), 0)
    return _clamp(cot + etf, 0, MAX_INSTITUTIONAL)


def score_retail(retail_long_pct: Optional[float] = None) -> Optional[float]:
    """15-pt Retail Sentiment — CONTRARIAN (market_sentiment.md §4.2 D)."""
    if retail_long_pct is None:
        return None
    p = retail_long_pct
    if p > 80:
        raw = -15
    elif p > 65:
        raw = -5
    elif p >= 35:
        raw = 5
    elif p >= 20:
        raw = 10
    else:
        raw = 15
    # Map [-15, +15] → [0, 15].
    return _clamp((raw + 15) / 30.0 * MAX_RETAIL, 0, MAX_RETAIL)


def score_news(
    news_sentiment_avg: Optional[float] = None,   # -1..+1
    geo_shock_48h: Optional[bool] = None,
) -> Optional[float]:
    """10-pt News & Event Risk. Returns None if nothing is known."""
    if news_sentiment_avg is None and geo_shock_48h is None:
        return None
    if news_sentiment_avg is None:
        ns = 0
    elif news_sentiment_avg > 0.2:
        ns = 5
    elif news_sentiment_avg < -0.2:
        ns = -5
    else:
        ns = 0
    geo = 5 if geo_shock_48h else 0
    raw = ns + geo  # -5..+10
    return _clamp((raw + 5) / 15.0 * MAX_NEWS, 0, MAX_NEWS)


# ── composite ────────────────────────────────────────────────────────────────
@dataclass
class GSSComponents:
    """Already-scored component points (each in [0, component_max]).

    ``None`` means "no data" → the neutral midpoint is substituted so a missing
    feed never pushes the total bullish or bearish.
    """
    fundamental: Optional[float] = None
    technical: Optional[float] = None
    institutional: Optional[float] = None
    retail: Optional[float] = None
    news: Optional[float] = None


@dataclass
class GSSResult:
    total: float
    regime: str
    breakdown: Dict[str, float]
    missing: list = field(default_factory=list)


# market_sentiment.md §4.3 interpretation scale.
def regime_for_score(total: float) -> str:
    if total >= 80:
        return "Extreme Bullish"
    if total >= 65:
        return "Strong Bullish"
    if total >= 50:
        return "Moderate Bullish"
    if total >= 35:
        return "Neutral / Chop"
    if total >= 20:
        return "Moderate Bearish"
    if total >= 5:
        return "Strong Bearish"
    return "Extreme Bearish"


def compute_gss(components: GSSComponents) -> GSSResult:
    """Sum component points into a 0-100 GSS and map to a regime label.

    Pure and total: any combination of present/missing components returns a
    valid result. Missing components contribute their neutral midpoint and are
    listed in ``missing`` so callers can downweight conviction.
    """
    breakdown: Dict[str, float] = {}
    missing: list = []
    for name in ("fundamental", "technical", "institutional", "retail", "news"):
        val = getattr(components, name)
        if val is None:
            missing.append(name)
            breakdown[name] = round(_NEUTRAL[name], 2)
        else:
            breakdown[name] = round(float(val), 2)
    total = round(sum(breakdown.values()), 2)
    total = _clamp(total, 0, MAX_TOTAL)
    return GSSResult(
        total=total,
        regime=regime_for_score(total),
        breakdown=breakdown,
        missing=missing,
    )
