"""
Phase 2 ensemble backtest — backtest.md §6.

Drives the live StrategyManager + RiskEngine + SimulatedBroker pipeline
through historical bars so the backtest measures the same thing production
will execute. This is the production gate (per backtest.md §6: "the run
that gates live deployment").

What this matches in production:
  • StrategyManager — same registry, same strategy code, same enable flags
  • RiskEngine.calculate_position_size — same sizing math
  • RiskProcessor.calculate_stops — same SL/TP logic
  • SimulatedBroker — strict fills + news blackout from #1 and #2

Known gaps (v1; documented for follow-up):
  • TF-aware resampling: strategies that declare timeframe != base TF receive
    base-TF bars. Live runtime uses DataEngine.get_bars(symbol, tf) which
    builds higher TF bars from ticks. Resampler hook is in place but
    pre-compute path is a TODO.
  • Per-regime breakdown (G8): regime classifier loop not yet replayed in
    backtest. The result object exposes per-strategy attribution that
    can be sliced by regime once the override-file replay lands.
  • Multi-symbol: pass one symbol at a time; loop externally.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd

from .backtest_engine import BacktestResult
from .metrics import PerformanceMetrics
from .news_replay import NewsBlackoutReplay
from .simulation import SimulatedBroker
from ..core.constants import OrderSide, OrderStatus
from ..core.types import Order, Symbol
from ..risk.risk_engine import RiskEngine
from ..risk.risk_processor import RiskProcessor
from ..strategies.strategy_manager import StrategyManager

log = logging.getLogger(__name__)


@dataclass
class StrategyAttribution:
    """Per-strategy P&L and trade-count breakdown in an ensemble run."""
    strategy: str
    trades: int = 0
    gross_pnl: float = 0.0
    wins: int = 0
    losses: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


@dataclass
class EnsembleResult:
    """Phase 2 ensemble backtest output. Wraps a `BacktestResult` for the
    aggregate ensemble plus per-strategy breakdowns for ablation analysis."""
    aggregate: BacktestResult
    per_strategy: Dict[str, StrategyAttribution] = field(default_factory=dict)


class EnsembleBacktestEngine:
    """
    Phase 2 ensemble engine — drives StrategyManager.on_bar() through history.

    Single-symbol; loop externally for multi-symbol grading per §2.
    """

    def __init__(
        self,
        symbol: Symbol,
        full_config: Dict,
        initial_capital: Decimal,
        commission_per_trade: Decimal = Decimal("0"),
        slippage_model: str = "strict",
        news_replay: Optional[NewsBlackoutReplay] = None,
        bypass_risk_limits: bool = True,
    ):
        self.symbol = symbol
        # Override signal cooldown to zero — StrategyManager uses wall-clock
        # time for cooldown which is incompatible with replay (every bar's
        # `now` is ~the same). The cooldown is also a live-only protection
        # against duplicate same-bar emissions; backtest already enforces
        # one-call-per-bar via the loop.
        cfg = copy.deepcopy(full_config)
        cfg.setdefault('strategies', {})
        cfg['strategies']['signal_cooldown_minutes'] = 0
        self.full_config = cfg
        self.initial_capital = initial_capital
        self.commission_per_trade = commission_per_trade
        self.slippage_model = slippage_model
        self.news_replay = news_replay
        self.bypass_risk_limits = bypass_risk_limits

        # Live components — same code as production.
        self.strategy_manager = StrategyManager([symbol], cfg)
        self.risk_engine = RiskEngine(cfg)
        self.risk_engine.equity_high_water_mark = initial_capital
        self.risk_engine.daily_start_equity = initial_capital
        self.risk_processor = RiskProcessor(cfg)

        news_active_at = (news_replay.is_active_at_bar
                          if news_replay is not None else None)
        self.broker = SimulatedBroker(
            initial_capital=initial_capital,
            commission_per_trade=commission_per_trade,
            slippage_model=slippage_model,
            trailing_stop_config=cfg.get('risk', {}).get('trailing_stop', {}),
            news_active_at=news_active_at,
        )

        self.metrics = PerformanceMetrics()
        self.attribution: Dict[str, StrategyAttribution] = defaultdict(
            lambda: StrategyAttribution(strategy="?")
        )

        # Track which bar each strategy last processed so we don't double-fire
        # within the same bar (mirrors live `_last_processed_bars`).
        self._last_processed: Dict[str, datetime] = {}

    # ------------------------------------------------------------------
    def run(
        self,
        bars: pd.DataFrame,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        min_history: int = 50,
        max_window: int = 1000,
    ) -> EnsembleResult:
        """Replay ensemble pipeline over a single-symbol bar series."""
        bars = self._normalize_bars(bars, start_date, end_date)
        log.info(f"Ensemble backtest: {len(bars)} bars  "
                 f"({bars.index.min()} → {bars.index.max()})")

        self.broker.reset()
        self.metrics.reset()
        self._last_processed.clear()
        self.attribution.clear()

        # Detect bar interval so trailing-stop time stops compute correctly.
        if len(bars) >= 2:
            delta = (bars.index[1] - bars.index[0]).total_seconds() / 60.0
            self.broker._bar_interval_minutes = max(delta, 1.0)

        current_day = None
        for i in range(len(bars)):
            window_start = max(0, i + 1 - max_window)
            available = bars.iloc[window_start:i + 1]
            if len(available) < min_history:
                continue

            current_bar = available.iloc[-1]
            bar_date = pd.to_datetime(current_bar.name).date()
            if current_day is None:
                current_day = bar_date
            elif bar_date != current_day:
                self.broker.reset_daily()
                self.risk_engine.reset_daily_metrics(self.broker.get_equity())
                if not self.bypass_risk_limits:
                    self.risk_engine.kill_switch.reset()
                    self.risk_engine.circuit_breaker.reset()
                current_day = bar_date

            self._step(available)

        # Close any open positions at final price (matches single-strat engine).
        self._close_all(bars.iloc[-1])

        return self._build_result(bars)

    # ------------------------------------------------------------------
    def _step(self, available: pd.DataFrame) -> None:
        """One bar through the ensemble pipeline."""
        current_bar = available.iloc[-1]

        # Update existing positions / check exits first.
        self.broker.update_positions(current_bar)
        self.broker.check_exits(current_bar)

        # News blackout: drop new signals (open positions stay open).
        if self.news_replay is not None and self.news_replay.is_active_at_bar(current_bar):
            self.metrics.update_equity(
                timestamp=current_bar.name,
                equity=float(self.broker.get_equity()),
            )
            return

        # Iterate every enabled strategy. Each strategy emits at most one
        # signal per call. Same-bar dedupe via _last_processed.
        signals: List[tuple] = []
        for strategy_name, strategy in self.strategy_manager.strategies.get(
            self.symbol.ticker, {}
        ).items():
            bar_key = f"{self.symbol.ticker}_{strategy_name}"
            if self._last_processed.get(bar_key) == current_bar.name:
                continue
            self._last_processed[bar_key] = current_bar.name

            try:
                signal = strategy.on_bar(available)
            except Exception as e:
                log.debug(f"strategy {strategy_name} raised: {e}")
                continue
            if signal is not None:
                signals.append((strategy_name, signal))

        # Execute every signal — same as live (RiskEngine gates which actually open).
        for strategy_name, signal in signals:
            self._execute(signal, strategy_name, current_bar)

        self.metrics.update_equity(
            timestamp=current_bar.name,
            equity=float(self.broker.get_equity()),
        )

    # ------------------------------------------------------------------
    def _execute(self, signal, strategy_name: str, current_bar) -> None:
        """Calculate stops, size, validate, fill — mirrors backtest_engine._process_bar."""
        # Stops via risk_processor (strategies emit signals without SL).
        if signal.entry_price and not signal.stop_loss:
            try:
                signal = self.risk_processor.calculate_stops(signal)
            except Exception as e:
                log.debug(f"calculate_stops failed: {e}")
                return
        if not signal.entry_price or not signal.stop_loss:
            return

        position_size = self.risk_engine.calculate_position_size(
            symbol=signal.symbol,
            account_balance=self.broker.get_balance(),
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            side=signal.side,
        )
        if position_size <= 0:
            return

        order = Order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=position_size,
            price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            status=OrderStatus.PENDING,
            metadata={
                'signal_id': str(signal.signal_id),
                'strategy': strategy_name,
                'regime': signal.regime.value if signal.regime else 'unknown',
                'strength': signal.strength,
            },
        )

        if not self.bypass_risk_limits:
            try:
                ok, reason = self.risk_engine.validate_order(
                    order=order,
                    account_balance=self.broker.get_balance(),
                    account_equity=self.broker.get_equity(),
                    current_positions={
                        str(p.position_id): p for p in self.broker.get_positions()
                    },
                    daily_pnl=self.broker.get_daily_pnl(),
                )
                if not ok:
                    log.debug(f"risk veto: {reason}")
                    return
            except Exception as e:
                log.debug(f"risk_engine raised: {e}")
                return

        fill_price = self.broker.execute_order(order=order, current_bar=current_bar)
        if fill_price is None:
            return

        # Per-strategy attribution: count opens here. Closing P&L is rolled up
        # in _build_result via the broker's closed_trades list. r_dollars is
        # the per-trade R per spec §1 (|entry-stop| × volume × value_per_lot).
        vpl = signal.symbol.value_per_lot if signal.symbol else Decimal("1")
        r_dollars = float(abs(fill_price - signal.stop_loss) * position_size * vpl)
        self.metrics.add_trade({
            'trade_idx': len(self.metrics.trades),
            'timestamp': str(current_bar.name),
            'symbol': signal.symbol.ticker,
            'side': signal.side.value,
            'entry_price': float(fill_price),
            'quantity': float(position_size),
            'stop_loss': float(signal.stop_loss) if signal.stop_loss else None,
            'take_profit': float(signal.take_profit) if signal.take_profit else None,
            'strategy': strategy_name,
            'strength': signal.strength,
            'r_dollars': r_dollars,
            'pnl': 0,
        })
        self.risk_engine.update_equity_hwm(self.broker.get_equity())

    # ------------------------------------------------------------------
    def _close_all(self, final_bar) -> None:
        """Force-close any remaining positions at the final bar's close."""
        from ..core.constants import PositionSide
        final_price = Decimal(str(final_bar['close']))
        for pos_id in list(self.broker.positions.keys()):
            position = self.broker.positions[pos_id]
            if position.side == PositionSide.LONG:
                pnl = (final_price - position.entry_price) * position.quantity * position.symbol.value_per_lot
            else:
                pnl = (position.entry_price - final_price) * position.quantity * position.symbol.value_per_lot
            commission = self.broker.commission_per_trade
            if position.symbol and position.symbol.commission_per_lot > 0:
                commission += position.symbol.commission_per_lot * position.quantity * Decimal("2")
            self.broker.closed_trades.append({
                'position_id': str(pos_id),
                'symbol': position.symbol.ticker,
                'side': position.side.value,
                'entry_price': float(position.entry_price),
                'exit_price': float(final_price),
                'quantity': float(position.quantity),
                'pnl': float(pnl),
                'commission': float(commission),
                'net_pnl': float(pnl - commission),
                'exit_reason': 'backtest_end',
                'strategy': position.metadata.get('strategy', 'unknown'),
            })
            self.broker.balance += pnl - commission
            del self.broker.positions[pos_id]

    # ------------------------------------------------------------------
    def _normalize_bars(self, bars, start_date, end_date) -> pd.DataFrame:
        bars = bars.copy()
        if 'timestamp' in bars.columns:
            bars['timestamp'] = pd.to_datetime(bars['timestamp'])
            bars = bars.set_index('timestamp')
        elif not isinstance(bars.index, pd.DatetimeIndex):
            bars.index = pd.to_datetime(bars.index)

        idx_tz = getattr(bars.index, 'tz', None)
        def coerce(d):
            ts = pd.to_datetime(d)
            if idx_tz is not None and ts.tzinfo is None:
                ts = ts.tz_localize(idx_tz)
            elif idx_tz is None and ts.tzinfo is not None:
                ts = ts.tz_convert(None)
            return ts
        if start_date:
            bars = bars[bars.index >= coerce(start_date)]
        if end_date:
            bars = bars[bars.index <= coerce(end_date)]
        return bars

    # ------------------------------------------------------------------
    def _build_result(self, bars: pd.DataFrame) -> EnsembleResult:
        """Compute aggregate metrics + per-strategy attribution."""
        closed = self.broker.get_closed_trades()
        # Patch entry trades' exit data so PerformanceMetrics sees real P&L.
        for trade in closed:
            net = trade.get('net_pnl', trade.get('pnl', 0))
            for entry in self.metrics.trades:
                if (entry.get('symbol') == trade.get('symbol')
                        and abs(entry.get('entry_price', 0) - trade.get('entry_price', 0)) < 0.01
                        and entry.get('strategy') == trade.get('strategy')
                        and entry.get('pnl', 0) == 0):
                    entry['pnl'] = net
                    entry['exit_price'] = trade.get('exit_price')
                    entry['exit_reason'] = trade.get('exit_reason')
                    break

        # Aggregate via the existing metrics layer (same as backtest_engine).
        equity_curve = self.metrics.get_equity_curve()
        initial = float(self.initial_capital)
        final = float(self.broker.get_equity())
        if len(equity_curve) > 1:
            returns = equity_curve.pct_change().dropna()
            sharpe = self.metrics.calculate_sharpe_ratio(returns)
            sortino = self.metrics.calculate_sortino_ratio(returns)
            max_dd, max_dd_pct = self.metrics.calculate_max_drawdown(equity_curve)
            daily_returns = equity_curve.resample('D').last().pct_change().dropna()
        else:
            sharpe = sortino = 0.0
            max_dd = max_dd_pct = 0.0
            daily_returns = pd.Series(dtype=float)

        trades = self.metrics.get_trades()
        wins = [t for t in trades if t.get('pnl', 0) > 0]
        losses = [t for t in trades if t.get('pnl', 0) < 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
        gross_p = sum(t['pnl'] for t in wins)
        gross_l = abs(sum(t['pnl'] for t in losses))
        pf = gross_p / gross_l if gross_l > 0 else 0
        expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
        largest_win = max((t['pnl'] for t in wins), default=0)
        largest_loss = min((t['pnl'] for t in losses), default=0)

        # Daily metrics (G1, G2). R = risk_per_trade_pct × initial (same fallback as backtest_engine).
        risk_pct = float(self.full_config.get('risk', self.full_config).get(
            'risk_per_trade_pct', 0.01) if isinstance(self.full_config, dict) else 0.01)
        r_dollars = float(self.initial_capital) * risk_pct
        daily_wr = PerformanceMetrics.calculate_daily_win_rate(trades)
        worst_r = PerformanceMetrics.calculate_worst_day_r(trades, r_dollars)
        trading_days = PerformanceMetrics.calculate_trading_days(trades)

        agg = BacktestResult(
            total_return=float(final - initial),
            total_return_pct=float((final - initial) / initial * 100),
            sharpe_ratio=float(sharpe),
            sortino_ratio=float(sortino),
            max_drawdown=float(max_dd),
            max_drawdown_pct=float(max_dd_pct),
            win_rate=float(win_rate),
            profit_factor=float(pf),
            expectancy=float(expectancy),
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_win=float(avg_win),
            avg_loss=float(avg_loss),
            largest_win=float(largest_win),
            largest_loss=float(largest_loss),
            equity_curve=equity_curve,
            trades=trades,
            daily_returns=daily_returns,
            daily_win_rate=float(daily_wr),
            worst_day_r=float(worst_r),
            trading_days=int(trading_days),
        )

        # Per-strategy attribution.
        by_strat: Dict[str, StrategyAttribution] = {}
        for trade in trades:
            name = trade.get('strategy', 'unknown')
            attr = by_strat.setdefault(name, StrategyAttribution(strategy=name))
            attr.trades += 1
            pnl = trade.get('pnl', 0)
            attr.gross_pnl += pnl
            if pnl > 0:
                attr.wins += 1
            elif pnl < 0:
                attr.losses += 1

        return EnsembleResult(aggregate=agg, per_strategy=by_strat)


def print_ensemble_report(result: EnsembleResult) -> None:
    """Stdout summary: aggregate gates + per-strategy attribution."""
    agg = result.aggregate
    print("\n" + "=" * 72)
    print("ENSEMBLE BACKTEST — Phase 2 (production gate)")
    print("=" * 72)
    print(f"  Total Return     : ${agg.total_return:,.2f}  ({agg.total_return_pct:+.2f}%)")
    print(f"  Sharpe / Sortino : {agg.sharpe_ratio:.2f} / {agg.sortino_ratio:.2f}")
    print(f"  Profit Factor    : {agg.profit_factor:.2f}")
    print(f"  Daily Win-Rate   : {agg.daily_win_rate:.0%}   "
          f"(G1 ≥70%) — {'PASS' if agg.daily_win_rate >= 0.70 else 'FAIL'}")
    print(f"  Worst Day        : {agg.worst_day_r:.2f}R     "
          f"(G2 ≥-2.0R) — {'PASS' if agg.worst_day_r >= -2.0 else 'FAIL'}")
    print(f"  Max Drawdown     : {agg.max_drawdown_pct:.2f}%")
    print(f"  Trades / Wins    : {agg.total_trades} / {agg.winning_trades} "
          f"({agg.win_rate:.1f}%)")
    print("\n  Per-strategy attribution:")
    print(f"  {'Strategy':<28} {'Trades':>7} {'Wins':>6} {'WinRate':>8} {'P&L':>10}")
    print("  " + "-" * 64)
    for name, attr in sorted(result.per_strategy.items(),
                             key=lambda kv: kv[1].gross_pnl, reverse=True):
        print(f"  {name:<28} {attr.trades:>7} {attr.wins:>6} "
              f"{attr.win_rate:>7.1%} ${attr.gross_pnl:>+9.2f}")
    print("=" * 72)
