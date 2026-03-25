#!/usr/bin/env python3
"""
Strategy Scorer — RL-lite feedback from trade results.

Reads trade_journal.csv, computes per-strategy performance metrics
grouped by market regime, and returns weight adjustments for the
regime classifier.

The scoring uses a simple empirical Bayesian approach:
  - Strategies that profit in a regime get weight boosts
  - Strategies that consistently lose get weight reductions
  - Guardrails prevent weights from going below 0.05 or above 0.95

This is NOT full reinforcement learning — it is a lightweight
performance feedback loop that avoids the complexity and data
requirements of a proper RL agent while still adapting to reality.
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_JOURNAL = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"

# Guardrails: weights cannot go below or above these bounds
WEIGHT_FLOOR = 0.05
WEIGHT_CEILING = 0.95

# geohot lens: pre-compute at module level, not per-call
_REQUIRED_JOURNAL_COLS = frozenset({"strategy", "realized_pnl"})


def load_trade_journal(journal_path: Path = None) -> pd.DataFrame:
    """Load and clean the trade journal CSV.

    Returns a DataFrame with columns:
        strategy, regime, realized_pnl, pnl_pct, duration_seconds
    Filters out rows with 'unknown' strategy (legacy trades).
    """
    path = journal_path or DEFAULT_JOURNAL
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path)
    if df.empty:
        return pd.DataFrame()

    if not _REQUIRED_JOURNAL_COLS.issubset(df.columns):
        return pd.DataFrame()

    # Filter out non-bot trades: 'unknown' (legacy) and 'manual' (human-opened positions)
    # so that human trading decisions don't distort strategy performance weights.
    df = df[~df["strategy"].isin({"unknown", "manual"})].copy()
    if df.empty:
        return pd.DataFrame()

    df["realized_pnl"] = pd.to_numeric(df["realized_pnl"], errors="coerce")
    df["pnl_pct"] = pd.to_numeric(df.get("pnl_pct", 0), errors="coerce")
    df["duration_seconds"] = pd.to_numeric(
        df.get("duration_seconds", 0), errors="coerce"
    )

    # Parse entry_time for lookback filtering
    if "entry_time" in df.columns:
        df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True, errors="coerce")

    return df


def compute_strategy_scores(
    journal_path: Path = None,
    lookback_days: int = 30,
) -> dict:
    """Compute per-strategy performance scores from recent trades.

    Returns:
        {strategy_name: score} where score is in [-1.0, 1.0].
        Positive = strategy is profitable, negative = losing.
        Returns empty dict if insufficient data.
    """
    df = load_trade_journal(journal_path)
    if df.empty or len(df) < 5:
        return {}

    # Apply lookback filter
    if "entry_time" in df.columns:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        df = df[df["entry_time"] >= cutoff]

    if len(df) < 5:
        return {}

    scores = {}
    for strategy, group in df.groupby("strategy"):
        n_trades = len(group)
        if n_trades < 3:
            continue

        win_rate = (group["realized_pnl"] > 0).mean()
        avg_pnl = group["realized_pnl"].mean()
        std_pnl = group["realized_pnl"].std()

        # Compute a composite score:
        # - Win rate component (0–1)
        # - Risk-adjusted return (Sharpe-like, capped)
        # - Sample size penalty for small sample counts
        sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0.0
        sharpe_capped = np.clip(sharpe, -2.0, 2.0) / 2.0  # normalize to [-1, 1]

        # Blend win rate and Sharpe, with sample penalty
        sample_factor = min(n_trades / 20.0, 1.0)  # full weight at 20+ trades
        raw_score = (0.4 * (win_rate - 0.5) * 2.0) + (0.6 * sharpe_capped)
        score = raw_score * sample_factor

        scores[strategy] = round(np.clip(score, -1.0, 1.0), 4)

    return scores


def compute_regime_strategy_scores(
    journal_path: Path = None,
    lookback_days: int = 30,
) -> dict:
    """Compute per-strategy scores broken down by market regime.

    Returns:
        {regime: {strategy: score}}  e.g. {"TREND": {"kalman_regime": 0.4}, ...}
        Strategies with < 3 trades in a regime are omitted (insufficient data).
        Falls back to global scores for missing regimes.
    """
    df = load_trade_journal(journal_path)
    if df.empty or len(df) < 5:
        return {}

    if "entry_time" in df.columns:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        df = df[df["entry_time"] >= cutoff]

    if len(df) < 5:
        return {}

    # Normalise regime column; if absent, treat all as unknown
    if "regime" not in df.columns:
        return {}

    df["regime"] = df["regime"].str.upper().fillna("UNKNOWN")

    result: dict = {}
    for regime, regime_grp in df.groupby("regime"):
        if regime == "UNKNOWN":
            continue
        regime_scores: dict = {}
        for strategy, grp in regime_grp.groupby("strategy"):
            n_trades = len(grp)
            if n_trades < 3:
                continue
            win_rate = (grp["realized_pnl"] > 0).mean()
            avg_pnl = grp["realized_pnl"].mean()
            std_pnl = grp["realized_pnl"].std()
            sharpe = avg_pnl / std_pnl if std_pnl > 1e-9 else 0.0
            sharpe_capped = float(np.clip(sharpe, -2.0, 2.0)) / 2.0
            sample_factor = min(n_trades / 20.0, 1.0)
            raw_score = (0.4 * (win_rate - 0.5) * 2.0) + (0.6 * sharpe_capped)
            regime_scores[strategy] = round(float(np.clip(raw_score * sample_factor, -1.0, 1.0)), 4)
        if regime_scores:
            result[regime] = regime_scores

    return result


def adjust_weights(
    base_weights: dict,
    performance_scores: dict,
    blend_ratio: float = 0.5,
) -> dict:
    """Blend static base weights with performance-adjusted weights.

    Args:
        base_weights: {strategy: weight} from STRATEGY_WEIGHTS table
        performance_scores: {strategy: score} from compute_strategy_scores()
        blend_ratio: how much weight to give performance (0=all static, 1=all performance)

    Returns:
        {strategy: adjusted_weight} with guardrails applied
    """
    adjusted = {}
    for strategy, base_w in base_weights.items():
        perf_score = performance_scores.get(strategy, 0.0)

        # Convert score [-1, 1] to weight adjustment [-0.3, +0.3]
        adjustment = perf_score * 0.3

        # Blend: final = (1-blend) × base + blend × (base + adjustment)
        final_w = base_w + (blend_ratio * adjustment)

        # Apply guardrails
        final_w = max(WEIGHT_FLOOR, min(WEIGHT_CEILING, final_w))
        adjusted[strategy] = round(final_w, 4)

    return adjusted


if __name__ == "__main__":
    print("=== Strategy Performance Scorer ===\n")
    scores = compute_strategy_scores()
    if not scores:
        print("Insufficient trade data for scoring.")
    else:
        for strat, score in sorted(scores.items(), key=lambda x: -x[1]):
            icon = "📈" if score > 0 else "📉"
            print(f"  {icon} {strat:<20} score={score:+.4f}")
