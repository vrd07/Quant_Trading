"""
Walk-forward driver — backtest.md §5.

Outer loop that generates rolling 70/30 IS/OOS windows (monthly roll, ~12.5
months per window, ~57 windows over a 5-year span) and calls TieredRetune
on each window. Concatenates per-window OOS results into a single aggregate
that grades against gates G1..G7 over the **union of OOS slices**, never
on IS — matching backtest.md §5.1.

This is the harness around §7's tiered retune. It also captures the
parameter trajectory across windows for §5.3's stability check.

Not in scope here:
  • Phase 2 ensemble (separate driver — task #5).
  • Report generator and parquet emission (task #6).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Type

import pandas as pd

from .backtest_engine import BacktestResult
from .grid_loader import Grid
from .news_replay import NewsBlackoutReplay
from .tiered_retune import Gates, RetuneResult, TieredRetune
from ..core.types import Symbol
from ..strategies.base_strategy import BaseStrategy

log = logging.getLogger(__name__)

# §5.1 cadence defaults — the spec is the source of truth.
DEFAULT_IS_MONTHS: float = 8.4
DEFAULT_OOS_MONTHS: float = 4.1
DEFAULT_ROLL_MONTHS: float = 1.0
# Conservative average month length so the slicing is stable across years.
DAYS_PER_MONTH: float = 30.4375


@dataclass(frozen=True)
class WindowSpec:
    """A single walk-forward window's date boundaries."""
    idx: int
    is_start: pd.Timestamp
    is_end: pd.Timestamp
    oos_start: pd.Timestamp
    oos_end: pd.Timestamp


@dataclass
class WindowOutcome:
    """Per-window record: spec + retune outcome + the OOS BacktestResult."""
    spec: WindowSpec
    retune: RetuneResult
    oos_result: Optional[BacktestResult]


