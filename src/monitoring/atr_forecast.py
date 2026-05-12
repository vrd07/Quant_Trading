"""
ATR forecaster for the live monitor.

Backtest finding (scripts/backtest_atr_forecast.py): one-bar-ahead ATR%
*magnitude* prediction cannot beat naive persistence on this data — ATR is
too autocorrelated and the EWMA/ARCH σ→ATR conversion is miscalibrated.
However, the *direction* of vol change (rising / stable / falling) is
predictable at ~60 % accuracy on XAUUSD vs a ~43 % rolling baseline.

So this module deliberately emits a categorical `vol_outlook` rather than a
numeric forecast. Inputs:

  1. Markov chain over discretized ATR%-quantile states — provides an
     unbiased next-step ATR% expectation (mean ≈ realized mean in backtest).
  2. Price-action sentiment proxy (RSI extremity + up-bar fraction + momentum z).
     NOTE: this is a proxy from price data — it is NOT external social/news
     sentiment. Field is named `sentiment_proxy` so callers cannot conflate.
  3. News pressure from the ForexFactory events the emitter already loads —
     imminence of next high-impact event for the symbol's exposure currencies
     plus density of high-impact events in the next 24h.

Decision rule for vol_outlook:
    delta_pct = (markov_pct - current_atr) / current_atr
    risk_tilt = 0.25·|sentiment_proxy| + 0.4·news_pressure       (∈ [0, ~0.65])
    adjusted  = delta_pct + risk_tilt                            (asymmetric: news/sentiment only push UP)
    > +0.05 → RISING
    < -0.05 → FALLING
    else    → STABLE

`direction` (UP / FLAT / DOWN) is unchanged price-direction logic.

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
    direction: str                # price direction: 'UP' | 'DOWN' | 'FLAT'
    vol_outlook: str              # vol direction: 'RISING' | 'STABLE' | 'FALLING'
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


def direction_mta(
    close: pd.Series,
    lookbacks: Tuple[int, ...] = (20, 80, 240),
    deadband: float = 0.001,
    weights: Optional[Tuple[float, ...]] = None,
) -> Dict[str, Any]:
    """Multi-timeframe direction on a single bar series via varying lookback.

    The classical MTA rule of thumb (Murphy / Elder) is that the dominant trend
    sits on the higher timeframe and lower TFs are for entry/timing. We emulate
    that on a single bar series by looking at progressively longer windows:
    a 20-bar window captures recent momentum, 240 bars captures the longer
    drift. Longer windows get higher weight so the consensus tilts toward the
    bigger trend, exactly as in classical MTA.

    Returns:
        {
          "consensus":  "UP" | "DOWN" | "FLAT",
          "strength":   abs(score) ∈ [0, 1] — closer to 1 = more aligned,
          "score":      signed score ∈ [-1, +1],
          "votes":      {lookback: "UP"|"DOWN"|"FLAT"} actually evaluated,
          "n_aligned":  int — how many windows match consensus,
          "n_total":    int — windows actually evaluated (some skipped on short history)
        }
    """
    if weights is None:
        # Default: weight ∝ lookback. So longer lookback → more influence,
        # matching MTA's "let the higher timeframe dominate" rule.
        weights = tuple(float(lb) for lb in lookbacks)
    if len(weights) != len(lookbacks):
        raise ValueError("weights and lookbacks must have the same length")

    sign_map = {"UP": 1, "DOWN": -1, "FLAT": 0}
    votes: Dict[int, str] = {}
    weighted_sum = 0.0
    weight_total = 0.0

    for lb, w in zip(lookbacks, weights):
        if len(close) < lb + 1:
            continue
        d = direction_from_returns(close, lookback=lb, deadband=deadband)
        votes[lb] = d
        weighted_sum += w * sign_map[d]
        weight_total += w

    if weight_total <= 0:
        return {
            "consensus": "FLAT", "strength": 0.0, "score": 0.0,
            "votes": votes, "n_aligned": 0, "n_total": 0,
        }

    score = weighted_sum / weight_total
    # Consensus thresholds chosen so a unanimous longer-window vote wins even
    # if the shortest disagrees. With weights = lookbacks (20, 80, 240) the
    # short window can move score by at most 20/340 ≈ 0.06, so 0.15 puts the
    # cut comfortably above noise from a single dissenter on the short window.
    if score > 0.15:
        consensus = "UP"
    elif score < -0.15:
        consensus = "DOWN"
    else:
        consensus = "FLAT"

    n_aligned = sum(1 for v in votes.values() if v == consensus)
    return {
        "consensus": consensus,
        "strength": float(abs(score)),
        "score": float(score),
        "votes": votes,
        "n_aligned": n_aligned,
        "n_total": len(votes),
    }


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
    outlook_threshold: float = 0.05,
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
    if not np.isfinite(cur_atr_pct) or cur_atr_pct <= 0:
        return None

    markov_fc = _markov_atr_forecast(
        atr_pct_series, n_states=n_markov_states, window=markov_window
    )

    sp = _sentiment_proxy(close, lookback=20)
    ccys = _SYMBOL_CCY.get(symbol.split(".")[0].upper(), ("USD",))
    npress = _news_pressure(upcoming_events or [], ccys)

    # delta = relative move Markov predicts; risk_tilt only pushes upward
    # (vol spikes on news/extreme sentiment, doesn't compress).
    delta_pct = (markov_fc - cur_atr_pct) / cur_atr_pct
    risk_tilt = 0.25 * abs(sp) + 0.4 * npress
    adjusted = delta_pct + risk_tilt

    if adjusted > outlook_threshold:
        outlook = "RISING"
    elif adjusted < -outlook_threshold:
        outlook = "FALLING"
    else:
        outlook = "STABLE"

    return AtrForecast(
        atr_pct=round(cur_atr_pct, 3),
        direction=direction_from_returns(close, lookback=20, deadband=0.001),
        vol_outlook=outlook,
        components={
            "markov_pct": round(markov_fc, 3),
            "delta_pct": round(delta_pct, 3),
            "sentiment_proxy": round(sp, 3),
            "news_pressure": round(npress, 3),
            "risk_tilt": round(risk_tilt, 3),
        },
    )
