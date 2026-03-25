"""
Risk Processor - Decouples Stop Loss / Take Profit math from strategies.

Following Dennis Ritchie's composability rule: Strategies emit a pure statistical direction
and metadata (indicators), and this processor converts them into hard risk parameters (SL/TP).
"""

from typing import Dict, Any
from decimal import Decimal

from ..core.types import Signal, Symbol
from ..core.constants import OrderSide

class RiskProcessor:
    """
    Computes Stop Loss (SL) and Take Profit (TP) for pure strategy signals.
    """

    def __init__(self, global_config: Dict[str, Any]):
        self.config = global_config
        self.strategies_config = global_config.get('strategies', {})

        from ..monitoring.logger import get_logger
        self.logger = get_logger(__name__)

    def calculate_stops(self, signal: Signal) -> Signal:
        """
        Attaches calculated stop_loss and take_profit to the Signal object inline.
        Returns the mutated Signal.
        """
        strategy_name = signal.metadata.get('strategy', getattr(signal, 'strategy_name', 'unknown'))
        entry = Decimal(str(signal.entry_price))
        side = signal.side

        # Load specific strategy risk configurations
        strat_cfg = self.strategies_config.get(strategy_name, {})
        
        sl = None
        tp = None

        if strategy_name == 'kalman_regime':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            sl_mult = Decimal(str(strat_cfg.get('sl_atr_multiplier', 2.5)))
            tp_mult = Decimal(str(strat_cfg.get('tp_atr_multiplier', 2.0)))
            
            sl_dist = sl_mult * atr
            tp_dist = tp_mult * atr

            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'momentum_scalp':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 2.0)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.0)))

            sl_dist = atr_mult * atr
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
            
            risk = abs(entry - sl)
            tp_dist = risk * rr

            # Dynamic TP based on ML momentum exhaustion prediction
            if strat_cfg.get('ml_dynamic_exhaustion', False):
                predicted_pips = Decimal(str(self.config.get('diagnostics', {}).get('predicted_momentum_pips', 0)))
                if predicted_pips > 0:
                    tp_dist = predicted_pips

            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'vwap_deviation':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            stop_mult = Decimal(str(strat_cfg.get('stop_atr_multiplier', 2.0)))
            vwap = Decimal(str(signal.metadata.get('vwap', entry)))

            sl_dist = stop_mult * atr
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist

            # Take profit is reversion to VWAP
            tp = vwap

        elif strategy_name == 'donchian_breakout':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 2.0)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.0)))
            
            upper_channel = Decimal(str(signal.metadata.get('donchian_upper', entry)))
            lower_channel = Decimal(str(signal.metadata.get('donchian_lower', entry)))

            atr_sl = entry - (atr_mult * atr) if side == OrderSide.BUY else entry + (atr_mult * atr)
            channel_sl = lower_channel if side == OrderSide.BUY else upper_channel

            if side == OrderSide.BUY:
                sl = max(atr_sl, channel_sl)
            else:
                sl = min(atr_sl, channel_sl)

            risk = abs(entry - sl)
            tp_dist = risk * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'zscore_mean_reversion':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            vwap = Decimal(str(signal.metadata.get('vwap', entry)))
            # Hardcoded in original file
            sl_dist = atr * Decimal("2.5")
            
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
            tp = vwap

            # Safety bounds from original logic
            if side == OrderSide.BUY:
                if tp <= entry: tp = entry + (atr * Decimal("0.5"))
                if sl >= entry: sl = entry - sl_dist
            else:
                if tp >= entry: tp = entry - (atr * Decimal("0.5"))
                if sl <= entry: sl = entry + sl_dist

        elif strategy_name == 'mini_medallion':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            risk_mult = Decimal(str(strat_cfg.get('risk_atr_multiplier', 1.0)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 1.5)))

            sl_dist = risk_mult * atr
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist

            tp_dist = sl_dist * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        else:
            # Fallback for unknown strategies (fail-safe ATR stop if available)
            self.logger.warning(f"RiskProcessor: Unknown strategy '{strategy_name}'. Using fallback.")
            atr = Decimal(str(signal.metadata.get('atr', entry * Decimal('0.005'))))
            sl_dist = atr * Decimal('2.0')
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
            tp = entry + (sl_dist * Decimal('2.0')) if side == OrderSide.BUY else entry - (sl_dist * Decimal('2.0'))

        # Carmack Rule: Broker StopsValidator
        if sl is not None:
            min_stop_distance = getattr(signal.symbol, 'min_stops_distance', Decimal('0'))
            actual_dist = abs(entry - sl)
            if min_stop_distance > 0 and actual_dist < min_stop_distance:
                self.logger.warning(
                    f"RiskProcessor [Carmack]: SL distance {actual_dist} < broker min {min_stop_distance}. Expanding SL."
                )
                sl = entry - min_stop_distance if side == OrderSide.BUY else entry + min_stop_distance

        # Standardize TP/SL types to float as Signal model uses Optional[float] / Optional[Decimal]
        # Types.py accepts Decimal
        signal.stop_loss = sl
        signal.take_profit = tp

        self.logger.debug(f"RiskProcessor calculated stops for {strategy_name}: SL={sl}, TP={tp}")
        return signal
