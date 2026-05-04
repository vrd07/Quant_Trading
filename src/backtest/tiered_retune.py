"""
TieredRetune — auto-retune protocol per backtest.md §7.

Given a strategy class, a single (IS, OOS) split, and a Grid (from grid_loader),
escalates through three tiers:

    Tier 1: cartesian over tier1_entry, optimize on IS, freeze winner, eval OOS
    Tier 2: hold tier1 winner, one-dim sweep over tier2_risk on IS, eval OOS
    Tier 3: hold tier1+tier2 winners, cartesian over tier3_filters on IS, eval OOS

After each tier, evaluates the OOS result against the §1 gates (G1..G7). If it
passes, return success. If all three tiers fail, return RetuneResult(passed=False).

This module does the *escalation logic* for a single window. A separate driver
(WalkForwardGridSearch — TODO) loops over the 57 walk-forward windows and calls
TieredRetune per window.

Phase 2 (ensemble) is a separate, larger piece — not built here.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Type

import pandas as pd

from .backtest_engine import BacktestEngine, BacktestResult
from .grid_loader import Grid
from ..core.types import Symbol
from ..strategies.base_strategy import BaseStrategy

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gates — backtest.md §1
# ---------------------------------------------------------------------------

@dataclass
class Gates:
    """Pass/fail thresholds. Defaults track backtest.md §1."""
    daily_win_rate_min: float = 0.70    # G1
    worst_day_r_min: float = -2.0       # G2
    profit_factor_min: float = 1.4      # G3
    sharpe_min: float = 1.0             # G4
    max_drawdown_pct_max: float = 12.0  # G5 (positive number; result.max_drawdown_pct is negative)
    min_trades_per_year: int = 60       # G6
    # G7/G8 evaluated by the walk-forward driver, not here.

    def evaluate(self, result: BacktestResult, oos_days: int) -> Dict[str, bool]:
        """Return per-gate pass/fail for an OOS BacktestResult."""
        years = max(1.0, oos_days / 252.0)
        trades_per_year = result.total_trades / years
        return {
            "G1_daily_win_rate": result.daily_win_rate >= self.daily_win_rate_min,
            "G2_worst_day_r":    result.worst_day_r >= self.worst_day_r_min,
            "G3_profit_factor":  result.profit_factor >= self.profit_factor_min,
            "G4_sharpe":         result.sharpe_ratio >= self.sharpe_min,
            "G5_max_dd":         abs(result.max_drawdown_pct) <= self.max_drawdown_pct_max,
            "G6_min_trades":     trades_per_year >= self.min_trades_per_year,
        }

    def all_pass(self, result: BacktestResult, oos_days: int) -> bool:
        return all(self.evaluate(result, oos_days).values())


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CombosOutcome:
    """One combo's IS+OOS outcome, used internally for ranking."""
    combo: Dict
    is_result: BacktestResult
    oos_result: Optional[BacktestResult] = None
    is_score: float = 0.0       # IS Sharpe with gate vetos (lower if vetoed)
    is_vetoed: bool = False
    veto_reasons: List[str] = field(default_factory=list)


@dataclass
class RetuneResult:
    """Final output of one tiered retune run."""
    passed: bool
    tier: int                   # 1, 2, or 3 (or 0 if all failed)
    winning_params: Dict
    oos_result: Optional[BacktestResult]
    gate_status: Dict[str, bool]
    n_combos_evaluated: int
    reason: str = ""

    @property
    def summary(self) -> str:
        if self.passed:
            return (
                f"PASS at tier {self.tier}: {self.n_combos_evaluated} combos. "
                f"OOS Sharpe={self.oos_result.sharpe_ratio:.2f}, "
                f"PF={self.oos_result.profit_factor:.2f}, "
                f"DailyWR={self.oos_result.daily_win_rate:.0%}, "
                f"WorstR={self.oos_result.worst_day_r:.2f}"
            )
        return f"FAIL all 3 tiers ({self.n_combos_evaluated} combos): {self.reason}"


# ---------------------------------------------------------------------------
# TieredRetune
# ---------------------------------------------------------------------------

