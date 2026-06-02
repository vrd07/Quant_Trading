"""
Market Sentiment Engine (skeleton).

Produces a deterministic Gold Sentiment Score (GSS, 0-100) from macro,
technical, positioning and news inputs, per ``market_sentiment.md`` §4.

DESIGN CONTRACT (do not break):
  - ``gss.py`` is PURE and deterministic — same inputs always give the same
    score. It is the only thing the execution path is ever allowed to consume,
    and only AFTER it passes the backtest gate in ``backtest.md``.
  - ``feeds.py`` does the dirty I/O (external APIs). Every feed fails SAFE:
    a missing input contributes a NEUTRAL sub-score, never a forced direction
    and never an exception that reaches the trading loop.
  - ``store.py`` persists the latest score to data/sentiment/ on a SLOW clock
    (15-60 min). Nothing here runs per-tick.

Nothing in this package is wired into the live trading pipeline yet. It is a
deterministic, unit-tested input waiting on a passing backtest.
"""

from .gss import (
    GSSComponents,
    GSSResult,
    compute_gss,
    regime_for_score,
)

__all__ = [
    "GSSComponents",
    "GSSResult",
    "compute_gss",
    "regime_for_score",
]
