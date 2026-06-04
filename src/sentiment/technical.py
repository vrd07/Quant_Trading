"""
Technical bias — computed from OUR OWN data, anchored to the live MT5 price.

market_sentiment.md §3.3 wants EODHD for EMA/RSI/MACD/BB/ATR. We already store
the canonical XAUUSD 5m series and have a vetted ``Indicators`` library, so we
compute these locally (geohot: own the stack, no black-box dependency).

Freshness: the stored 5m CSV only refreshes weekly, so mid-week its last bar is
several days old. The slow daily EMAs barely move over a few days, but the
*current* read (close, trend, BB position, RSI/MACD tip) must reflect NOW — so
when the running bot's live MT5 price is available we splice it onto the close
series as the forming bar and recompute the close-based indicators. ATR and
swing S/R stay on completed daily bars (a forming bar has no real range).

We do NOT open our own MT5 bridge connection: the file bridge is single-owner
(one shared command file, matched by request timestamp) and a second process
issuing GET_BARS races the live bot's 250ms loop. We piggyback on the price the
bot already publishes (``run_sentiment_engine._live_price``) instead.

Pure-ish: the only side effect is reading the historical CSV. Returns
points=None on any problem so the scorer falls back to a neutral sub-score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.data.indicators import Indicators
from .gss import score_technical

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_daily(symbol: str) -> Optional[pd.DataFrame]:
    """Load the canonical 5m series and resample to daily bars (left/left)."""
    src = _PROJECT_ROOT / "data" / "historical" / f"{symbol}_5m_real.csv"
    if not src.exists():
        return None
    try:
        df = pd.read_csv(src, parse_dates=["timestamp"], index_col="timestamp")
        daily = df.resample("1D", label="left", closed="left").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"])
        return daily if len(daily) >= 60 else None
    except Exception:
        return None


def _splice_live(daily: pd.DataFrame, live_price: float) -> pd.DataFrame:
    """Return ``daily`` with a forming bar at today's live price appended.

    If the last stored bar is already today's, it is replaced (we never stack two
    bars on the same UTC day). high/low/open mirror the live close — those fields
    are unused downstream for the close-based indicators (EMA/RSI/MACD/BB read
    ``close`` only); ATR is computed on the untouched completed frame.
    """
    tz = daily.index.tz
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    if tz is not None:
        today = today.tz_localize(tz)
    base = daily.iloc[:-1] if daily.index[-1].normalize() == today else daily
    forming = pd.DataFrame(
        {"open": [live_price], "high": [live_price], "low": [live_price],
         "close": [live_price], "volume": [0]}, index=[today])
    return pd.concat([base, forming])


def compute_technical(symbol: str = "XAUUSD",
                      live_price: Optional[float] = None) -> Dict[str, Any]:
    """Real technical bias + market-structure fields, anchored to the live price.

    Args:
        symbol: instrument key for the stored 5m series.
        live_price: latest MT5 mid from the running bot (``_live_price``). When
            present and positive, the close series is extended with a forming bar
            at this price so the read reflects NOW instead of the last stored
            (weekly-refreshed, often days-old) bar.

    Returns a dict always containing ``points`` (None if data unavailable), a
    ``structure`` block for the monitor / Claude context, ``anchored_live`` (was
    the live price spliced in) and ``as_of`` (timestamp of the last *completed*
    daily bar, so callers can see how stale the slow indicators are).
    ``points`` is fed to GSSComponents.technical.
    """
    out: Dict[str, Any] = {"points": None, "structure": {},
                           "source": "local_5m_csv", "anchored_live": False,
                           "as_of": None}
    daily = _load_daily(symbol)
    if daily is None or daily.empty:
        return out

    as_of = daily.index[-1]
    out["as_of"] = as_of.isoformat()
    anchored = bool(live_price and live_price > 0)
    out["anchored_live"] = anchored
    out["source"] = "5m_csv+live_mt5" if anchored else "local_5m_csv"

    # Close-based indicators (EMA/RSI/MACD/BB) read off a frame whose tail
    # reflects NOW when a live price is available; ATR + swing S/R stay on the
    # completed daily frame (a forming bar carries no real range).
    live_df = _splice_live(daily, live_price) if anchored else daily

    close = float(live_df["close"].iloc[-1])
    ema50 = float(Indicators.ema(live_df, 50).iloc[-1])
    ema200 = float(Indicators.ema(live_df, 200).iloc[-1]) if len(live_df) >= 200 else float("nan")
    rsi = float(Indicators.rsi(live_df, 14).iloc[-1])
    macd_line, signal_line, _hist = Indicators.macd(live_df)
    macd_bullish = bool(macd_line.iloc[-1] > signal_line.iloc[-1])
    upper, _mid, lower = Indicators.bollinger_bands(live_df)
    atr14 = float(Indicators.atr(daily, 14).iloc[-1])  # completed bars only

    has200 = ema200 == ema200  # not NaN
    if has200 and close > ema50 > ema200:
        trend = "bull_aligned"
    elif has200 and close < ema50 < ema200:
        trend = "bear_aligned"
    elif close > ema50:
        trend = "recovering"
    else:
        trend = "chop"

    if close > float(upper.iloc[-1]):
        bb_state = "upper_walk"
    elif close < float(lower.iloc[-1]):
        bb_state = "lower_breach"
    else:
        bb_state = "inside"

    # Nearest support/resistance from the last 20 completed daily swings.
    window = daily.iloc[-21:-1]
    nearest_support = float(window["low"].min()) if not window.empty else None
    nearest_resistance = float(window["high"].max()) if not window.empty else None

    out["points"] = score_technical(
        trend=trend, rsi_14=rsi, macd_bullish=macd_bullish, bb_state=bb_state)
    out["structure"] = {
        "price": round(close, 2),
        "ema_50": round(ema50, 2),
        "ema_200": round(ema200, 2) if has200 else None,
        "price_vs_50ema": "above" if close > ema50 else "below",
        "price_vs_200ema": ("above" if close > ema200 else "below") if has200 else "n/a",
        "trend": trend,
        "rsi_14": round(rsi, 1),
        "macd_signal": "bullish" if macd_bullish else "bearish",
        "bb_state": bb_state,
        "atr_14": round(atr14, 2),
        "atr_pct": round(atr14 / close * 100, 2) if close else None,
        "nearest_support": round(nearest_support, 2) if nearest_support else None,
        "nearest_resistance": round(nearest_resistance, 2) if nearest_resistance else None,
        "anchored_live": anchored,
        "as_of": as_of.isoformat(),
    }
    return out
