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

import http.cookiejar
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional


@dataclass
class FundamentalInputs:
    fed_policy: Optional[str] = None          # "dovish"|"neutral"|"hawkish"
    real_yield_10y: Optional[float] = None    # FRED DFII10
    real_yield_falling: Optional[bool] = None
    dxy_level: Optional[float] = None
    dxy_falling: Optional[bool] = None
    cpi_yoy: Optional[float] = None
    fiscal_stress: Optional[bool] = None      # debt-ceiling / shutdown active


def _env(key: str) -> Optional[str]:
    val = os.environ.get(key)
    return val if val else None


def _env_bool(key: str) -> Optional[bool]:
    v = os.environ.get(key)
    if v is None or v == "":
        return None
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── disk cache for slow feeds ────────────────────────────────────────────────
# Alpha Vantage free tier is 25 calls/day; the engine's 15-min loop would make
# ~96/day per AV feed. Macro/positioning data also changes daily/weekly, not
# every 15 min. So cache each feed's last GOOD value with a TTL and only hit the
# network when stale. Only non-None results are cached — a failed fetch never
# poisons the cache, and the caller still falls back to neutral.
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sentiment" / ".feed_cache"


def _cached(key: str, ttl_seconds: float, producer: Callable[[], Any]) -> Any:
    try:
        path = _CACHE_DIR / f"{key}.json"
        if path.exists():
            blob = json.loads(path.read_text())
            if (time.time() - blob.get("ts", 0)) < ttl_seconds:
                return blob.get("value")
    except Exception:
        pass
    value = producer()
    if value is not None:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            (_CACHE_DIR / f"{key}.json").write_text(
                json.dumps({"ts": time.time(), "value": value}))
        except Exception:
            pass
    return value


# Some endpoints (Myfxbook) 403 the default urllib User-Agent.
_UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")}


def _get_json(url: str, timeout: int = 20) -> Optional[dict]:
    """GET JSON with a browser UA. Returns None on any error (fail-safe)."""
    try:
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


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


def _fetch_fred_dict() -> Optional[dict]:
    """Network half of the fundamental feed → plain dict (cacheable). None if no key."""
    key = _env("FRED_API_KEY")
    if not key:
        return None
    out: dict = {}
    dfii10 = _fred_series("DFII10", key)               # 10Y TIPS real yield, %
    if dfii10:
        out["real_yield_10y"] = dfii10[0]
        out["real_yield_falling"] = _falling(dfii10)
    fedfunds = _fred_series("FEDFUNDS", key, limit=4)  # monthly effective rate
    if len(fedfunds) >= 2:
        out["fed_policy"] = ("dovish" if fedfunds[0] < fedfunds[1]
                             else "hawkish" if fedfunds[0] > fedfunds[1] else "neutral")
    dollar = _fred_series("DTWEXBGS", key)             # broad-dollar (direction only)
    if dollar:
        out["dxy_falling"] = _falling(dollar)
    cpi = _fred_series("CPIAUCSL", key, limit=14)      # monthly index level
    if len(cpi) >= 13:
        out["cpi_yoy"] = round((cpi[0] / cpi[12] - 1.0) * 100, 2)
    # Only treat the fetch as good (cacheable) when the core anchors arrived;
    # a transient partial must not be cached for 6h. Returning None → this cycle
    # is neutral and the next cycle retries.
    if "real_yield_10y" not in out or "cpi_yoy" not in out:
        return None
    return out


def fetch_fundamental() -> FundamentalInputs:
    """FRED real yields (DFII10) + broad-dollar trend + Fed funds + CPI YoY,
    plus a manual fiscal-stress flag.

    Requires FRED_API_KEY (free). FRED data is cached 6h (it updates daily at
    most). True DXY is ICE-proprietary; we use FRED's broad-dollar index for
    DIRECTION only, unless DXY_LEVEL is supplied. FiscalScore (§4.2 A) has no
    clean free API, so it is driven by the FISCAL_STRESS env flag — set it true
    during a known debt-ceiling / shutdown episode.
    """
    out = FundamentalInputs()
    data = _cached("fred_fundamental", 6 * 3600, _fetch_fred_dict) or {}
    out.real_yield_10y = data.get("real_yield_10y")
    out.real_yield_falling = data.get("real_yield_falling")
    out.fed_policy = data.get("fed_policy")
    out.dxy_falling = data.get("dxy_falling")
    out.cpi_yoy = data.get("cpi_yoy")

    dxy_override = _env("DXY_LEVEL")
    if dxy_override:
        try:
            out.dxy_level = float(dxy_override)
        except ValueError:
            pass

    out.fiscal_stress = _env_bool("FISCAL_STRESS")
    return out


# CFTC Commitments of Traders — Legacy Futures-Only, via the official Socrata
# open-data API (free, no key). Gold = COMEX contract market code 088691. We use
# the official source rather than a third-party wrapper: fewer black boxes, and
# it cannot silently disappear behind someone else's rate limit.
_CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_GOLD_CONTRACT_CODE = "088691"


def fetch_cot_net_long_wow_pct() -> Optional[float]:
    """WoW % change in non-commercial NET-long gold positioning (cached 12h)."""
    return _cached("cot_gold", 12 * 3600, _fetch_cot_raw)


def _fetch_cot_raw() -> Optional[float]:
    """net = noncomm_long - noncomm_short; (net_now - net_prev)/|net_prev| * 100.
    None on error / insufficient history. Weekly data (Fri release)."""
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


_MYFXBOOK = "https://www.myfxbook.com/api"


