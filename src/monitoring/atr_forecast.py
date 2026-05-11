"""
ATR forecaster for the live monitor.

Outputs a single AtrForecast per symbol, computed from the same bars the rest
of the bot uses (DataEngine.get_bars). Five inputs are combined:

  1. Markov chain over discretized ATR%-quantile states (next-step expectation).
  2. EWMA variance forecast (RiskMetrics λ=0.94) of log-returns.
  3. ARCH(1) variance forecast via OLS on r²_t ~ r²_{t-1}.
  4. Price-action sentiment proxy (RSI extremity + up-bar fraction + momentum z).
     NOTE: this is a proxy from price data — it is NOT external social/news
     sentiment. Field is named `sentiment_proxy` so callers cannot conflate.
  5. News pressure from the ForexFactory events the emitter already loads —
     imminence of next high-impact event for the symbol's exposure currencies
     plus density of high-impact events in the next 24h.

The base forecast is a weighted blend of (1)+(2)+(3). Sentiment and news only
add a bounded upward tilt (capped at +40 %): they push vol forecasts UP when
risk is imminent, never down. That asymmetry matches how vol actually reacts
to news.

Direction is the same 20-bar deadband logic the emitter used before — kept
unchanged so the existing UI semantics don't shift.

This module is pure-Python/pandas/numpy. No new dependencies, no I/O, no state.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


# Symbol → currencies whose news events should count toward news pressure.
# Conservative mapping; symbols not listed fall back to ("USD",).
_SYMBOL_CCY: Dict[str, Tuple[str, ...]] = {
    "XAUUSD": ("USD",),
    "XAGUSD": ("USD",),
    "EURUSD": ("USD", "EUR"),
    "GBPUSD": ("USD", "GBP"),
    "USDJPY": ("USD", "JPY"),
    "BTCUSD": ("USD",),
    "ETHUSD": ("USD",),
}


@dataclass
class AtrForecast:
    atr_pct: float                # current Wilder ATR as % of price
    forecast_pct: float           # next-bar ATR% forecast (blended)
    direction: str                # 'UP' | 'DOWN' | 'FLAT'
    components: Dict[str, float] = field(default_factory=dict)


def wilder_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder's ATR — true range with gap handling, RMA (EMA with α=1/period)."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def direction_from_returns(
    close: pd.Series, lookback: int = 20, deadband: float = 0.001
) -> str:
    if len(close) < lookback + 1:
        return "FLAT"
    chg = float(close.iloc[-1] / close.iloc[-lookback - 1] - 1.0)
    if chg > deadband:
        return "UP"
    if chg < -deadband:
        return "DOWN"
    return "FLAT"


def _ewma_var_forecast(returns: pd.Series, lam: float = 0.94) -> float:
    r2 = (returns.dropna() ** 2).values
    if len(r2) < 5:
        return float(r2.mean()) if len(r2) else 0.0
    var = float(r2[0])
    for x in r2[1:]:
        var = lam * var + (1.0 - lam) * float(x)
    return var


def _arch1_var_forecast(returns: pd.Series) -> float:
    """σ²_{t+1} = ω + α·r²_t. ω, α via OLS on r² vs lagged r², clamped for stationarity."""
    r2 = (returns.dropna() ** 2).values
    if len(r2) < 30:
        return float(r2.mean()) if len(r2) else 0.0
    y = r2[1:]
    x = r2[:-1]
    x_mean = x.mean()
    y_mean = y.mean()
    denom = float(((x - x_mean) ** 2).sum()) or 1e-12
    alpha = float(((x - x_mean) * (y - y_mean)).sum() / denom)
    alpha = max(0.0, min(0.9, alpha))
    omega = max(0.0, float(y_mean - alpha * x_mean))
    return omega + alpha * float(r2[-1])


def _markov_atr_forecast(
    atr_pct_series: pd.Series, n_states: int = 5, window: int = 500
) -> float:
    """Discretize ATR% into n quantile buckets on the trailing window, fit a
    Laplace-smoothed transition matrix, return next-step expected ATR%."""
    s = atr_pct_series.dropna().tail(window)
    if len(s) < n_states * 4:
        return float(s.iloc[-1]) if len(s) else 0.0

    qs = np.linspace(0.0, 1.0, n_states + 1)
    edges = np.unique(np.quantile(s.values, qs))
    if len(edges) - 1 < 2:
        return float(s.iloc[-1])

    states = np.clip(np.digitize(s.values, edges[1:-1]), 0, n_states - 1)
    bucket_means = np.array(
        [
            float(s.values[states == k].mean()) if (states == k).any() else 0.0
            for k in range(n_states)
        ]
    )

    T = np.ones((n_states, n_states))  # Laplace prior
    for a, b in zip(states[:-1], states[1:]):
        T[a, b] += 1.0
    T = T / T.sum(axis=1, keepdims=True)

    current = int(states[-1])
    return float((T[current] * bucket_means).sum())


def _sentiment_proxy(close: pd.Series, lookback: int = 20) -> float:
    """[-1, +1]. Price-action proxy ONLY — RSI extremity + up-bar fraction + momentum z."""
    if len(close) < lookback + 1:
        return 0.0
    diff = close.diff().tail(lookback)
    up = float(diff.clip(lower=0).mean())
    dn = float((-diff.clip(upper=0)).mean())
    if dn > 0:
        rsi = 100.0 - 100.0 / (1.0 + up / dn)
    else:
        rsi = 100.0 if up > 0 else 50.0
    rsi_signed = (rsi - 50.0) / 50.0

    up_frac = float((diff > 0).mean()) * 2.0 - 1.0

    ret = close.pct_change().tail(lookback).dropna()
    if len(ret) > 1 and ret.std() > 0:
        z = float(ret.mean() / ret.std())
        z = max(-3.0, min(3.0, z)) / 3.0
    else:
        z = 0.0

    return float(0.5 * rsi_signed + 0.3 * up_frac + 0.2 * z)


def _news_pressure(
    upcoming_events: Iterable[Dict[str, Any]], symbol_currencies: Iterable[str]
) -> float:
    """[0, 1]. Imminence of next HIGH-impact event (exp-decay, τ=120 min) blended
    with density of HIGH events in the next 24h. Returns 0 when no relevant events."""
    ccys = {c.upper() for c in symbol_currencies}
    relevant = [
        e for e in upcoming_events if (e.get("currency") or "").upper() in ccys
    ]
    if not relevant:
        return 0.0
    high = [e for e in relevant if (e.get("impact") or "").upper() == "HIGH"]
    if not high:
        return 0.0
    next_min = min(int(e.get("mins_until", 99999) or 99999) for e in high)
    imminence = float(np.exp(-max(0, next_min) / 120.0))
    count_24h = sum(
        1 for e in high if 0 <= int(e.get("mins_until", -1) or -1) <= 24 * 60
    )
    density = min(1.0, count_24h / 3.0)
    return float(0.7 * imminence + 0.3 * density)


def compute_forecast(
    bars: pd.DataFrame,
    symbol: str,
    upcoming_events: Optional[List[Dict[str, Any]]] = None,
    atr_period: int = 14,
    n_markov_states: int = 5,
    markov_window: int = 500,
) -> Optional[AtrForecast]:
    """Compute one forecast row. Returns None if the bar history is too short."""
    if bars is None or len(bars) < max(atr_period * 4, 40):
        return None
    for col in ("high", "low", "close"):
        if col not in bars.columns:
            return None

    close = bars["close"].astype(float)
    last_close = float(close.iloc[-1])
    if last_close <= 0:
        return None

    atr_series = wilder_atr(bars["high"].astype(float), bars["low"].astype(float), close, atr_period)
    atr_pct_series = atr_series / close * 100.0
    cur_atr_pct = float(atr_pct_series.iloc[-1])
    if not np.isfinite(cur_atr_pct):
        return None

    markov_fc = _markov_atr_forecast(
        atr_pct_series, n_states=n_markov_states, window=markov_window
    )

    log_ret = np.log(close / close.shift(1))
    ewma_var = _ewma_var_forecast(log_ret)
    arch_var = _arch1_var_forecast(log_ret)
    # σ (per-bar log-return std) → ATR%-equivalent.
    # For a Brownian step the expected absolute deviation is σ·√(2/π); ATR's true
    # range is wider than |Δclose|, but on a per-bar basis this gives the right
    # order of magnitude without fitting a separate constant per timeframe.
    sigma_to_atr_pct = float(np.sqrt(2.0 / np.pi)) * 100.0
    ewma_atr_pct = float(np.sqrt(max(ewma_var, 0.0))) * sigma_to_atr_pct
    arch_atr_pct = float(np.sqrt(max(arch_var, 0.0))) * sigma_to_atr_pct
    if ewma_atr_pct == 0.0:
        ewma_atr_pct = cur_atr_pct
    if arch_atr_pct == 0.0:
        arch_atr_pct = cur_atr_pct

    sp = _sentiment_proxy(close, lookback=20)
    ccys = _SYMBOL_CCY.get(symbol.split(".")[0].upper(), ("USD",))
    npress = _news_pressure(upcoming_events or [], ccys)

    base = 0.5 * markov_fc + 0.3 * ewma_atr_pct + 0.2 * arch_atr_pct
    uplift = min(0.4, 0.25 * abs(sp) + 0.4 * npress)
    forecast = float(base * (1.0 + uplift))

    return AtrForecast(
        atr_pct=round(cur_atr_pct, 3),
        forecast_pct=round(forecast, 3),
        direction=direction_from_returns(close, lookback=20, deadband=0.001),
        components={
            "markov_pct": round(markov_fc, 3),
            "ewma_pct": round(ewma_atr_pct, 3),
            "arch_pct": round(arch_atr_pct, 3),
            "sentiment_proxy": round(sp, 3),
            "news_pressure": round(npress, 3),
        },
    )
