"""Unit tests for PositionSizer — focus on the margin-aware notional ceiling.

Regression guard for the crypto sizing trap: a flat `max_lot: 0.01` is not
price-aware, so risk-based sizing on ETHUSD (~$2.3k) gets clamped to ~$0.16 of
risk while the config asks for ~$15-50. `max_notional_pct` raises the ceiling to
a margin budget so the sizer can actually reach the intended risk.
"""

from decimal import Decimal

from src.core.types import Symbol
from src.risk.position_sizer import PositionSizer


def _sizer(risk_pct: float = 0.01) -> PositionSizer:
    return PositionSizer({"risk": {"risk_per_trade_pct": str(risk_pct)}})


def _eth(max_lot="3.0", max_notional_pct="0") -> Symbol:
    return Symbol(
        ticker="ETHUSD",
        min_lot=Decimal("0.01"),
        max_lot=Decimal(max_lot),
        lot_step=Decimal("0.01"),
        value_per_lot=Decimal("1"),
        leverage=Decimal("5"),
        max_notional_pct=Decimal(max_notional_pct),
    )


def test_flat_max_lot_clamps_risk_to_pennies():
    """Without a notional budget, 0.01 max_lot crushes ETH risk sizing."""
    sizer = _sizer()
    sym = _eth(max_lot="0.01", max_notional_pct="0")
    # $5k * 1% = $50 risk, stop $16 away -> wants ~3.1 lots, clamped to 0.01.
    size = sizer.calculate_position_size(
        symbol=sym,
        account_balance=Decimal("5000"),
        entry_price=Decimal("2300"),
        stop_loss=Decimal("2284"),
    )
    assert size == Decimal("0.01")


def test_notional_budget_lets_sizing_reach_target_risk():
    """max_notional_pct + a sane max_lot let sizing reach the intended risk."""
    sizer = _sizer()
    sym = _eth(max_lot="3.0", max_notional_pct="1.0")  # $5k notional budget
    size = sizer.calculate_position_size(
        symbol=sym,
        account_balance=Decimal("5000"),
        entry_price=Decimal("2300"),
        stop_loss=Decimal("2284"),
    )
    # Risk wants 3.1 lots; notional ceiling = 5000/2300 = 2.17 lots -> binds.
    assert size == Decimal("2.17")
    # Margin used = 2.17 * 2300 / 5 = ~$998 — safely inside the account.
    assert size * Decimal("2300") / Decimal("5") < Decimal("5000")


def test_notional_ceiling_is_a_ceiling_not_a_target():
    """When risk sizing lands below the notional ceiling, it is used as-is.

    risk_pct 0.0002 -> $1 risk / $16 stop = 0.0625 -> 0.06 lot, well under the
    2.17-lot ceiling. The notional budget must NOT inflate it up to the ceiling.
    (Legacy flat max_lot 0.01 would instead have clamped this down to 0.01 —
    the budget correctly lets the genuine risk-based size through.)
    """
    sizer = _sizer(risk_pct=0.0002)
    sym = _eth(max_lot="3.0", max_notional_pct="1.0")
    size = sizer.calculate_position_size(
        symbol=sym,
        account_balance=Decimal("5000"),
        entry_price=Decimal("2300"),
        stop_loss=Decimal("2284"),
    )
    assert size == Decimal("0.06")


def test_zero_notional_pct_is_legacy_behaviour():
    """Default (0) must reproduce the old flat-max_lot clamp exactly."""
    sizer = _sizer()
    sym = _eth(max_lot="0.05", max_notional_pct="0")
    size = sizer.calculate_position_size(
        symbol=sym,
        account_balance=Decimal("5000"),
        entry_price=Decimal("2300"),
        stop_loss=Decimal("2284"),
    )
    assert size == Decimal("0.05")
