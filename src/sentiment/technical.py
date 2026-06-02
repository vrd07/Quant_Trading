"""
Technical bias — computed from OUR OWN data, no external API.

market_sentiment.md §3.3 wants EODHD for EMA/RSI/MACD/BB/ATR. We already store
the canonical XAUUSD 5m series and already have a vetted ``Indicators`` library,
so we compute these locally (geohot: own the stack, no black-box dependency).
This is the one GSS component that is fully REAL today.

Pure-ish: the only side effect is reading the historical CSV. Returns None on
any problem so the scorer falls back to a neutral technical sub-score.
"""
from __future__ import annotations

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


def compute_technical(symbol: str = "XAUUSD") -> Dict[str, Any]:
    """Real technical bias + market-structure fields.

    Returns a dict always containing ``points`` (None if data unavailable) and a
    ``structure`` block for the monitor / Claude context. ``points`` is fed to
    GSSComponents.technical.
    """
    out: Dict[str, Any] = {"points": None, "structure": {}, "source": "local_5m_csv"}
    daily = _load_daily(symbol)
    if daily is None or daily.empty:
        return out

    close = float(daily["close"].iloc[-1])
    ema50 = float(Indicators.ema(daily, 50).iloc[-1])
    ema200 = float(Indicators.ema(daily, 200).iloc[-1]) if len(daily) >= 200 else float("nan")
    rsi = float(Indicators.rsi(daily, 14).iloc[-1])
    macd_line, signal_line, hist = Indicators.macd(daily)
    macd_bullish = bool(macd_line.iloc[-1] > signal_line.iloc[-1])
    upper, _mid, lower = Indicators.bollinger_bands(daily)
    atr14 = float(Indicators.atr(daily, 14).iloc[-1])

    has200 = ema200 == ema200  # not NaN
    if has200 and close > ema50 > ema200:
        trend = "bull_aligned"
    elif has200 and close < ema50 < ema200:
        trend = "bear_aligned"
    elif close > ema50:
        trend = "recovering"
    else:
        trend = "chop"

    last_close = close
    if last_close > float(upper.iloc[-1]):
        bb_state = "upper_walk"
    elif last_close < float(lower.iloc[-1]):
        bb_state = "lower_breach"
    else:
        bb_state = "inside"

    # Nearest support/resistance from the last 20 daily swings (excl. today).
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
    }
    return out
