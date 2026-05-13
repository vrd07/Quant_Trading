"""
ConfluenceGate — combo-based filter that gates raw strategy signals before
execution.

Policy (from ``combine_startegy.md`` / project memory 2026-05-13):

  COMBO A — Trend Surge (TREND regime):
    Primary: ``sbr``
    Confluence required: ``fibonacci_retracement`` + ``momentum`` (same side)
    If both fired in window, the primary's entry is overridden to fib's level
    when available ("Fibonacci gives the level").

  COMBO B — Range Reversion (RANGE regime):
    Primary: ``vwap``
    Confluence required: ``asia_range_fade`` + ``smc_ob`` (same side)
    SMC OB's entry overrides VWAP's when available ("SMC pinpoints entry").

  COMBO C — Structural Sniper (any regime, rare, 1.5× sized):
    All three must align in window: ``smc_ob`` + ``fibonacci_retracement`` +
    ``momentum``. Emits a synthesized ``combo_sniper`` signal carrying a
    ``lot_size_multiplier`` of 1.5 inside ``metadata``.

Allowlist:
    ``kalman_regime`` continues to fire solo (unchanged behaviour).

Filter-only (never trade alone — only act as confluence votes):
    ``momentum``, ``asia_range_fade``, ``smc_ob``, ``fibonacci_retracement``

Kill list (dropped on sight, even if a config still has them enabled):
    ``breakout``, ``mean_reversion``, ``supply_demand``,
    ``descending_channel_breakout``, ``mini_medallion``,
    ``continuation_breakout``

Design notes (Carmack + TJ):
  * One responsibility: filter a list of ``(name, Signal)`` tuples down to a
    list of executable ``Signal``s. No strategy code changes anywhere.
  * State is explicit: a per-symbol time-windowed deque of
    ``(timestamp, strategy_name, side, entry_price)`` tuples.
  * Sniper cooldown prevents the same triple alignment from firing multiple
    sniper trades on consecutive ticks.
  * ``enabled=False`` short-circuits to passthrough but still drops the kill
    list — a safety net even when the gate is off.
"""

from __future__ import annotations

from collections import deque
from copy import copy
from datetime import datetime, timezone, timedelta
from typing import Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal


SOLO_ALLOWED: frozenset = frozenset({"kalman_regime"})
FILTER_ONLY: frozenset = frozenset({
    "momentum",
    "asia_range_fade",
    "smc_ob",
    "fibonacci_retracement",
})
KILL_LIST: frozenset = frozenset({
    "breakout",
    "mean_reversion",
    "supply_demand",
    "descending_channel_breakout",
    "mini_medallion",
    "continuation_breakout",
})

# Combo definitions: primary strategy → required confluence list + regime gate.
COMBO_A = {
    "id": "A",
    "primary": "sbr",
    "confluence": ("fibonacci_retracement", "momentum"),
    "regime": MarketRegime.TREND,
    "entry_source": "fibonacci_retracement",
}
COMBO_B = {
    "id": "B",
    "primary": "vwap",
    "confluence": ("asia_range_fade", "smc_ob"),
    "regime": MarketRegime.RANGE,
    "entry_source": "smc_ob",
}
COMBO_C_LEGS = ("smc_ob", "fibonacci_retracement", "momentum")


