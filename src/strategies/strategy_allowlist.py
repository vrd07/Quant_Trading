"""
StrategyAllowlist — the safety net extracted from ConfluenceGate.

ConfluenceGate (deleted 2026-08-13) carried two separable jobs: combo
assembly (COMBO A/B/C, ``combo_sniper``, the exhaustion modifier) and a
default-deny allowlist. Every input to the combo half was deleted along with
it — the six confluence-only legs never once produced a combo, across a
window widened 25 -> 120 minutes.

This module keeps only the second job:

  * ``SOLO_ALLOWED`` — strategies permitted to reach the risk engine.
  * ``KILL_LIST``    — names of deleted strategies, so a stale config cannot
                       resurrect one. Configs are hot-swapped between eight
                       files day by day; this is the net under that.

Anything not explicitly allowlisted is dropped. Default deny is the point.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from ..core.types import Signal

# Strategies permitted to execute. A strategy absent from this set cannot
# trade no matter what any config says.
SOLO_ALLOWED: frozenset = frozenset({
    "kalman_regime",
    "squeeze_breakout",
    "stoch_pullback",
    "bos_structure",
    "london_breakout",
    "monday_drift",
    "index_overnight",
    "wednesday_drift",
})

# Deleted strategies. Names are retained so a stale config referencing one is
# dropped loudly rather than crashing the registry lookup.
KILL_LIST: frozenset = frozenset({
    # deleted 2026-06-10 — no backtested edge
    "breakout",
    "mean_reversion",
    "supply_demand",
    "descending_channel_breakout",
    "mini_medallion",
    "continuation_breakout",
    # deleted 2026-08-13 — confluence-only legs whose combos never fired,
    # plus two already-disabled strategies with published refutations
    "vwap",
    "momentum",
    "sbr",
    "asia_range_fade",
    "smc_ob",
    "fibonacci_retracement",
    "wavelet_cycle",
    "ema200_nasdaq",
})


class StrategyAllowlist:
    """Default-deny filter over raw strategy signals."""

    def __init__(self, config: Optional[dict] = None) -> None:
        # `config` is accepted and ignored. Call sites still hand over the old
        # gate config block; there is deliberately no enable flag, because a
        # switch that turns a safety net off is not a safety net.
        from ..monitoring.logger import get_logger
        self._logger = get_logger(__name__)

    def filter(
        self,
        symbol: str,
        signals: Iterable[Tuple[str, Signal]],
    ) -> List[Signal]:
        """Return the executable signals, in input order.

        Args:
            symbol: ticker the signals were generated for (logging only).
            signals: ``(strategy_name, Signal)`` pairs emitted this tick.

        Returns:
            Signals whose strategy is in ``SOLO_ALLOWED``. May be empty.
        """
        out: List[Signal] = []
        for name, sig in signals:
            if name in KILL_LIST:
                self._logger.info(
                    f"[StrategyAllowlist] kill-list drop: {name} on {symbol}"
                )
                continue
            if name not in SOLO_ALLOWED:
                self._logger.info(
                    f"[StrategyAllowlist] not allowlisted, dropped: {name} on {symbol}"
                )
                continue
            out.append(sig)
        return out
