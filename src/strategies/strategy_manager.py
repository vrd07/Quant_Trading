"""
Strategy Manager - Coordinate multiple strategies.

Responsibilities:
- Initialize all configured strategies
- Route bar updates to each strategy
- Collect signals from all strategies
- Handle higher timeframe bar routing for MTF confirmation
- Handle strategy enable/disable
- Track strategy performance
"""

from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime, timezone, timedelta

from .base_strategy import BaseStrategy
from ..core.constants import MarketRegime
from .breakout_strategy import BreakoutStrategy
from .mean_reversion_strategy import MeanReversionStrategy
from .vwap_strategy import VWAPStrategy
from .momentum_strategy import MomentumStrategy
from .kalman_regime_strategy import KalmanRegimeStrategy
from .mini_medallion_strategy import MiniMedallionStrategy
from .structure_break_retest import StructureBreakRetestStrategy
from .supply_demand_strategy import SupplyDemandStrategy
from .asia_range_fade_strategy import AsiaRangeFadeStrategy
from .descending_channel_breakout_strategy import DescendingChannelBreakoutStrategy
from ..core.types import Symbol, Signal


class StrategyManager:
    """Manage multiple trading strategies with MTF support."""

    # Torvalds: registry eliminates special cases — add new strategies here.
    STRATEGY_REGISTRY: Dict[str, type] = {
        'breakout':       BreakoutStrategy,
        'mean_reversion': MeanReversionStrategy,
        'vwap':           VWAPStrategy,
        'momentum':       MomentumStrategy,
        'kalman_regime':  KalmanRegimeStrategy,
        'mini_medallion': MiniMedallionStrategy,
        'sbr':            StructureBreakRetestStrategy,
        'supply_demand':  SupplyDemandStrategy,
        'asia_range_fade': AsiaRangeFadeStrategy,
        'descending_channel_breakout': DescendingChannelBreakoutStrategy,
    }

    def __init__(self, symbols: List[Symbol], config: dict):
        """
        Initialize strategy manager.

        Args:
            symbols: List of symbols to trade
            config: Full configuration dict
        """
        self.symbols = {s.ticker: s for s in symbols}
        self.config = config

        # Initialize strategies for each symbol via registry
        self.strategies: Dict[str, Dict[str, BaseStrategy]] = {}
        strategies_cfg = config.get('strategies', {})

        for symbol in symbols:
            self.strategies[symbol.ticker] = {}
            for name, cls in self.STRATEGY_REGISTRY.items():
                strat_cfg = strategies_cfg.get(name, {})
                if strat_cfg.get('enabled', False):
                    self.strategies[symbol.ticker][name] = cls(
                        symbol=symbol, config=strat_cfg
                    )
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
        
        # Signal cooldown tracking: prevents same strategy firing too often
        # Key: (symbol, strategy_name) -> last signal datetime
        self._last_signal_time: Dict[tuple, datetime] = {}
        self._signal_cooldown_minutes = config.get('strategies', {}).get(
            'signal_cooldown_minutes', 30
        )
    
    def set_higher_tf_bars(
        self,
        symbol: str,
        bars_by_timeframe: Dict[str, pd.DataFrame]
    ) -> None:
        """
        Set higher timeframe bars for MTF confirmation on all strategies.
        
        Args:
            symbol: Symbol ticker
            bars_by_timeframe: Dict mapping timeframe to bars, e.g. {'5m': df, '15m': df}
        """
        if symbol not in self.strategies:
            return
        
        for strategy_name, strategy in self.strategies[symbol].items():
            if hasattr(strategy, 'set_higher_tf_bars'):
                strategy.set_higher_tf_bars(bars_by_timeframe)
    
    def on_bar(
        self,
        symbol: str,
        bars: pd.DataFrame,
        bars_by_timeframe: Optional[Dict[str, pd.DataFrame]] = None
    ) -> List[Signal]:
        """
        Process new bar for a symbol across all strategies.
        
        Args:
            symbol: Symbol ticker
            bars: Historical bars for this symbol (primary timeframe)
            bars_by_timeframe: Optional higher timeframe bars for MTF confirmation
        
        Returns:
            List of signals generated (may be empty)
        """
        if symbol not in self.strategies:
            return []
        
        # Set higher TF bars if provided
        if bars_by_timeframe:
            self.set_higher_tf_bars(symbol, bars_by_timeframe)
        
        signals = []
        
        for strategy_name, strategy in self.strategies[symbol].items():
            try:
                signal = strategy.on_bar(bars)
                
                if signal:
                    # Knuth fix: cooldown keyed by (symbol, strategy) so
                    # one strategy's signal doesn't suppress another's.
                    cooldown_key = (symbol, strategy_name)
                    now = datetime.now(timezone.utc)
                    last_signal = self._last_signal_time.get(cooldown_key)
                    
                    if last_signal and (now - last_signal) < timedelta(minutes=self._signal_cooldown_minutes):
                        remaining = self._signal_cooldown_minutes - (now - last_signal).total_seconds() / 60
                        self.logger.info(
                            f"Signal suppressed (symbol cooldown / reversal buffer)",
                            strategy=strategy_name,
                            symbol=symbol,
                            remaining_min=f"{remaining:.1f}"
                        )
                        continue
                    
                    # Accept signal and update global symbol cooldown
                    self._last_signal_time[cooldown_key] = now
                    signals.append(signal)
                    self.logger.info(
                        f"Signal generated",
                        strategy=strategy_name,
                        symbol=symbol,
                        side=signal.side.value if signal.side else None
                    )
            except Exception as e:
                self.logger.error(
                    f"Strategy error",
                    strategy=strategy_name,
                    symbol=symbol,
                    error=str(e),
                    exc_info=True
                )
        
        return signals
    
    def get_strategy(self, symbol: str, strategy_name: str) -> Optional[BaseStrategy]:
        """Get specific strategy instance."""
        if symbol in self.strategies and strategy_name in self.strategies[symbol]:
            return self.strategies[symbol][strategy_name]
        return None
    
    def enable_strategy(self, symbol: str, strategy_name: str) -> None:
        """Enable a strategy."""
        strategy = self.get_strategy(symbol, strategy_name)
        if strategy:
            strategy.enable()
    
    def disable_strategy(self, symbol: str, strategy_name: str) -> None:
        """Disable a strategy."""
        strategy = self.get_strategy(symbol, strategy_name)
        if strategy:
            strategy.disable()
    
    def set_ml_regime_all(self, symbol: str, regime: Optional[MarketRegime]) -> None:
        """
        Push ML-predicted regime to every strategy for the given symbol.

        Each strategy then uses this instead of its rule-based RegimeFilter
        until the next override is applied (or None is passed to revert).
        """
        if symbol not in self.strategies:
            return
        for strategy in self.strategies[symbol].values():
            strategy.set_ml_regime(regime)

    def get_all_strategies(self) -> Dict[str, Dict[str, BaseStrategy]]:
        """Get all strategies."""
        return self.strategies
