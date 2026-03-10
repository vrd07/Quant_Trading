"""
Monte Carlo Robustness Testing.

Randomly shuffles strategy returns and re-computes terminal equity
for N simulations.  If the strategy's actual performance is within
the range of shuffled results, it is likely overfit.

From Instruct.md:
    def monte_carlo(returns, simulations=1000):
        results = []
        for _ in range(simulations):
            shuffled = np.random.permutation(returns)
            equity = (1 + shuffled).cumprod()
            results.append(equity[-1])
        return results
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Union


def monte_carlo_equity(
    returns: Union[pd.Series, np.ndarray],
    n_simulations: int = 1000,
    seed: int = None,
) -> List[float]:
    """
    Run Monte Carlo simulations by shuffling returns.

    Args:
        returns: Array of strategy returns (e.g. daily pct changes).
        n_simulations: Number of random permutations to run.
        seed: Optional random seed for reproducibility.

    Returns:
        List of terminal equity values (one per simulation).
        Starting equity is normalised to 1.0.
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    if isinstance(returns, pd.Series):
        ret_arr = returns.dropna().values
    else:
        ret_arr = np.asarray(returns)

    ret_arr = ret_arr[np.isfinite(ret_arr)]

    if len(ret_arr) == 0:
        return [1.0] * n_simulations

    results: List[float] = []
    for _ in range(n_simulations):
        shuffled = rng.permutation(ret_arr)
        equity = np.prod(1 + shuffled)
        results.append(float(equity))

    return results


def confidence_interval(
    results: List[float],
    pct: float = 95,
) -> Tuple[float, float]:
    """
    Compute a symmetric confidence interval from simulation results.

    Args:
        results: Terminal equity values from monte_carlo_equity().
        pct: Confidence level (e.g. 95 for 95% CI).

    Returns:
        (lower_bound, upper_bound)
    """
    lower = (100 - pct) / 2
    upper = 100 - lower
    arr = np.array(results)
    return float(np.percentile(arr, lower)), float(np.percentile(arr, upper))


def p_value(
    actual_terminal: float,
    simulated_terminals: List[float],
) -> float:
    """
    Fraction of simulations that beat the actual strategy.

    A low p-value (< 0.05) suggests the strategy return is unlikely
    to be due to random ordering of trades → not overfit.

    Args:
        actual_terminal: The strategy's actual terminal equity.
        simulated_terminals: List from monte_carlo_equity().

    Returns:
        p-value (0 to 1).
    """
    arr = np.array(simulated_terminals)
    return float(np.mean(arr >= actual_terminal))