class TieredRetune:
    """
    Run the §7 3-tier escalation on a single IS/OOS split for one strategy.

    Args:
        strategy_class: BaseStrategy subclass to instantiate per combo.
        symbol: Trading symbol (XAUUSD/BTCUSD/EURUSD).
        is_bars: In-sample bars (pd.DataFrame, OHLCV with timestamp index).
        oos_bars: Out-of-sample bars.
        grid: Grid loaded via grid_loader.load_grid_for(strategy_name).
        full_config: The full loaded config_live_*.yaml dict (passed to BacktestEngine).
        initial_capital: Starting capital per backtest.
        commission_per_trade: Forwarded to BacktestEngine.
        slippage_model: "realistic" | "fixed" | "aggressive" (strict model is TODO).
        gates: Pass/fail gates (default = backtest.md §1).
    """

    def __init__(
        self,
        strategy_class: Type[BaseStrategy],
        symbol: Symbol,
        is_bars: pd.DataFrame,
        oos_bars: pd.DataFrame,
        grid: Grid,
        full_config: Dict,
        initial_capital: Decimal,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "realistic",
        gates: Optional[Gates] = None,
    ):
        self.strategy_class = strategy_class
        self.symbol = symbol
        self.is_bars = is_bars
        self.oos_bars = oos_bars
        self.grid = grid
        self.full_config = full_config
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_model = slippage_model
        self.gates = gates or Gates()

        # Estimate OOS day count for trades-per-year scaling on G6
        self._oos_days = self._estimate_days(oos_bars)

    # ------------------------------------------------------------------
    def run(self) -> RetuneResult:
        """Execute the 3-tier escalation. Returns at the first passing tier or after tier 3."""
        total_combos = 0

        # ---- TIER 1 ------------------------------------------------------
        log.info("Tier 1: cartesian over tier1_entry")
        tier1_combos = self.grid.tier1_combos()
        outcomes_1 = self._evaluate_combos(tier1_combos)
        total_combos += len(outcomes_1)
        winner_1 = self._pick_winner(outcomes_1)
        if winner_1 is None:
            n_zero = sum(1 for o in outcomes_1 if "zero_trades_IS" in o.veto_reasons)
            n_g2 = sum(1 for o in outcomes_1 if "G2_worst_day_IS" in o.veto_reasons)
            best_oos = None
            best = max(outcomes_1, key=lambda o: o.is_result.sharpe_ratio, default=None)
            if best is not None:
                best.oos_result = self._run_one_safe(best.combo, self.oos_bars)
                best_oos = best.oos_result
            gate_status = self.gates.evaluate(best_oos, self._oos_days) if best_oos else {}
            return RetuneResult(
                passed=False, tier=0,
                winning_params=self.grid.build_config(best.combo) if best else {},
                oos_result=best_oos, gate_status=gate_status,
                n_combos_evaluated=total_combos,
                reason=(
                    f"All {len(outcomes_1)} tier1 combos vetoed on IS "
                    f"(zero-trades: {n_zero}, G2 worst-day-floor breached: {n_g2})"
                ),
            )
        if self._evaluate_oos_and_check(winner_1):
            return self._success(1, winner_1, total_combos)

        # ---- TIER 2 ------------------------------------------------------
        log.info("Tier 2: one-dim sweep over tier2_risk (anchored to tier1 winner)")
        tier2_sweep_combos = [
            {**winner_1.combo, **sweep}
            for sweep in self.grid.tier2_sweeps()
        ]
        outcomes_2 = self._evaluate_combos(tier2_sweep_combos)
        total_combos += len(outcomes_2)
        # Winner candidate set = tier1 winner + any tier2 sweep that improved IS score
        candidates = [winner_1] + outcomes_2
        winner_2 = self._pick_winner(candidates)
        if winner_2 is winner_1:
            log.info("Tier 2 produced no improvement on IS; reusing tier1 winner OOS evaluation.")
        else:
            if self._evaluate_oos_and_check(winner_2):
                return self._success(2, winner_2, total_combos)

        # ---- TIER 3 ------------------------------------------------------
        log.info("Tier 3: cartesian over tier3_filters (anchored to tier2 winner)")
        base_params = winner_2.combo if winner_2 is not None else winner_1.combo
        tier3_combos = [
            {**base_params, **filt}  # filt may contain preset names; resolved at build time
            for filt in self.grid.tier3_combos()
        ]
        outcomes_3 = self._evaluate_combos(tier3_combos)
        total_combos += len(outcomes_3)
        winner_3 = self._pick_winner([winner_2 or winner_1] + outcomes_3)
        if winner_3 is not None and winner_3 is not (winner_2 or winner_1):
            if self._evaluate_oos_and_check(winner_3):
                return self._success(3, winner_3, total_combos)

        # ---- ALL TIERS FAILED -------------------------------------------
        best = winner_3 or winner_2 or winner_1
        if best is None or best.oos_result is None:
            return RetuneResult(
                passed=False, tier=0, winning_params={},
                oos_result=None, gate_status={},
                n_combos_evaluated=total_combos,
                reason="No tier produced an OOS-evaluable winner",
            )
        gate_status = self.gates.evaluate(best.oos_result, self._oos_days)
        failed = [g for g, ok in gate_status.items() if not ok]
        return RetuneResult(
            passed=False, tier=0,
            winning_params=self.grid.build_config(best.combo),
            oos_result=best.oos_result,
            gate_status=gate_status,
            n_combos_evaluated=total_combos,
            reason=f"All 3 tiers failed gates: {','.join(failed)}",
        )

    # ------------------------------------------------------------------
    def _evaluate_combos(self, combos: List[Dict]) -> List[CombosOutcome]:
        """Run each combo through IS backtest, score, return CombosOutcome list."""
        outcomes: List[CombosOutcome] = []
        for i, combo in enumerate(combos):
            try:
                is_result = self._run_one(combo, self.is_bars)
            except Exception as e:
                log.warning(f"  combo {i} crashed on IS: {e}")
                continue

            # Veto: G2 (worst-day floor) or zero trades on IS
            veto_reasons = []
            if is_result.total_trades == 0:
                veto_reasons.append("zero_trades_IS")
            if is_result.worst_day_r < self.gates.worst_day_r_min:
                veto_reasons.append("G2_worst_day_IS")

            score = is_result.sharpe_ratio if not veto_reasons else -999.0
            outcomes.append(CombosOutcome(
                combo=combo, is_result=is_result,
                is_score=score, is_vetoed=bool(veto_reasons),
                veto_reasons=veto_reasons,
            ))
        return outcomes

    def _pick_winner(self, outcomes: List[CombosOutcome]) -> Optional[CombosOutcome]:
        """Best by IS score (Sharpe with vetos). Returns None if all vetoed/empty."""
        if not outcomes:
            return None
        viable = [o for o in outcomes if not o.is_vetoed]
        if not viable:
            return None
        return max(viable, key=lambda o: o.is_score)

    def _run_one_safe(self, combo: Dict, bars: pd.DataFrame) -> Optional[BacktestResult]:
        try:
            return self._run_one(combo, bars)
        except Exception as e:
            log.warning(f"OOS run crashed: {e}")
            return None

    def _evaluate_oos_and_check(self, outcome: CombosOutcome) -> bool:
        """Run the winner on OOS bars, attach to outcome, return whether all gates pass."""
        if outcome.oos_result is None:
            try:
                outcome.oos_result = self._run_one(outcome.combo, self.oos_bars)
            except Exception as e:
                log.warning(f"OOS run crashed: {e}")
                return False
        return self.gates.all_pass(outcome.oos_result, self._oos_days)

    def _success(self, tier: int, outcome: CombosOutcome, total: int) -> RetuneResult:
        return RetuneResult(
            passed=True, tier=tier,
            winning_params=self.grid.build_config(outcome.combo),
            oos_result=outcome.oos_result,
            gate_status=self.gates.evaluate(outcome.oos_result, self._oos_days),
            n_combos_evaluated=total,
        )

    # ------------------------------------------------------------------
    def _run_one(self, combo: Dict, bars: pd.DataFrame) -> BacktestResult:
        """
        Build full config and run BacktestEngine.

        Layering (later wins):
          1. live config's strategies[<name>] section — source of truth for all
             keys not under grid control (timeframe, session filter, etc.)
          2. grid anchor — explicit baseline for keys the grid tracks
          3. combo (with presets resolved) — the variation under test
          4. enabled=true (force on for backtest)
        """
        cfg = copy.deepcopy(self.full_config)
        live_strat_cfg = dict((cfg.get("strategies") or {}).get(self.grid.strategy) or {})
        live_strat_cfg.update(self.grid.anchor)
        live_strat_cfg.update(self.grid.resolve(combo))
        live_strat_cfg["enabled"] = True
        cfg.setdefault("strategies", {})[self.grid.strategy] = live_strat_cfg

        strategy = self.strategy_class(self.symbol, live_strat_cfg)
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=self.initial_capital,
            risk_config=cfg,
            commission_per_trade=self.commission_per_trade,
            slippage_model=self.slippage_model,
            bypass_risk_limits=True,
        )
        return engine.run(bars)

    @staticmethod
    def _estimate_days(bars: pd.DataFrame) -> int:
        if bars.empty:
            return 1
        idx = bars.index
        try:
            span = (idx.max() - idx.min()).days
            return max(1, int(span))
        except Exception:
            return max(1, len(bars) // 1440)  # rough fallback for M1
