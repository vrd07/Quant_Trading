"""
External data feeds for the sentiment engine — I/O layer (SKELETON).

Each feed is the dirty part: a network call to a third-party API that will, at
some point, be slow, rate-limited, or down. Two non-negotiable rules:

  1. FAIL SAFE. On any error/timeout/missing key, return None. The scorer turns
     None into a neutral sub-score; the trade loop is never blocked or forced.
  2. SLOW CLOCK. These are polled every 15-60 min, never per tick. Free tiers
     (Alpha Vantage 25/day, UniRate 200/day) cannot survive faster polling.

Per the Forge review, start with only the 2-3 highest-signal, most reliable
feeds and prove the GSS edge in backtest BEFORE adding the rest. The functions
below define the contract and are intentionally unimplemented — implement them
one at a time, each behind its own backtest, rather than wiring all eight APIs.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class FundamentalInputs:
    fed_policy: Optional[str] = None          # "dovish"|"neutral"|"hawkish"
    real_yield_10y: Optional[float] = None    # FRED DFII10
    real_yield_falling: Optional[bool] = None
    dxy_level: Optional[float] = None
    dxy_falling: Optional[bool] = None
    cpi_yoy: Optional[float] = None


def _env(key: str) -> Optional[str]:
    val = os.environ.get(key)
    return val if val else None


_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _fred_series(series_id: str, key: str, limit: int = 14) -> List[float]:
    """Most-recent `limit` numeric observations (newest first). [] on any error."""
    try:
        q = urllib.parse.urlencode({
            "series_id": series_id, "api_key": key, "file_type": "json",
            "sort_order": "desc", "limit": limit,
        })
        with urllib.request.urlopen(f"{_FRED_URL}?{q}", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vals: List[float] = []
        for obs in data.get("observations", []):
            v = obs.get("value")
            if v not in (None, ".", ""):
                try:
                    vals.append(float(v))
                except ValueError:
                    continue
        return vals
    except Exception:
        return []


def _falling(series_newest_first: List[float], lookback: int = 3) -> Optional[bool]:
    """True if the series is lower now than `lookback` prints ago."""
    if len(series_newest_first) <= lookback:
        return None
    return series_newest_first[0] < series_newest_first[lookback]


def fetch_fundamental() -> FundamentalInputs:
    """FRED real yields (DFII10) + broad-dollar trend + Fed funds + CPI YoY.

    Highest-signal feed. Requires FRED_API_KEY (free). Every field independently
    fails safe to None, so a partial outage degrades to neutral rather than a
    fabricated bias. DXY proper needs Alpha Vantage; we use FRED's broad-dollar
    index (DTWEXBGS) as a stand-in and normalize its level to a ~DXY scale only
    for the falling/level classification, not as a true DXY print.
    """
    key = _env("FRED_API_KEY")
    if not key:
        return FundamentalInputs()
    out = FundamentalInputs()

    dfii10 = _fred_series("DFII10", key)        # 10Y TIPS real yield, %
    if dfii10:
        out.real_yield_10y = dfii10[0]
        out.real_yield_falling = _falling(dfii10)

    fedfunds = _fred_series("FEDFUNDS", key, limit=4)  # monthly effective rate
    if len(fedfunds) >= 2:
        if fedfunds[0] < fedfunds[1]:
            out.fed_policy = "dovish"
        elif fedfunds[0] > fedfunds[1]:
            out.fed_policy = "hawkish"
        else:
            out.fed_policy = "neutral"

    # Broad-dollar index gives us a reliable DIRECTION; its level is on a
    # different scale than DXY, so we do NOT use it as the level. If the user
    # supplies a real DXY print via DXY_LEVEL (config/sentiment.env), use that
    # for the level-based DXYScore — otherwise level stays None (direction-only).
    dollar = _fred_series("DTWEXBGS", key)
    if dollar:
        out.dxy_falling = _falling(dollar)
    dxy_override = _env("DXY_LEVEL")
    if dxy_override:
        try:
            out.dxy_level = float(dxy_override)
        except ValueError:
            pass

    cpi = _fred_series("CPIAUCSL", key, limit=14)  # monthly index level
    if len(cpi) >= 13:
        out.cpi_yoy = round((cpi[0] / cpi[12] - 1.0) * 100, 2)

    return out


# CFTC Commitments of Traders — Legacy Futures-Only, via the official Socrata
# open-data API (free, no key). Gold = COMEX contract market code 088691. We use
# the official source rather than a third-party wrapper: fewer black boxes, and
# it cannot silently disappear behind someone else's rate limit.
_CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_GOLD_CONTRACT_CODE = "088691"


def fetch_cot_net_long_wow_pct() -> Optional[float]:
    """Week-over-week % change in non-commercial NET-long gold positioning.

    net = noncomm_long - noncomm_short. Returns (net_now - net_prev)/|net_prev|
    * 100, or None on any error / insufficient history (→ neutral institutional
    sub-score). Weekly data (Fri release); polling faster is pointless.
    """
    try:
        q = urllib.parse.urlencode({
            "cftc_contract_market_code": _GOLD_CONTRACT_CODE,
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": "2",
        })
        with urllib.request.urlopen(f"{_CFTC_URL}?{q}", timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if len(data) < 2:
            return None
        now = float(data[0]["noncomm_positions_long_all"]) - \
            float(data[0]["noncomm_positions_short_all"])
        prev = float(data[1]["noncomm_positions_long_all"]) - \
            float(data[1]["noncomm_positions_short_all"])
        if prev == 0:
            return None
        return round((now - prev) / abs(prev) * 100, 2)
    except Exception:
        return None


def fetch_retail_long_pct() -> Optional[float]:
    """Retail long % (contrarian). Myfxbook / OANDA. Build third.

    TODO: scrape/API the retail long/short ratio for XAUUSD. Returns None until
    implemented.
    """
    return None


def fetch_news_sentiment() -> Optional[float]:
    """Average NLP sentiment of recent gold headlines, in [-1, +1].

    TODO: Alpha Vantage NEWS_SENTIMENT (function=NEWS_SENTIMENT, tickers/topics).
    Lowest priority — noisiest signal. Returns None until implemented.
    """
    return None