class ConfluenceGate:
    """Combo-based confluence filter."""

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.enabled: bool = bool(config.get("enabled", True))
        # Window expressed in minutes — works across heterogeneous strategy
        # timeframes (1m / 5m / 15m). Default ≈ 5 × 5m bars.
        self.window_minutes: float = float(config.get("window_minutes", 25.0))
        self.sniper_lot_multiplier: float = float(config.get("sniper_lot_multiplier", 1.5))
        self.sniper_cooldown_minutes: float = float(config.get("sniper_cooldown_minutes", 60.0))
        # Per-symbol deque of (timestamp, name, side, entry_price). Bounded
        # to avoid unbounded growth; 64 events is far more than any window
        # could hold given typical bar cadence.
        self._history: Dict[str, deque] = {}
        self._last_sniper: Dict[str, datetime] = {}

        from ..monitoring.logger import get_logger
        self._logger = get_logger(__name__)

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def filter(
        self,
        symbol: str,
        signals: Iterable[Tuple[str, Signal]],
        regime: Optional[MarketRegime] = None,
        now: Optional[datetime] = None,
    ) -> List[Signal]:
        """Filter raw signals down to combo-compliant executable signals.

        Args:
            symbol: ticker (matches Signal.symbol.ticker).
            signals: iterable of ``(strategy_name, Signal)`` tuples emitted
                this tick.
            regime: current market regime for the symbol. ``UNKNOWN`` is
                treated as "no regime info" — only sniper (any regime) and
                solo allowlist (kalman) can fire.
            now: override clock for testing.

        Returns:
            List of executable ``Signal`` objects. May be empty.
        """
        signals = list(signals)
        now = now or datetime.now(timezone.utc)

        # Always drop the kill list, even when the gate is disabled. This is
        # the only safety net if someone left a killed strategy enabled in a
        # config by mistake.
        signals = [(n, s) for n, s in signals if n not in KILL_LIST]
        for name in (n for n, _ in list(signals) if n in KILL_LIST):
            self._logger.info(f"[ConfluenceGate] kill-list drop: {name} on {symbol}")

        if not self.enabled:
            # Passthrough mode: just kill-list filter. Used as instant rollback.
            return [s for _, s in signals]

        # Record everything (incl. filter-only votes) into the per-symbol
        # window BEFORE evaluating combos, so signals firing on the same tick
        # count as confluence for one another.
        self._record(symbol, signals, now)

        output: List[Signal] = []
        consumed_ids: set = set()  # python id() of source Signal objects already emitted

        # 1. Solo allowlist — pass through unchanged.
        for name, sig in signals:
            if name in SOLO_ALLOWED:
                output.append(sig)
                consumed_ids.add(id(sig))

        # 2. Combo A (TREND regime only).
        if regime == MarketRegime.TREND:
            for name, sig in signals:
                if name == COMBO_A["primary"] and id(sig) not in consumed_ids and sig.side:
                    if self._has_confluence(symbol, sig.side, COMBO_A["confluence"], now):
                        self._apply_combo_metadata(sig, COMBO_A)
                        fib_entry = self._latest_entry_price(
                            symbol, COMBO_A["entry_source"], sig.side, now
                        )
                        if fib_entry is not None:
                            sig.entry_price = fib_entry
                            sig.metadata["entry_source"] = COMBO_A["entry_source"]
                        output.append(sig)
                        consumed_ids.add(id(sig))
                        self._logger.info(
                            f"[ConfluenceGate] COMBO A pass",
                            symbol=symbol, side=sig.side.value,
                        )

        # 3. Combo B (RANGE regime only).
        if regime == MarketRegime.RANGE:
            for name, sig in signals:
                if name == COMBO_B["primary"] and id(sig) not in consumed_ids and sig.side:
                    if self._has_confluence(symbol, sig.side, COMBO_B["confluence"], now):
                        self._apply_combo_metadata(sig, COMBO_B)
                        smc_entry = self._latest_entry_price(
                            symbol, COMBO_B["entry_source"], sig.side, now
                        )
                        if smc_entry is not None:
                            sig.entry_price = smc_entry
                            sig.metadata["entry_source"] = COMBO_B["entry_source"]
                        output.append(sig)
                        consumed_ids.add(id(sig))
                        self._logger.info(
                            f"[ConfluenceGate] COMBO B pass",
                            symbol=symbol, side=sig.side.value,
                        )

        # 4. Combo C (any regime, sniper).
        sniper = self._try_sniper(symbol, signals, now)
        if sniper is not None:
            output.append(sniper)
            self._last_sniper[symbol] = now
            self._logger.info(
                f"[ConfluenceGate] COMBO C SNIPER fire",
                symbol=symbol, side=sniper.side.value,
                lot_multiplier=self.sniper_lot_multiplier,
            )

        # 5. Anything left over (filter-only votes, unmatched primaries) is
        # silently suppressed — that's the whole point of the gate.
        for name, sig in signals:
            if id(sig) not in consumed_ids and sig not in output:
                self._logger.info(
                    f"[ConfluenceGate] suppress",
                    symbol=symbol, strategy=name,
                    reason=("filter_only" if name in FILTER_ONLY else "no_combo_match"),
                )

        return output

    # ──────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────

    def _record(
        self,
        symbol: str,
        signals: List[Tuple[str, Signal]],
        now: datetime,
    ) -> None:
        bucket = self._history.setdefault(symbol, deque(maxlen=64))
        for name, sig in signals:
            if sig.side is None:
                continue
            bucket.append((now, name, sig.side, sig.entry_price))
        self._evict(symbol, now)

    def _evict(self, symbol: str, now: datetime) -> None:
        bucket = self._history.get(symbol)
        if not bucket:
            return
        cutoff = now - timedelta(minutes=self.window_minutes)
        while bucket and bucket[0][0] < cutoff:
            bucket.popleft()

    def _strategy_fired(
        self,
        symbol: str,
        strategy_name: str,
        side: OrderSide,
        now: datetime,
    ) -> bool:
        """True if ``strategy_name`` emitted a ``side`` signal in window."""
        self._evict(symbol, now)
        bucket = self._history.get(symbol) or ()
        return any(name == strategy_name and s_side == side for _, name, s_side, _ in bucket)

    def _has_confluence(
        self,
        symbol: str,
        side: OrderSide,
        required: Iterable[str],
        now: datetime,
    ) -> bool:
        return all(self._strategy_fired(symbol, name, side, now) for name in required)

    def _latest_entry_price(
        self,
        symbol: str,
        strategy_name: str,
        side: OrderSide,
        now: datetime,
    ) -> Optional[object]:
        """Most recent matching entry price recorded in window, if any."""
        self._evict(symbol, now)
        bucket = self._history.get(symbol) or ()
        for ts, name, s_side, entry in reversed(bucket):
            if name == strategy_name and s_side == side and entry is not None:
                return entry
        return None

    def _try_sniper(
        self,
        symbol: str,
        signals: List[Tuple[str, Signal]],
        now: datetime,
    ) -> Optional[Signal]:
        """Emit a sniper signal if SMC + Fib + Momentum align on either side."""
        last = self._last_sniper.get(symbol)
        if last and (now - last) < timedelta(minutes=self.sniper_cooldown_minutes):
            return None

        for side in (OrderSide.BUY, OrderSide.SELL):
            if all(self._strategy_fired(symbol, leg, side, now) for leg in COMBO_C_LEGS):
                return self._build_sniper_signal(symbol, side, signals, now)
        return None

    def _build_sniper_signal(
        self,
        symbol: str,
        side: OrderSide,
        signals: List[Tuple[str, Signal]],
        now: datetime,
    ) -> Optional[Signal]:
        """Synthesize a sniper Signal from the first matching leg seen today."""
        # Prefer a fib-priced entry, fall back to SMC, then momentum.
        leg_priority = ("fibonacci_retracement", "smc_ob", "momentum")
        template: Optional[Signal] = None
        for preferred in leg_priority:
            for name, sig in signals:
                if name == preferred and sig.side == side:
                    template = sig
                    break
            if template is not None:
                break
        if template is None:
            # No live leg this tick — pull from history.
            bucket = self._history.get(symbol) or ()
            for ts, name, s_side, entry in reversed(bucket):
                if name in COMBO_C_LEGS and s_side == side:
                    # Build a minimal Signal scaffold from history.
                    snip = Signal(
                        signal_id=uuid4(),
                        strategy_name="combo_sniper",
                        symbol=None,  # filled by caller context if needed
                        side=side,
                        strength=0.9,
                        timestamp=now,
                        regime=MarketRegime.UNKNOWN,
                        entry_price=entry,
                    )
                    snip.metadata = {
                        "combo": "C",
                        "confluence": list(COMBO_C_LEGS),
                        "lot_size_multiplier": self.sniper_lot_multiplier,
                        "synthesized": True,
                    }
                    return snip
            return None

        # Clone the template so we don't mutate a sibling strategy's Signal.
        snip = copy(template)
        snip.signal_id = uuid4()
        snip.strategy_name = "combo_sniper"
        snip.timestamp = now
        snip.metadata = dict(template.metadata or {})
        snip.metadata["combo"] = "C"
        snip.metadata["confluence"] = list(COMBO_C_LEGS)
        existing_mult = float(snip.metadata.get("lot_size_multiplier", 1.0) or 1.0)
        snip.metadata["lot_size_multiplier"] = existing_mult * self.sniper_lot_multiplier
        snip.metadata["entry_source"] = "sniper:" + template.strategy_name
        return snip

    @staticmethod
    def _apply_combo_metadata(sig: Signal, combo: dict) -> None:
        sig.metadata.setdefault("combo", combo["id"])
        existing = list(sig.metadata.get("confluence", []) or [])
        for leg in combo["confluence"]:
            if leg not in existing:
                existing.append(leg)
        sig.metadata["confluence"] = existing

    # ──────────────────────────────────────────────────────────────────
    # Diagnostics
    # ──────────────────────────────────────────────────────────────────

    def get_window_snapshot(self, symbol: str) -> List[Tuple[datetime, str, str]]:
        """Return current window contents for debug/dashboard use."""
        bucket = self._history.get(symbol) or ()
        return [(ts, name, side.value) for ts, name, side, _ in bucket]