@dataclass
class WalkForwardDriverResult:
    """Aggregate output across all windows."""
    windows: List[WindowOutcome] = field(default_factory=list)
    gates: Gates = field(default_factory=Gates)

    # ------- §5.1 OOS-only aggregates -------
    @property
    def n_windows(self) -> int:
        return len(self.windows)

    @property
    def n_passed(self) -> int:
        return sum(1 for w in self.windows if w.retune.passed)

    @property
    def oos_profitable_window_pct(self) -> float:
        """G7: fraction of OOS slices that finished net green."""
        scored = [w for w in self.windows if w.oos_result is not None]
        if not scored:
            return 0.0
        green = sum(1 for w in scored if w.oos_result.total_return > 0)
        return green / len(scored)

    @property
    def avg_oos_sharpe(self) -> float:
        vals = [w.oos_result.sharpe_ratio for w in self.windows if w.oos_result is not None]
        return float(statistics.mean(vals)) if vals else 0.0

    @property
    def avg_oos_pf(self) -> float:
        vals = [w.oos_result.profit_factor for w in self.windows if w.oos_result is not None]
        return float(statistics.mean(vals)) if vals else 0.0

    @property
    def avg_oos_daily_win_rate(self) -> float:
        vals = [w.oos_result.daily_win_rate for w in self.windows if w.oos_result is not None]
        return float(statistics.mean(vals)) if vals else 0.0

    @property
    def worst_oos_day_r(self) -> float:
        """G2 union-grade: worst day across every OOS slice's worst day."""
        vals = [w.oos_result.worst_day_r for w in self.windows if w.oos_result is not None]
        return float(min(vals)) if vals else 0.0

    # ------- §5.3 parameter stability -------
    def parameter_stability(self, tolerance_pct: float = 0.20) -> Dict[str, float]:
        """For every numeric grid param, return the fraction of windows whose
        value lies within ±`tolerance_pct` of the median across windows.

        Per §5.3 a stable strategy keeps optimal params within ±20% of their
        median in ≥ 80% of windows.
        """
        # Collect all numeric param values across windows.
        per_key: Dict[str, List[float]] = {}
        for w in self.windows:
            if not w.retune.passed:
                continue
            for k, v in (w.retune.winning_params or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    per_key.setdefault(k, []).append(float(v))

        out: Dict[str, float] = {}
        for k, values in per_key.items():
            if len(values) < 2:
                out[k] = 1.0  # trivially stable
                continue
            median = statistics.median(values)
            if median == 0:
                # zero-median can't be normalized; flag as stable iff all values
                # are also zero, otherwise unstable.
                out[k] = 1.0 if all(v == 0 for v in values) else 0.0
                continue
            band_low = median * (1 - tolerance_pct)
            band_high = median * (1 + tolerance_pct)
            in_band = sum(1 for v in values if band_low <= v <= band_high)
            out[k] = in_band / len(values)
        return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class WalkForwardDriver:
    """Generate rolling 70/30 IS/OOS windows per backtest.md §5.1 and run
    TieredRetune on each.

    Args:
        strategy_class: BaseStrategy subclass under test.
        symbol: Trading symbol.
        bars: Full historical OHLCV with DatetimeIndex (any TF).
        grid: Loaded Grid object for this strategy.
        full_config: Full live-config dict (mirrors live runtime).
        initial_capital: Starting equity per window.
        commission_per_trade: Forwarded to BacktestEngine.
        slippage_model: 'realistic' (legacy) or 'strict' (production gate).
        gates: G1..G7 thresholds (default = backtest.md §1).
        news_replay: Optional NewsBlackoutReplay for §3.4 blackout replay.
        is_months / oos_months / roll_months: §5.1 cadence overrides.
    """

    def __init__(
        self,
        strategy_class: Type[BaseStrategy],
        symbol: Symbol,
        bars: pd.DataFrame,
        grid: Grid,
        full_config: Dict,
        initial_capital: Decimal,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "strict",
        gates: Optional[Gates] = None,
        news_replay: Optional[NewsBlackoutReplay] = None,
        is_months: float = DEFAULT_IS_MONTHS,
        oos_months: float = DEFAULT_OOS_MONTHS,
        roll_months: float = DEFAULT_ROLL_MONTHS,
    ):
        if not isinstance(bars.index, pd.DatetimeIndex):
            raise ValueError("WalkForwardDriver requires bars with a DatetimeIndex")
        self.strategy_class = strategy_class
        self.symbol = symbol
        self.bars = bars.sort_index()
        self.grid = grid
        self.full_config = full_config
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_model = slippage_model
        self.gates = gates or Gates()
        self.news_replay = news_replay
        self.is_months = is_months
        self.oos_months = oos_months
        self.roll_months = roll_months

    # ------------------------------------------------------------------
    def generate_windows(self) -> List[WindowSpec]:
        """Compute the list of (IS, OOS) date boundaries, monthly-rolling."""
        if self.bars.empty:
            return []

        start = self.bars.index.min()
        end = self.bars.index.max()
        is_delta = timedelta(days=self.is_months * DAYS_PER_MONTH)
        oos_delta = timedelta(days=self.oos_months * DAYS_PER_MONTH)
        roll_delta = timedelta(days=self.roll_months * DAYS_PER_MONTH)

        windows: List[WindowSpec] = []
        cursor = start
        idx = 0
        while cursor + is_delta + oos_delta <= end + timedelta(days=1):
            is_start = cursor
            is_end = cursor + is_delta
            oos_start = is_end
            oos_end = oos_start + oos_delta
            windows.append(WindowSpec(
                idx=idx,
                is_start=is_start, is_end=is_end,
                oos_start=oos_start, oos_end=oos_end,
            ))
            cursor = cursor + roll_delta
            idx += 1
        return windows

    # ------------------------------------------------------------------
    def run(
        self,
        max_windows: Optional[int] = None,
        progress_every: int = 5,
    ) -> WalkForwardDriverResult:
        """Run TieredRetune for each window and collect results.

        Args:
            max_windows: Cap window count (smoke-testing). None = full sweep.
            progress_every: Print one line every N windows.
        """
        specs = self.generate_windows()
        if max_windows is not None:
            specs = specs[:max_windows]
        if not specs:
            log.warning("No walk-forward windows fit the data range.")
            return WalkForwardDriverResult(gates=self.gates)

        log.info(
            f"WalkForward: {len(specs)} windows  "
            f"(IS={self.is_months}mo, OOS={self.oos_months}mo, roll={self.roll_months}mo)"
        )

        result = WalkForwardDriverResult(gates=self.gates)
        for spec in specs:
            is_slice = self.bars.loc[spec.is_start:spec.is_end]
            oos_slice = self.bars.loc[spec.oos_start:spec.oos_end]
            if len(is_slice) < 200 or len(oos_slice) < 50:
                # Skip windows with too little data — usually only at the very
                # tail when an OOS slice is partly past end_date.
                continue

            retune = TieredRetune(
                strategy_class=self.strategy_class,
                symbol=self.symbol,
                is_bars=is_slice,
                oos_bars=oos_slice,
                grid=self.grid,
                full_config=self.full_config,
                initial_capital=self.initial_capital,
                commission_per_trade=self.commission_per_trade,
                slippage_model=self.slippage_model,
                gates=self.gates,
            )
            try:
                retune_result = retune.run()
            except Exception as e:
                log.warning(f"Window {spec.idx} crashed: {e}")
                continue

            result.windows.append(WindowOutcome(
                spec=spec,
                retune=retune_result,
                oos_result=retune_result.oos_result,
            ))

            if (spec.idx + 1) % progress_every == 0:
                pct_pass = result.n_passed / result.n_windows
                log.info(
                    f"  [{spec.idx+1}/{len(specs)}] "
                    f"pass-rate={pct_pass:.0%} "
                    f"avg-oos-PF={result.avg_oos_pf:.2f} "
                    f"worst-day-R={result.worst_oos_day_r:.2f}"
                )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def print_report(result: WalkForwardDriverResult) -> None:
        """Print a one-screen summary against G1..G7."""
        if not result.windows:
            print("  WalkForward: no windows produced results.")
            return

        oos_green_pct = result.oos_profitable_window_pct
        avg_dwr = result.avg_oos_daily_win_rate
        avg_pf = result.avg_oos_pf
        avg_sharpe = result.avg_oos_sharpe
        worst_r = result.worst_oos_day_r

        gates = result.gates
        line = lambda gate, val, ok: f"  [{'PASS' if ok else 'FAIL'}] {gate:<24} {val}"

        print("\n" + "=" * 72)
        print("WALK-FORWARD AGGREGATE — backtest.md §5.1 OOS union")
        print("=" * 72)
        print(f"  Windows run            : {result.n_windows}")
        print(f"  TieredRetune passed    : {result.n_passed}/{result.n_windows} "
              f"({result.n_passed / max(1, result.n_windows):.0%})")
        print()
        print(line("G1 daily win-rate",   f"{avg_dwr:>5.0%}",        avg_dwr   >= gates.daily_win_rate_min))
        print(line("G2 worst-day R",      f"{worst_r:>5.2f}R",       worst_r   >= gates.worst_day_r_min))
        print(line("G3 profit factor",    f"{avg_pf:>5.2f}",         avg_pf    >= gates.profit_factor_min))
        print(line("G4 Sharpe (OOS avg)", f"{avg_sharpe:>5.2f}",     avg_sharpe>= gates.sharpe_min))
        print(line("G7 OOS green %",      f"{oos_green_pct:>5.0%}",  oos_green_pct >= 0.80))
        print("=" * 72)

        # §5.3 parameter stability snapshot — only meaningful when ≥3 windows passed.
        stability = result.parameter_stability()
        if stability and result.n_passed >= 3:
            print("\nParameter stability (within ±20% of median, ≥80% required):")
            for k, frac in sorted(stability.items()):
                ok = frac >= 0.80
                print(f"  [{'OK' if ok else 'WARN'}] {k:<32} {frac:>5.0%}")
            print()
