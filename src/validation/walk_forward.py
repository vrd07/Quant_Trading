"""
Walk-Forward Validation.

Splits data into rolling train/test windows so that parameters
are always calibrated on past data and tested on unseen future data.

Example from Instruct.md:
    Train: 2018-2022 → Test: 2023
    Re-calibrate
    Train: 2019-2023 → Test: 2024

Never optimise on the full dataset.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Callable, Dict, Any


@dataclass
class WalkForwardResult:
    """Result of a walk-forward validation run."""
    n_splits: int = 0
    sharpe_ratios: List[float] = field(default_factory=list)
    max_drawdowns: List[float] = field(default_factory=list)
    total_returns: List[float] = field(default_factory=list)
    all_returns: List[pd.Series] = field(default_factory=list)

    @property
    def mean_sharpe(self) -> float:
        return float(np.mean(self.sharpe_ratios)) if self.sharpe_ratios else 0.0

    @property
    def mean_drawdown(self) -> float:
        return float(np.mean(self.max_drawdowns)) if self.max_drawdowns else 0.0

    @property
    def mean_return(self) -> float:
        return float(np.mean(self.total_returns)) if self.total_returns else 0.0

    def summary(self) -> Dict[str, Any]:
        return {
            "n_splits": self.n_splits,
            "mean_sharpe": round(self.mean_sharpe, 4),
            "mean_max_dd": round(self.mean_drawdown, 4),
            "mean_return": round(self.mean_return, 4),
            "sharpe_per_fold": [round(s, 4) for s in self.sharpe_ratios],
        }


def walk_forward_split(
    df: pd.DataFrame,
    train_pct: float = 0.7,
    n_splits: int = 5,
) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Create rolling train/test splits.

    The data is divided into (n_splits + 1) equal blocks.  Each split uses
    the first *train_pct* of the available-so-far data for training and the
    next block for testing.  This ensures a strictly expanding training
    window with no future data leakage.

    Args:
        df: Full historical DataFrame (must be sorted chronologically).
        train_pct: Fraction of available data used for training in each fold.
        n_splits: Number of out-of-sample test windows.

    Returns:
        List of (train_df, test_df) tuples.
    """
    n = len(df)
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")

    block_size = n // (n_splits + 1)
    if block_size < 2:
        raise ValueError("Not enough data for the requested number of splits")

    splits = []
    for i in range(1, n_splits + 1):
        test_end = min((i + 1) * block_size, n)
        test_start = i * block_size
        train_end = test_start
        train_start = max(0, int(train_end - train_end * train_pct))

        train = df.iloc[train_start:train_end].copy()
        test = df.iloc[test_start:test_end].copy()
        splits.append((train, test))

    return splits


def _compute_sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def _compute_max_dd(returns: pd.Series) -> float:
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    return float(drawdown.min())


def run_walk_forward(
    df: pd.DataFrame,
    strategy_fn: Callable[[pd.DataFrame, pd.DataFrame], pd.Series],
    n_splits: int = 5,
    train_pct: float = 0.7,
    periods_per_year: int = 252,
) -> WalkForwardResult:
    """
    Run walk-forward validation.

    Args:
        df: Full historical OHLCV DataFrame.
        strategy_fn: Callable(train_df, test_df) -> pd.Series of returns
                      on the test set (the function should calibrate on
                      train_df and produce returns on test_df).
        n_splits: Number of out-of-sample windows.
        train_pct: Fraction of data for training in each fold.
        periods_per_year: For Sharpe annualisation.

    Returns:
        WalkForwardResult with per-fold metrics.
    """
    splits = walk_forward_split(df, train_pct=train_pct, n_splits=n_splits)
    result = WalkForwardResult(n_splits=len(splits))

    for train, test in splits:
        test_returns = strategy_fn(train, test)
        result.all_returns.append(test_returns)
        result.sharpe_ratios.append(_compute_sharpe(test_returns, periods_per_year))
        result.max_drawdowns.append(_compute_max_dd(test_returns))
        result.total_returns.append(float((1 + test_returns).prod() - 1))

    return result
