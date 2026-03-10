"""
Bayesian Optimization for strategy parameters.

Uses scikit-optimize (skopt) for probabilistic global optimisation.
More sample-efficient than genetic search for expensive objective functions.

Install:
    pip install scikit-optimize

Usage:
    from src.optimization.bayesian import optimize_params
    result = optimize_params(objective, param_space, n_calls=50)
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, Tuple, List, Any

import numpy as np


@dataclass
class BayesResult:
    """Result of Bayesian optimisation."""
    best_params: Dict[str, float]
    best_score: float
    all_scores: List[float] = field(default_factory=list)


def optimize_params(
    objective_fn: Callable[[Dict[str, float]], float],
    param_space: Dict[str, Tuple[float, float]],
    n_calls: int = 50,
    n_initial_points: int = 10,
    seed: int = 42,
) -> BayesResult:
    """
    Optimise parameters using Bayesian optimisation (Gaussian Process).

    The objective_fn should return a value to **maximise** (e.g. Sharpe ratio).
    Internally we negate it because skopt minimises.

    Args:
        objective_fn: Callable(params_dict) → score (higher is better).
        param_space: Dict mapping param name → (min, max).
        n_calls: Total number of evaluations.
        n_initial_points: Random exploration points before modelling.
        seed: Random seed.

    Returns:
        BayesResult with the best parameters and score history.
    """
    try:
        from skopt import gp_minimize
        from skopt.space import Real
    except ImportError:
        raise ImportError(
            "scikit-optimize is required for Bayesian optimization. "
            "Install it with: pip install scikit-optimize"
        )

    param_names = list(param_space.keys())
    dimensions = [Real(lo, hi, name=name) for name, (lo, hi) in param_space.items()]

    all_scores: List[float] = []

    def _objective(values):
        params = dict(zip(param_names, values))
        try:
            score = objective_fn(params)
        except Exception:
            score = -1e6
        all_scores.append(score)
        return -score  # skopt minimises

    result = gp_minimize(
        _objective,
        dimensions,
        n_calls=n_calls,
        n_initial_points=n_initial_points,
        random_state=seed,
    )

    best_params = dict(zip(param_names, result.x))
    best_score = -result.fun

    return BayesResult(
        best_params=best_params,
        best_score=best_score,
        all_scores=all_scores,
    )
