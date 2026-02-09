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

from .base_strategy import BaseStrategy
from .breakout_strategy import BreakoutStrategy
from .mean_reversion_strategy import MeanReversionStrategy
from .vwap_strategy import VWAPStrategy
from .momentum_strategy import MomentumStrategy
from ..core.types import Symbol, Signal


class StrategyManager:
    """Manage multiple trading strategies with MTF support."""
    
    def __init__(self, symbols: List[Symbol], config: dict):
        """
        Initialize strategy manager.
        
        Args:
            symbols: List of symbols to trade
            config: Full configuration dict
        """
        self.symbols = {s.ticker: s for s in symbols}
        self.config = config
        
        # Initialize strategies for each symbol
        self.strategies: Dict[str, Dict[str, BaseStrategy]] = {}
        
        for symbol in symbols:
            self.strategies[symbol.ticker] = {}
            
            # Initialize breakout strategy if enabled
            if config.get('strategies', {}).get('breakout', {}).get('enabled', False):
                self.strategies[symbol.ticker]['breakout'] = BreakoutStrategy(
                    symbol=symbol,
                    config=config.get('strategies', {}).get('breakout', {})
                )
            
            # Initialize mean reversion strategy if enabled
            if config.get('strategies', {}).get('mean_reversion', {}).get('enabled', False):
                self.strategies[symbol.ticker]['mean_reversion'] = MeanReversionStrategy(
                    symbol=symbol,
                    config=config.get('strategies', {}).get('mean_reversion', {})
                )
            
            # Initialize VWAP strategy if enabled
            if config.get('strategies', {}).get('vwap', {}).get('enabled', False):
                self.strategies[symbol.ticker]['vwap'] = VWAPStrategy(
                    symbol=symbol,
                    config=config.get('strategies', {}).get('vwap', {})
                )
            
            # Initialize Momentum strategy if enabled
            if config.get('strategies', {}).get('momentum', {}).get('enabled', False):
                self.strategies[symbol.ticker]['momentum'] = MomentumStrategy(
                    symbol=symbol,
                    config=config.get('strategies', {}).get('momentum', {})
                )
        
        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)
    
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
    
    def get_all_strategies(self) -> Dict[str, Dict[str, BaseStrategy]]:
        """Get all strategies."""
        return self.strategies
