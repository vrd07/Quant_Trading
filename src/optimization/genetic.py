"""
Genetic Algorithm Optimizer.

Optimises strategy parameters using evolutionary search.

Tunable parameters (from Instruct.md):
    - Kalman q / r
    - Z-score threshold
    - Volatility window
    - ATR stop multiplier

Fitness function:
    fitness = Sharpe − λ × |MaxDrawdown|

Avoids brute-force grid search by using random mutation,
crossover, and tournament selection.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Callable, List, Tuple, Any


@dataclass
class Individual:
    """A candidate solution (set of parameters)."""
    params: Dict[str, float]
    fitness: float = float("-inf")


@dataclass
class GeneticResult:
    """Result of a genetic optimisation run."""
    best_params: Dict[str, float]
    best_fitness: float
    history: List[float] = field(default_factory=list)  # best fitness per generation


class GeneticOptimizer:
    """
    Genetic algorithm for strategy parameter tuning.

    Usage:
        optimizer = GeneticOptimizer(param_space, fitness_fn)
        result = optimizer.run()
    """

    def __init__(
        self,
        param_space: Dict[str, Tuple[float, float]],
        fitness_fn: Callable[[Dict[str, float]], float],
        population_size: int = 50,
        n_generations: int = 30,
        mutation_rate: float = 0.2,
        crossover_rate: float = 0.7,
        tournament_size: int = 3,
        lambda_dd: float = 0.5,
        seed: int = None,
    ):
        """
        Args:
            param_space: Dict mapping param name → (min, max).
            fitness_fn: Callable(params_dict) → fitness score.
                        Should already incorporate the
                        Sharpe − λ * drawdown logic if desired.
            population_size: Number of individuals.
            n_generations: Number of generations.
            mutation_rate: Probability of mutating each gene.
            crossover_rate: Probability of crossover between parents.
            tournament_size: Tournament selection size.
            lambda_dd: (unused if fitness_fn handles it) — included
                       for documentation purposes.
            seed: Random seed.
        """
        self.param_space = param_space
        self.fitness_fn = fitness_fn
        self.pop_size = population_size
        self.n_gen = n_generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_size = tournament_size
        self.rng = np.random.default_rng(seed)

    # ── Population initialisation ──────────────────

    def _random_individual(self) -> Individual:
        params = {}
        for name, (lo, hi) in self.param_space.items():
            params[name] = float(self.rng.uniform(lo, hi))
        return Individual(params=params)

    def _init_population(self) -> List[Individual]:
        return [self._random_individual() for _ in range(self.pop_size)]

    # ── Selection ──────────────────────────────────

    def _tournament_select(self, population: List[Individual]) -> Individual:
        candidates = self.rng.choice(len(population), size=self.tournament_size, replace=False)
        best = max(candidates, key=lambda idx: population[idx].fitness)
        return population[best]

    # ── Crossover ──────────────────────────────────

    def _crossover(self, p1: Individual, p2: Individual) -> Individual:
        child_params: Dict[str, float] = {}
        for name in self.param_space:
            if self.rng.random() < self.crossover_rate:
                # Blend crossover
                alpha = self.rng.uniform(0, 1)
                child_params[name] = alpha * p1.params[name] + (1 - alpha) * p2.params[name]
            else:
                child_params[name] = p1.params[name]
        return Individual(params=child_params)

    # ── Mutation ───────────────────────────────────

    def _mutate(self, ind: Individual) -> Individual:
        params = dict(ind.params)
        for name, (lo, hi) in self.param_space.items():
            if self.rng.random() < self.mutation_rate:
                # Gaussian perturbation (10% of range)
                sigma = (hi - lo) * 0.1
                params[name] = float(np.clip(params[name] + self.rng.normal(0, sigma), lo, hi))
        return Individual(params=params)

    # ── Evaluate ───────────────────────────────────

    def _evaluate(self, population: List[Individual]) -> None:
        for ind in population:
            if ind.fitness == float("-inf"):
                try:
                    ind.fitness = self.fitness_fn(ind.params)
                except Exception:
                    ind.fitness = float("-inf")

    # ── Main loop ──────────────────────────────────

    def run(self) -> GeneticResult:
        """Execute the genetic algorithm and return the best result."""
        population = self._init_population()
        self._evaluate(population)

        history: List[float] = []

        for gen in range(self.n_gen):
            new_pop: List[Individual] = []

            # Elitism: keep best individual
            best = max(population, key=lambda x: x.fitness)
            new_pop.append(Individual(params=dict(best.params), fitness=best.fitness))

            while len(new_pop) < self.pop_size:
                p1 = self._tournament_select(population)
                p2 = self._tournament_select(population)
                child = self._crossover(p1, p2)
                child = self._mutate(child)
                new_pop.append(child)

            self._evaluate(new_pop)
            population = new_pop

            best = max(population, key=lambda x: x.fitness)
            history.append(best.fitness)

        best = max(population, key=lambda x: x.fitness)
        return GeneticResult(
            best_params=best.params,
            best_fitness=best.fitness,
            history=history,
        )
