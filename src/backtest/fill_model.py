"""
Strict fill model — backtest.md §3.

Pure functions over (symbol, side, signal_price, bar OHLC, hour, news_active)
returning a fill price that incorporates spread + 1.5× empirical slippage +
queue penalty for stop orders.

The aim is to make backtests pessimistic about execution so that strategies
which survive earn their `enabled: true` slot honestly.

Spread + slippage tables are §3.2 fallback defaults — we have no per-bar
spread series and the live trade journal has <100 fills today. When that
data exists, swap in load_empirical_slippage() / load_spread_series().

Units: every distance is in price units of the symbol (i.e. raw quote-price
deltas, not pips, not dollars). Conversion to dollars happens only via
Symbol.value_per_lot in the broker layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from ..core.constants import OrderSide, PositionSide
from ..core.types import Symbol


# ---------------------------------------------------------------------------
# §3.2 fallback empirical slippage — used when logs/trade_journal.csv has
# fewer than 100 fills. All values in price units.
# ---------------------------------------------------------------------------
DEFAULT_SLIPPAGE_PRICE_UNITS: Dict[str, Decimal] = {
    "XAUUSD": Decimal("0.005"),  # 0.5 pip × 0.01 pip_value = 0.005 in price
    "BTCUSD": Decimal("5.0"),    # $5 raw — BTC quote is in USD, value_per_lot=1
    "EURUSD": Decimal("0.00003"),  # 0.3 pip × 0.0001 pip_value
}

# 1.5× safety margin, §3.2: "applies 1.5× the measured average".
SLIPPAGE_SAFETY_MULT: Decimal = Decimal("1.5")


# ---------------------------------------------------------------------------
# §3.1 spread fallback — broker-typical median spread per symbol, in price
# units. Multiplied by hour-of-day curve below to capture session liquidity.
# ---------------------------------------------------------------------------
DEFAULT_BASE_SPREAD_PRICE_UNITS: Dict[str, Decimal] = {
    "XAUUSD": Decimal("0.30"),   # 30 cents — typical Goat Funded XAU
    "BTCUSD": Decimal("15.0"),   # $15 — typical retail BTC spread
    "EURUSD": Decimal("0.0001"),  # 1.0 pip
}

# Hour-of-day liquidity multiplier (UTC hours 0..23).
# Tightest during London/NY overlap (12:00–16:00 UTC), widest during the
# Asia–Sydney handover (~22:00 UTC) and very early Asia (00:00–02:00 UTC).
# These are not measured per-symbol — they are the same coarse curve we
# would get from a 90-day sample if we had spread data, and they are
# explicitly a placeholder until §11.4 (live spread bootstrap) lands.
_HOUR_SPREAD_MULT: Dict[int, Decimal] = {
    0: Decimal("1.4"), 1: Decimal("1.5"), 2: Decimal("1.4"), 3: Decimal("1.2"),
    4: Decimal("1.1"), 5: Decimal("1.0"), 6: Decimal("0.95"), 7: Decimal("0.9"),
    8: Decimal("0.85"), 9: Decimal("0.85"), 10: Decimal("0.85"), 11: Decimal("0.8"),
    12: Decimal("0.75"), 13: Decimal("0.75"), 14: Decimal("0.75"), 15: Decimal("0.8"),
    16: Decimal("0.85"), 17: Decimal("0.95"), 18: Decimal("1.05"), 19: Decimal("1.1"),
    20: Decimal("1.2"), 21: Decimal("1.6"), 22: Decimal("1.8"), 23: Decimal("1.6"),
}

# §3.1: news multiplier on spread inside blackout window.
NEWS_SPREAD_MULT: Decimal = Decimal("3.0")


@dataclass(frozen=True)
class FillContext:
    """Per-bar fill inputs — kept explicit so the call site shows what
    drives the fill, not hidden state."""
    bar_open: Decimal
    bar_high: Decimal
    bar_low: Decimal
    bar_close: Decimal
    hour_utc: int
    news_active: bool = False  # wired by news-blackout replay (#2)


class StrictFillModel:
    """Backtest.md §3 strict fill model.

    Stateless: given a symbol, an order side, a target price, and a bar
    context, return the realistic fill price. Stop / limit fills are
    handled by separate methods because their queue semantics differ.
    """

    def __init__(
        self,
        slippage_overrides: Optional[Dict[str, Decimal]] = None,
        base_spread_overrides: Optional[Dict[str, Decimal]] = None,
    ):
        # Allow overrides for backtests that have measured live data, but
        # never silently hide the source — the call site decides.
        self._slippage = dict(DEFAULT_SLIPPAGE_PRICE_UNITS)
        if slippage_overrides:
            self._slippage.update(slippage_overrides)
        self._base_spread = dict(DEFAULT_BASE_SPREAD_PRICE_UNITS)
        if base_spread_overrides:
            self._base_spread.update(base_spread_overrides)

    # ---- spread / slippage lookups ----

    def _slippage_for(self, symbol: Symbol) -> Decimal:
        """Per-side slippage in price units (already 1.5×)."""
        raw = self._slippage.get(symbol.ticker, Decimal("0"))
        return raw * SLIPPAGE_SAFETY_MULT

    def _spread_for(self, symbol: Symbol, hour_utc: int, news_active: bool) -> Decimal:
        """Total spread (bid→ask) in price units for this bar."""
        base = self._base_spread.get(symbol.ticker, Decimal("0"))
        hour_mult = _HOUR_SPREAD_MULT.get(hour_utc, Decimal("1.0"))
        spread = base * hour_mult
        if news_active:
            spread *= NEWS_SPREAD_MULT
        return spread

    # ---- §3.3 market entry ----

    def market_fill(
        self,
        symbol: Symbol,
        side: OrderSide,
        signal_price: Decimal,
        ctx: FillContext,
    ) -> Decimal:
        """Market order fill — cross half the spread + 1.5× slippage."""
        half_spread = self._spread_for(symbol, ctx.hour_utc, ctx.news_active) / Decimal("2")
        slip = self._slippage_for(symbol)
        side_sign = Decimal("1") if side == OrderSide.BUY else Decimal("-1")
        return signal_price + side_sign * half_spread + side_sign * slip

    # ---- §3.3 stop fill (SL on existing position) ----

    def stop_fill(
        self,
        symbol: Symbol,
        position_side: PositionSide,
        stop_price: Decimal,
        ctx: FillContext,
    ) -> Decimal:
        """SL fill — worse of (stop ± slippage) or bar extremum.

        For a LONG position the SL is a sell-stop: when triggered we sell,
        worse fill = lower price. We take min(stop - slippage, bar_low).
        For a SHORT position the SL is a buy-stop: worse fill = higher price.
        We take max(stop + slippage, bar_high).

        Note: backtest.md §3.3 has the bar-extremum sides written as
        "long SL = bar's high" — that is a typo (a long stop punishes us
        on the low). The economics here drive the implementation; the spec
        intent is "use the gappier of the two".
        """
        slip = self._slippage_for(symbol)
        if position_side == PositionSide.LONG:
            # Sell-stop: pick the lower (worse) of slipped stop or bar low.
            return min(stop_price - slip, ctx.bar_low)
        else:
            # Buy-stop covering a short: pick the higher of slipped stop or bar high.
            return max(stop_price + slip, ctx.bar_high)

    # ---- §3.3 limit fill (TP on existing position) ----

    def limit_fill(
        self,
        symbol: Symbol,
        position_side: PositionSide,
        limit_price: Decimal,
        ctx: FillContext,
    ) -> Optional[Decimal]:
        """TP fill — only if bar reaches the limit; no positive slippage.

        Returns None if the bar did not reach the limit price. Returns the
        exact limit price if it did (assume we are last in queue, so we
        get filled at the level, never inside it).
        """
        if position_side == PositionSide.LONG:
            # Sell-limit above entry: hit if bar_high >= limit.
            if ctx.bar_high >= limit_price:
                return limit_price
            return None
        else:
            # Buy-limit below entry: hit if bar_low <= limit.
            if ctx.bar_low <= limit_price:
                return limit_price
            return None