def _retail_once(email: str, password: str) -> Optional[float]:
    """One login→outlook→logout attempt on a SHARED cookie jar.

    Myfxbook sets a cookie at login that its API checks alongside the session
    token; without it the very next call returns "Invalid session". So all three
    calls must go through one opener that carries cookies forward.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(url: str) -> Optional[dict]:
        try:
            req = urllib.request.Request(url, headers=_UA)
            with opener.open(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    session = None
    try:
        login = call(f"{_MYFXBOOK}/login.json?email={urllib.parse.quote(email)}"
                     f"&password={urllib.parse.quote(password)}")
        if not login or login.get("error") or not login.get("session"):
            return None
        # Myfxbook returns the session token ALREADY url-encoded (%2F, %3D ...).
        # Re-quoting it double-encodes the token and the next call fails with
        # "Invalid session" — pass it through raw.
        session = login["session"]
        out = call(f"{_MYFXBOOK}/get-community-outlook.json?session={session}")
        if not out or out.get("error"):
            return None
        for sym in out.get("symbols", []) or []:
            if sym.get("name") != "XAUUSD":
                continue
            # longPercentage is Myfxbook's headline number (matches the site).
            lp = sym.get("longPercentage")
            if lp is not None:
                return round(float(lp), 1)
            long_vol = float(sym.get("longVolume", 0) or 0)
            short_vol = float(sym.get("shortVolume", 0) or 0)
            if long_vol + short_vol > 0:
                return round(long_vol / (long_vol + short_vol) * 100, 1)
        return None
    finally:
        if session:
            try:
                call(f"{_MYFXBOOK}/logout.json?session={session}")
            except Exception:
                pass


def fetch_retail_long_pct(retries: int = 2) -> Optional[float]:
    """Retail % LONG on XAUUSD from Myfxbook Community Outlook (CONTRARIAN input).

    Needs MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD. Reads the community-outlook
    endpoint and takes XAUUSD's longPercentage (the site's headline number),
    falling back to the long/short volume ratio. Retries a couple of times
    because the session check is flaky, then returns None → neutral retail
    sub-score (never blocks the engine).
    """
    email = _env("MYFXBOOK_EMAIL")
    password = _env("MYFXBOOK_PASSWORD")
    if not email or not password:
        return None
    # Cache good values 20 min so a transient Myfxbook throttle doesn't blank the
    # component every cycle; the producer retries a couple of times itself.
    def _produce() -> Optional[float]:
        for _ in range(max(1, retries)):
            result = _retail_once(email, password)
            if result is not None:
                return result
            time.sleep(1.5)
        return None
    return _cached("myfxbook_retail", 20 * 60, _produce)


_AV_DAILY = "https://www.alphavantage.co/query"


def fetch_etf_flow_3d() -> Optional[str]:
    """GLD 3-day flow proxy → 'inflow' | 'flat' | 'outflow' (cached 12h).

    True GLD fund flows (tonnes in trust) have no clean free API, so we proxy
    with the 3-day GLD price trend via Alpha Vantage TIME_SERIES_DAILY: ETF
    money chases price. A proxy, not actual creations/redemptions — labeled as
    such. None on rate-limit/error → neutral ETF sub-score.
    """
    return _cached("av_gld_flow", 12 * 3600, _fetch_etf_flow_raw)


def _fetch_etf_flow_raw() -> Optional[str]:
    key = _env("ALPHAVANTAGE_API_KEY")
    if not key:
        return None
    data = _get_json(
        f"{_AV_DAILY}?function=TIME_SERIES_DAILY&symbol=GLD&outputsize=compact"
        f"&apikey={urllib.parse.quote(key)}")
    series = (data or {}).get("Time Series (Daily)")
    if not series:
        return None
    try:
        dates = sorted(series.keys(), reverse=True)[:4]
        closes = [float(series[d]["4. close"]) for d in dates]
    except (KeyError, ValueError):
        return None
    if len(closes) < 4:
        return None
    # 3-day change: most recent close vs close 3 sessions ago.
    chg = (closes[0] - closes[3]) / closes[3] * 100
    if chg > 0.3:
        return "inflow"
    if chg < -0.3:
        return "outflow"
    return "flat"


def fetch_news_sentiment() -> Optional[float]:
    """Average gold news sentiment in [-1, +1] from Alpha Vantage (cached 3h)."""
    return _cached("av_news_gld", 3 * 3600, _fetch_news_raw)


def _fetch_news_raw() -> Optional[float]:
    """Query the GLD gold-ETF ticker (no clean spot-gold ticker) and average the
    GLD-specific ticker_sentiment_score, falling back to overall. None on
    rate-limit (no 'feed') / error."""
    key = _env("ALPHAVANTAGE_API_KEY")
    if not key:
        return None
    data = _get_json(
        "https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
        f"&tickers=GLD&limit=50&apikey={urllib.parse.quote(key)}")
    if not data or "feed" not in data:
        return None
    gld_scores: List[float] = []
    overall_scores: List[float] = []
    for article in data["feed"]:
        ov = article.get("overall_sentiment_score")
        if ov not in (None, ""):
            try:
                overall_scores.append(float(ov))
            except ValueError:
                pass
        for t in article.get("ticker_sentiment", []) or []:
            if t.get("ticker") == "GLD":
                try:
                    gld_scores.append(float(t["ticker_sentiment_score"]))
                except (ValueError, KeyError):
                    pass
    scores = gld_scores or overall_scores
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    return round(max(-1.0, min(1.0, avg)), 4)
