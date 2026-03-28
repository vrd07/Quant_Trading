"""
Base Strategy - Abstract base class for all trading strategies.

All strategies must inherit from this and implement required methods.

Strategy Lifecycle:
1. on_bar() called for each new bar close
2. Strategy calculates indicators
3. Strategy generates signal (or None)
4. Signal passed to risk engine
5. If approved, order placed

Design Principles:
- Strategies are stateless (don't store positions)
- Strategies generate signals, don't place orders
- Same code works in backtest and live
- All decisions must be explainable (via metadata)
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from datetime import datetime
import pandas as pd

from ..core.types import Bar, Signal, Symbol
from ..core.constants import MarketRegime, OrderSide
from ..data.indicators import Indicators


def _parse_ml_regime(regime_str: Optional[str]) -> Optional[MarketRegime]:
    """Convert ML override regime string to MarketRegime, or None if unrecognised."""
    if not regime_str:
        return None
    try:
        return MarketRegime[regime_str.upper()]
    except KeyError:
        return None


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    
    Subclasses must implement:
    - on_bar()
    - get_name()
    """
    
    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        """
        Initialize strategy.
        
        Args:
            symbol: Symbol to trade
            config: Strategy configuration
        """
        self.symbol = symbol
        self.config = config
        self.enabled = config.get('enabled', True)

        # ML regime override: set by _apply_regime_override() in main.py.
        # When not None, strategies use this instead of rule-based regime detection.
        self.ml_regime: Optional[MarketRegime] = None

        # Logging
        from ..monitoring.logger import get_logger
        self.logger = get_logger(f"strategy.{self.get_name()}")
    
    @abstractmethod
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        """
        Process new bar and generate signal if conditions met.
        
        Args:
            bars: Historical bars DataFrame (OHLCV)
                  Most recent bar is last row
        
        Returns:
            Signal object if trade signal generated, None otherwise
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """
        Get strategy name.
        
        Returns:
            Strategy identifier (e.g., "donchian_breakout")
        """
        pass
    
    def is_enabled(self) -> bool:
        """Check if strategy is enabled."""
        return self.enabled
    
    def enable(self) -> None:
        """Enable strategy."""
        self.enabled = True
        self.logger.info(f"{self.get_name()} enabled")
    
    def disable(self) -> None:
        """Disable strategy."""
        self.enabled = False
        self.logger.info(f"{self.get_name()} disabled")

    def set_ml_regime(self, regime: Optional[MarketRegime]) -> None:
        """
        Inject the ML-predicted market regime.

        When set, strategies bypass their rule-based RegimeFilter and use this
        value directly.  Pass None to revert to rule-based detection.
        """
        self.ml_regime = regime
    
    def _create_signal(
        self,
        side: OrderSide,
        strength: float,
        regime: MarketRegime,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Signal:
        """
        Helper to create signal with standard fields.
        
        Args:
            side: BUY or SELL
            strength: Signal strength 0.0-1.0
            regime: Market regime (TREND/RANGE)
            entry_price: Suggested entry price
            stop_loss: Suggested stop loss
            take_profit: Suggested take profit
            metadata: Additional signal context
        
        Returns:
            Signal object
        """
        from decimal import Decimal
        
        signal = Signal(
            strategy_name=self.get_name(),
            symbol=self.symbol,
            side=side,
            strength=strength,
            regime=regime,
            entry_price=Decimal(str(entry_price)) if entry_price else None,
            stop_loss=Decimal(str(stop_loss)) if stop_loss else None,
            take_profit=Decimal(str(take_profit)) if take_profit else None,
            metadata=metadata or {}
        )
        
        self.logger.info(
            f"Signal generated",
            side=side.value,
            strength=strength,
            regime=regime.value,
            entry=entry_price,
            sl=stop_loss,
            tp=take_profit
        )
        
        return signal
    
    def _log_no_signal(self, reason: str) -> None:
        """Log why no signal was generated (INFO so it's visible in normal logs)."""
        import re
        if not hasattr(self, '_last_no_signal_reason'):
            self._last_no_signal_reason = None

        # Strip numeric values to deduplicate reasons that differ only in indicator values
        # e.g. "No BB squeeze: recent_avg=0.0072 not tight vs prior_avg=0.0064"
        #  and "No BB squeeze: recent_avg=0.0081 not tight vs prior_avg=0.0061"
        # are treated as the same reason type to avoid per-bar log spam
        reason_key = re.sub(r'[-+]?\d+\.?\d*', '#', reason)

        if reason_key != self._last_no_signal_reason:
            self.logger.info(f"No signal: {reason}")
            self._last_no_signal_reason = reason_key
        else:
            self.logger.debug(f"No signal: {reason}")
