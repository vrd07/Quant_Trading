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

    # Maps internal strategy names to their config YAML keys
    _CONFIG_KEY_MAP = {
        'donchian_breakout': 'breakout',
        'momentum_scalp': 'momentum',
        'zscore_mean_reversion': 'mean_reversion',
        'vwap_deviation': 'vwap',
        'vwap_macd_crossover': 'vwap',   # new VWAP+MACD strategy — config lives under strategies.vwap
        'kalman_regime': 'kalman_regime',
        'mini_medallion': 'mini_medallion',
        'structure_break_retest': 'sbr',
        'fibonacci_retracement': 'fibonacci_retracement',
    }

    def calculate_stops(self, signal: Signal) -> Signal:
        """
        Attaches calculated stop_loss and take_profit to the Signal object inline.
        Returns the mutated Signal.
        """
        strategy_name = signal.metadata.get('strategy', getattr(signal, 'strategy_name', 'unknown'))
        entry = Decimal(str(signal.entry_price))
        side = signal.side

        # Load specific strategy risk configurations using the correct YAML key
        config_key = self._CONFIG_KEY_MAP.get(strategy_name, strategy_name)
        strat_cfg = self.strategies_config.get(config_key, {})
        
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

            # Take profit is reversion to VWAP — but enforce minimum 1.0×ATR distance
            # so we never have near-zero TP when VWAP is very close to entry
            tp = vwap
            min_tp_dist = atr * Decimal("1.0")
            if side == OrderSide.BUY and (tp - entry) < min_tp_dist:
                tp = entry + min_tp_dist
            elif side == OrderSide.SELL and (entry - tp) < min_tp_dist:
                tp = entry - min_tp_dist

        elif strategy_name == 'vwap_macd_crossover':
            # The strategy pre-computes stop_price and take_profit_price at signal time
            # (1.5×ATR SL, 2×SL TP = fixed 1:2 RR). Use them directly; fall back to
            # ATR-based calculation only when metadata keys are absent.
            precomputed_sl = signal.metadata.get('stop_price')
            precomputed_tp = signal.metadata.get('take_profit_price')
            if precomputed_sl is not None and precomputed_tp is not None:
                sl = Decimal(str(precomputed_sl))
                tp = Decimal(str(precomputed_tp))
            else:
                # Fallback: reconstruct from ATR and config
                atr = Decimal(str(signal.metadata.get('atr', 0)))
                stop_mult = Decimal(str(strat_cfg.get('stop_atr_mult', 1.5)))
                rr = Decimal(str(signal.metadata.get('risk_reward', 2.0)))
                sl_dist = stop_mult * atr
                sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
                tp = entry + (sl_dist * rr) if side == OrderSide.BUY else entry - (sl_dist * rr)

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
            # Ensure TP is on the correct side with at least 1.0× ATR (min 1:0.4 RR vs 2.5 SL)
            if side == OrderSide.BUY:
                if tp <= entry: tp = entry + (atr * Decimal("1.0"))
                if sl >= entry: sl = entry - sl_dist
            else:
                if tp >= entry: tp = entry - (atr * Decimal("1.0"))
                if sl <= entry: sl = entry + sl_dist

        elif strategy_name == 'mini_medallion':
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            risk_mult = Decimal(str(strat_cfg.get('risk_atr_multiplier', 1.0)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 1.5)))

            sl_dist = risk_mult * atr
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist

            tp_dist = sl_dist * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'descending_channel_breakout':
            # DCB: for bullish breakout, channel_lower is the recent HL anchor;
            # for bearish rejection, channel_upper is the rejected resistance.
            # Anchor SL beyond the relevant boundary with an ATR buffer, then
            # clip to a max ATR distance to keep R:R workable on small ATR bars.
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            channel_upper = Decimal(str(signal.metadata.get('channel_upper', entry)))
            channel_lower = Decimal(str(signal.metadata.get('channel_lower', entry)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 1.5)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.0)))

            buffer = atr_mult * atr
            if side == OrderSide.BUY:
                structure_sl = channel_lower - buffer
                atr_sl = entry - buffer
                sl = min(structure_sl, atr_sl)  # Farther stop wins (more room)
                max_dist = atr * Decimal('3.0')
                if (entry - sl) > max_dist:
                    sl = entry - max_dist
            else:
                structure_sl = channel_upper + buffer
                atr_sl = entry + buffer
                sl = max(structure_sl, atr_sl)
                max_dist = atr * Decimal('3.0')
                if (sl - entry) > max_dist:
                    sl = entry + max_dist

            risk = abs(entry - sl)
            tp_dist = risk * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'structure_break_retest':
            # SBR uses the broken level as a natural SL anchor.
            # SL sits beyond the broken level by atr_stop_multiplier × ATR.
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            broken_level = Decimal(str(signal.metadata.get('broken_level', entry)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 1.5)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.5)))

            # SL beyond the broken level (invalidation of the retest thesis)
            sl_buffer = atr_mult * atr
            if side == OrderSide.BUY:
                sl = broken_level - sl_buffer
            else:
                sl = broken_level + sl_buffer

            risk = abs(entry - sl)
            tp_dist = risk * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'fibonacci_retracement':
            # Fib retracement: SL beyond the swing low (BUY) or swing high (SELL).
            # The swing point is the structural invalidation level — if price
            # moves past the swing, the pullback thesis is dead.
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            swing_high = Decimal(str(signal.metadata.get('swing_high', entry)))
            swing_low = Decimal(str(signal.metadata.get('swing_low', entry)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 1.5)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.5)))

            sl_buffer = atr_mult * atr
            if side == OrderSide.BUY:
                # Bullish: SL below swing low (invalidation of upswing)
                sl = swing_low - sl_buffer
            else:
                # Bearish: SL above swing high (invalidation of downswing)
                sl = swing_high + sl_buffer

            risk = abs(entry - sl)
            tp_dist = risk * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        elif strategy_name == 'continuation_breakout':
            # Continuation breakout (Wyckoff stair-step): the re-accumulation
            # cluster is the structural anchor. If price re-enters the cluster
            # against our direction, the continuation thesis is invalidated.
            # SL sits just past the opposite cluster boundary by atr_mult × ATR.
            atr = Decimal(str(signal.metadata.get('atr', 0)))
            cluster_high = Decimal(str(signal.metadata.get('cluster_high', entry)))
            cluster_low = Decimal(str(signal.metadata.get('cluster_low', entry)))
            atr_mult = Decimal(str(strat_cfg.get('atr_stop_multiplier', 1.0)))
            rr = Decimal(str(strat_cfg.get('rr_ratio', 2.0)))

            sl_buffer = atr_mult * atr
            if side == OrderSide.BUY:
                structure_sl = cluster_low - sl_buffer
                atr_sl = entry - (Decimal('2.0') * atr)
                sl = min(structure_sl, atr_sl)
                max_dist = atr * Decimal('3.0')
                if (entry - sl) > max_dist:
                    sl = entry - max_dist
            else:
                structure_sl = cluster_high + sl_buffer
                atr_sl = entry + (Decimal('2.0') * atr)
                sl = max(structure_sl, atr_sl)
                max_dist = atr * Decimal('3.0')
                if (sl - entry) > max_dist:
                    sl = entry + max_dist

            risk = abs(entry - sl)
            tp_dist = risk * rr
            tp = entry + tp_dist if side == OrderSide.BUY else entry - tp_dist

        else:
            # Fallback for unknown strategies (fail-safe ATR stop if available)
            self.logger.warning(f"RiskProcessor: Unknown strategy '{strategy_name}'. Using fallback.")
            atr = Decimal(str(signal.metadata.get('atr', entry * Decimal('0.005'))))
            sl_dist = atr * Decimal('2.0')
            sl = entry - sl_dist if side == OrderSide.BUY else entry + sl_dist
            tp = entry + (sl_dist * Decimal('2.0')) if side == OrderSide.BUY else entry - (sl_dist * Decimal('2.0'))

        # ── Carmack Rule: Liquidity-anchored SL/TP adjustment ─────────────
        # Inline here (NOT extracted into a method) so the mutation is visible
        # right next to the other SL/TP assignments below. The actual logic
        # lives in a PURE function (adjust_stops_for_liquidity) that enforces
        # two invariants: never widens TP, never tightens SL. Worst case the
        # trade behaves like the original strategy plan.
        if sl is not None and tp is not None:
            liq_levels = signal.metadata.get('liquidity_levels') or {}
            if liq_levels:
                from ..monitoring.liquidity_levels import adjust_stops_for_liquidity
                atr = Decimal(str(signal.metadata.get('atr', 0)))
                # Buffer = 0.1 × ATR. Small enough that we sit just past the
                # stop-run wick but not so wide that R:R collapses.
                liq_buffer = atr * Decimal('0.1') if atr > 0 else Decimal('0')
                side_str = "BUY" if side == OrderSide.BUY else "SELL"
                sl, tp, reasons = adjust_stops_for_liquidity(
                    entry=entry, sl=sl, tp=tp, side=side_str,
                    levels=liq_levels, buffer=liq_buffer,
                )
                for r in reasons:
                    self.logger.info(f"RiskProcessor [Carmack/liquidity]: {r}")

        # Carmack Rule: Broker StopsValidator
        # Validate BOTH SL and TP against broker minimum stops distance.
        # Add a 5% buffer to avoid edge-case rejections where MT5 requires
        # strictly greater than (not equal to) the minimum distance.
        if sl is not None and tp is not None:
            min_stop_distance = getattr(signal.symbol, 'min_stops_distance', Decimal('0'))
            if min_stop_distance > 0:
                buffered_min = min_stop_distance * Decimal('1.05')

                # Expand SL if too close to entry
                sl_dist = abs(entry - sl)
                if sl_dist < buffered_min:
                    self.logger.warning(
                        f"RiskProcessor [Carmack]: SL distance {sl_dist:.3f} < broker min "
                        f"{min_stop_distance} (buffered={buffered_min:.3f}). Expanding SL."
                    )
                    sl = entry - buffered_min if side == OrderSide.BUY else entry + buffered_min

                # Expand TP if too close to entry
                tp_dist = abs(entry - tp)
                if tp_dist < buffered_min:
                    self.logger.warning(
                        f"RiskProcessor [Carmack]: TP distance {tp_dist:.3f} < broker min "
                        f"{min_stop_distance} (buffered={buffered_min:.3f}). Expanding TP."
                    )
                    tp = entry + buffered_min if side == OrderSide.BUY else entry - buffered_min

        # ── Carmack Rule: Tiered R:R floor ────────────────────────────────
        # Replaces the previous hard 1.0 floor. Higher-confidence signals
        # require a wider reward-to-risk; lower-confidence signals are
        # allowed to take a 1:1. The tier is derived from `signal.strength`
        # (the 0–1 confidence every strategy already sets) and is fully
        # overridable in YAML via:
        #
        #   risk:
        #     rr_floors:
        #       confirmed:                       1.0   # strength >= confirmed_strength_threshold
        #       default:                         0.75  # in between
        #       unconfirmed:                     0.5   # strength < unconfirmed_strength_threshold
        #       confirmed_strength_threshold:    0.7
        #       unconfirmed_strength_threshold:  0.4
        #
        # Rejection (set SL/TP to None) is the explicit action when the
        # floor is breached — matches the prior fail-closed pattern.
        if sl is not None and tp is not None:
            rr_cfg = (self.config.get('risk', {}) or {}).get('rr_floors', {}) or {}
            floor_confirmed   = Decimal(str(rr_cfg.get('confirmed',   1.0)))
            floor_default     = Decimal(str(rr_cfg.get('default',     0.75)))
            floor_unconfirmed = Decimal(str(rr_cfg.get('unconfirmed', 0.5)))
            th_confirmed   = float(rr_cfg.get('confirmed_strength_threshold',   0.7))
            th_unconfirmed = float(rr_cfg.get('unconfirmed_strength_threshold', 0.4))

            strength = float(signal.strength) if signal.strength is not None else 0.5
            if strength >= th_confirmed:
                tier, rr_floor = "CONFIRMED", floor_confirmed
            elif strength < th_unconfirmed:
                tier, rr_floor = "UNCONFIRMED", floor_unconfirmed
            else:
                tier, rr_floor = "DEFAULT", floor_default

            final_risk = abs(entry - sl)
            final_reward = abs(entry - tp)
            if final_risk > 0 and (final_reward / final_risk) < rr_floor:
                self.logger.warning(
                    f"RiskProcessor [Carmack/RR]: {strategy_name} strength={strength:.2f} "
                    f"tier={tier}, R:R {float(final_reward/final_risk):.2f} < floor "
                    f"{float(rr_floor):.1f} — rejecting signal."
                )
                signal.stop_loss = None
                signal.take_profit = None
                return signal

        # Standardize TP/SL types to float as Signal model uses Optional[float] / Optional[Decimal]
        # Types.py accepts Decimal
        signal.stop_loss = sl
        signal.take_profit = tp

        self.logger.debug(f"RiskProcessor calculated stops for {strategy_name}: SL={sl}, TP={tp}")
        return signal

