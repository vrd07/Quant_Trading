"""Manual-trade monitor — detects MT5-side manual clicks and enforces guards.

The RiskEngine can't gate trades the user opens directly in MT5 (those orders
never touch our Python stack). This monitor is the post-hoc enforcement layer:
it polls open positions, identifies manual ones (by comment/strategy tag), and
applies the same guards the RiskEngine would have.

Checks, per open manual position:
  1. Stop loss present?        — warn if missing (unprotected trade).
  2. Per-trade risk ≤ cap?     — warn (or close, if auto_close enabled)
                                 when SL hit would cost more than configured
                                 `max_risk_per_trade_usd`.
  3. Opened during blocked hr? — warn (or close) if the UTC hour is in the
                                 `trading_windows.blocked_hours_utc` set.

Violations are emitted as structured log lines (grep-able from trading_system.log)
and, when `manual_guard.auto_close_violations: true`, the monitor will close the
offending position via the MT5 connector.

Legends applied:
  - Carmack: pure `evaluate_position()` returns Violation list; side effects
    (logging, closing) happen in the orchestrator. State mutations visible.
  - TJ:      one class, one responsibility; no framework, no magic.
  - geohot:  simplest thing that works — a loop over positions + a few
    arithmetic checks. No inheritance hierarchy, no plugin system.
  - Jeff Dean: every violation is one structured log line with numeric fields
    so you can grep, aggregate, and alert on them.
"""

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from ..core.types import Position


@dataclass(frozen=True)
class Violation:
    """One rule-violation found on an open manual position. Immutable on purpose."""
    ticket: str
    symbol: str
    rule: str                 # 'no_stop_loss' | 'risk_exceeds_cap' | 'blocked_hour'
    severity: str             # 'warn' | 'critical'
    detail: str
    risk_usd: Optional[Decimal] = None


# Shared with RiskEngine._MANUAL_STRATEGY_TAGS so behavior stays consistent.
# Duplicated literally (4 strings) instead of cross-importing — less coupling.
_MANUAL_TAGS = frozenset({"manual", "manual_gut", "manual_rules", "unknown", "", "none"})


def _is_manual(position: Position) -> bool:
    """Pure: true iff the position's strategy metadata marks it manual."""
    tag = str((position.metadata or {}).get("strategy", "")).strip().lower()
    return tag in _MANUAL_TAGS


def _position_risk_usd(position: Position) -> Optional[Decimal]:
    """Worst-case $ loss if SL hits. None when no SL set."""
    sl = position.stop_loss
    if sl is None or position.entry_price is None:
        return None
    distance = abs(position.entry_price - sl)
    return distance * position.quantity * position.symbol.value_per_lot


def _position_open_hour_utc(position: Position) -> Optional[int]:
    opened = getattr(position, "opened_at", None)
    if opened is None:
        return None
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    return opened.astimezone(timezone.utc).hour


def evaluate_position(
    position: Position,
    max_risk_per_trade_usd: Decimal,
    blocked_hours_utc: Set[int],
) -> List[Violation]:
    """Pure function: list violations for one manual position.

    No I/O, no global state, no logging here. The orchestrator decides what
    to do with the returned list.
    """
    violations: List[Violation] = []
    ticket = str((position.metadata or {}).get("mt5_ticket", position.position_id))
    symbol = position.symbol.ticker

    # Rule 1: Must have a stop loss.
    if position.stop_loss is None:
        violations.append(Violation(
            ticket=ticket, symbol=symbol,
            rule="no_stop_loss", severity="critical",
            detail="Manual position opened without a stop loss — unlimited risk",
        ))

    # Rule 2: Per-trade $ risk must be within the user's configured cap.
    risk = _position_risk_usd(position)
    if risk is not None and max_risk_per_trade_usd > 0 and risk > max_risk_per_trade_usd:
        violations.append(Violation(
            ticket=ticket, symbol=symbol,
            rule="risk_exceeds_cap", severity="warn",
            detail=(f"Worst-case ${risk:.2f} > per-trade cap "
                    f"${max_risk_per_trade_usd:.2f}"),
            risk_usd=risk,
        ))

    # Rule 3: Must not have been opened during a blocked UTC hour.
    hr = _position_open_hour_utc(position)
    if hr is not None and hr in blocked_hours_utc:
        violations.append(Violation(
            ticket=ticket, symbol=symbol,
            rule="blocked_hour", severity="warn",
            detail=f"Opened at {hr:02d}:00 UTC, in blocked window {sorted(blocked_hours_utc)}",
        ))

    return violations


class ManualTradeMonitor:
    """Orchestrator: polls the connector, evaluates each manual position, acts."""

    def __init__(
        self,
        connector,
        config: dict,
        logger=None,
    ):
        self.connector = connector
        self.logger = logger

        risk_cfg = config.get("risk", {}) or {}
        mg_cfg = risk_cfg.get("manual_guard", {}) or {}
        tw_cfg = risk_cfg.get("trading_windows", {}) or {}

        self.enabled: bool = bool(mg_cfg.get("enabled", False))
        self.auto_close: bool = bool(mg_cfg.get("auto_close_violations", False))

        # Per-trade $ cap. Default: account_balance × risk_per_trade_pct.
        default_cap = (
            Decimal(str(config.get("account", {}).get("initial_balance", 0)))
            * Decimal(str(risk_cfg.get("risk_per_trade_pct", 0.001)))
        )
        self.max_risk_per_trade_usd = Decimal(
            str(mg_cfg.get("max_risk_per_trade_usd", default_cap))
        )

        blocked_raw = tw_cfg.get("blocked_hours_utc", []) or []
        self.blocked_hours_utc: Set[int] = {
            int(h) for h in blocked_raw if 0 <= int(h) <= 23
        }

        # De-dupe: don't log the same violation for the same ticket every tick.
        self._reported: Set[tuple] = set()

    def check_once(self) -> List[Violation]:
        """Poll positions once, return all current violations. Public so tests
        and the main loop can drive cadence."""
        if not self.enabled:
            return []
        positions: Dict[str, Position] = self.connector.get_positions()
        findings: List[Violation] = []
        for ticket, pos in positions.items():
            if not _is_manual(pos):
                continue
            for v in evaluate_position(
                pos, self.max_risk_per_trade_usd, self.blocked_hours_utc
            ):
                findings.append(v)
                self._emit(v)
                if self.auto_close:
                    self._close(ticket, v)
        return findings

    # ── side effects ─────────────────────────────────────────────────────

    def _emit(self, v: Violation) -> None:
        key = (v.ticket, v.rule)
        if key in self._reported:
            return
        self._reported.add(key)
        if self.logger is None:
            return
        fields = dict(
            ticket=v.ticket, symbol=v.symbol, rule=v.rule,
            severity=v.severity, detail=v.detail,
            risk_usd=(float(v.risk_usd) if v.risk_usd is not None else None),
        )
        if v.severity == "critical":
            self.logger.critical("Manual trade violation", **fields)
        else:
            self.logger.warning("Manual trade violation", **fields)

    def _close(self, ticket: str, v: Violation) -> None:
        if self.logger is not None:
            self.logger.warning(
                "Auto-closing manual position for rule violation",
                ticket=ticket, rule=v.rule, symbol=v.symbol,
            )
        try:
            self.connector.close_position(ticket)
        except Exception as e:
            if self.logger is not None:
                self.logger.error(
                    "Auto-close failed", ticket=ticket, error=str(e)
                )
