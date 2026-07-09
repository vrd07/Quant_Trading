"""
BOS Structure Strategy — XAUUSD 15m.

SMC-style break-of-structure sequence (new_strategies.md #1, user spec):

  1. CHOCH   — close breaks the last swing level AGAINST the prevailing trend.
  2. BOS #1  — close breaks the next swing level in the NEW direction (trend flips).
  3. BOS #2  — second break in the new direction (sequence "armed").
  4. ENTRY   — the next CONFIRMED pullback pivot: higher-low for longs /
               lower-high for shorts. Each further BOS re-arms one more entry.
  5. STOP    — structural, just beyond the entry pivot (`buffer_atr` × ATR,
               floored); TP = `rr` × stop distance.

Swing pivots are `pivot_bars`-bar fractals and CONFIRM `pivot_bars` bars after
their extreme — the signal fires on the pivot-confirmation bar (no lookahead).
Breaks are close-based (wick breaks ignored).

Research basis (scripts/research_bos_structure.py, 2026-07-07, strict fills,
walk-forward 2025/2026): XAUUSD N=5 positive BOTH years at ALL RRs (PF 1.41-1.60);
best cell N=5 RR2.0 — full PF 1.60 (+$2,269 @0.02 lot), 2025 PF 1.56 / 2026 PF
1.64, cost-robust to 3× (PF 1.52), median stop 13.6 pts, ~4-5 trades/week.
US30 FAILED the same harness (PF 1.05, enforced 0.73) — hence the hard gate.

⚠️ $5k caveat: under enforcement (fixed 0.02 lot, $150 daily, $250 trailing) the
run trips the 5% trailing kill switch in the Jun-Jul 2025 losing stretch after
peaking +$850 (PF 1.51 to the halt) — the stoch_pullback pattern: min lot can't
de-risk into a drawdown. Happier at $25k+.

Stateless: the full structure state machine is recomputed from the supplied bars
window every on_bar; the only instance state is a signal-dedup latch.
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from ..data.indicators import Indicators
from .base_strategy import BaseStrategy


class BOSStructureStrategy(BaseStrategy):
    """CHOCH → BOS×2 → confirmed pullback-pivot entry. XAUUSD 15m only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.pivot_bars = int(config.get('pivot_bars', 5))    # spec default; relaxed=3
        self.rr = float(config.get('rr', 2.0))                # TP = rr × structural SL
        # Frequency-relaxation knobs (2026-07-07 tuning pass, user request for
        # ~2 trades/day on $50k; sweep in research_bos_structure.py):
        #   arm_after_bos 2 = the original spec ("wait for second BOS");
        #     1 = arm on the first BOS (relaxed).
        #   one_shot True = one entry per BOS (spec); False = every qualifying
        #     pullback pivot fires while the sequence stays armed (relaxed).
        #   entry_on_break False = pullback entries only (spec); True = ALSO
        #     enter on each armed BOS break itself (stop = last trend pivot).
        # Relaxed cell (pivot 3 / arm 1 / one_shot False / break False / RR 2.0):
        # 578 trades (~1.5/day one-position sim), PF 1.25 full, 1.27/1.23 both yrs.
        self.arm_after_bos = int(config.get('arm_after_bos', 2))
        self.one_shot = bool(config.get('one_shot', True))
        self.entry_on_break = bool(config.get('entry_on_break', False))
        # ⚠️ THE EDGE ONLY EXISTS UNDER ONE-POSITION-AT-A-TIME DISCIPLINE
        # (2026-07-07 production-engine post-mortem: the engine takes every
        # overlapping same-sequence signal → 1686 trades, PF 0.87, −99%; the
        # one-position research stream is PF 1.30 norm-sized). single_position
        # replays a VIRTUAL position over the window after each emitted signal
        # and suppresses new signals until that trade's SL or TP would have
        # resolved — deterministic from bars, mirrors the research simulator.
        self.single_position = bool(config.get('single_position', True))
        # HTF side-alignment gate (squeeze_breakout recipe, validated for BOS
        # 2026-07-08): BUY only above the slow EMA, SELL only below — side-only,
        # no slope term. EMA(600) on 15m ≈ EMA150 1H; norm-sized research PF
        # improves BOTH years (2025 1.16→1.28, 2026 1.30→1.38); 400–600 plateau,
        # 800 degrades. Set 0 to disable.
        self.htf_ema_period = int(config.get('htf_ema_period', 600))
        self.buffer_atr = float(config.get('buffer_atr', 0.10))
        self.min_stop_pts = float(config.get('min_stop_pts', 2.0))
        self.atr_period = int(config.get('atr_period', 14))
        self.cooldown_bars = int(config.get('cooldown_bars', 5))
        self.timeframe_minutes = int(config.get('timeframe_minutes', 15))
        # Hard symbol gate (prefix match so the broker's XAUUSDs passes).
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['XAUUSD'])
        )
        self._last_signal_ts = None   # dedup/cooldown latch

    def get_name(self) -> str:
        return "bos_structure"

    # ------------------------------------------------------------------
    # Structure engine (pure functions of the bars window)
    # ------------------------------------------------------------------
    def _find_pivots(self, bars: pd.DataFrame) -> List[Tuple[int, str, float]]:
        """N-bar fractal pivots as (confirm_bar, 'H'|'L', price), sorted by
        confirm bar. A pivot confirms `pivot_bars` bars after its extreme."""
        n = self.pivot_bars
        h = bars['high'].to_numpy(float)
        l = bars['low'].to_numpy(float)
        w = 2 * n + 1
        hmax = bars['high'].rolling(w, center=True).max().to_numpy(float)
        lmin = bars['low'].rolling(w, center=True).min().to_numpy(float)
        piv: List[Tuple[int, str, float]] = []
        last_h = last_l = -10 ** 9
        for i in range(n, len(bars) - n):
            if h[i] == hmax[i] and i - last_h > n:
                piv.append((i + n, 'H', h[i]))
                last_h = i
            if l[i] == lmin[i] and i - last_l > n:
                piv.append((i + n, 'L', l[i]))
                last_l = i
        piv.sort(key=lambda p: p[0])
        return piv

    def _walk_structure(self, bars: pd.DataFrame,
                        atr: pd.Series) -> List[Dict[str, Any]]:
        """Replay the CHOCH→BOS→pullback machine over the window; return the
        entry signals it would have emitted (mirrors research_bos_structure.py)."""
        c = bars['close'].to_numpy(float)
        atr_arr = atr.to_numpy(float)
        if self.htf_ema_period > 0:
            htf = (bars['close'].ewm(span=self.htf_ema_period, adjust=False)
                   .mean().to_numpy(float))
            allow = lambda i, buy: (c[i] > htf[i]) if buy else (c[i] < htf[i])
        else:
            allow = lambda i, buy: True
        by_bar: Dict[int, List[Tuple[str, float]]] = {}
        for cb, kind, price in self._find_pivots(bars):
            by_bar.setdefault(cb, []).append((kind, price))

        trend = 0        # established trend (+1/-1/0)
        seq_dir = 0      # active CHOCH sequence direction
        bos_count = 0
        armed = False    # BOS#2 printed → next pullback pivot fires (one-shot per BOS)
        cur_sh = cur_sl = None    # latest UNBROKEN swing levels
        prev_hp = prev_lp = None  # previous pivot prices (HL/LH test)
        last_hp = last_lp = None
        out: List[Dict[str, Any]] = []

        for i in range(len(bars)):
            for kind, price in by_bar.get(i, []):
                buf = max(self.buffer_atr * (atr_arr[i] if atr_arr[i] > 0 else 0.0),
                          self.min_stop_pts * 0.5)
                if kind == 'H':
                    prev_hp, last_hp = last_hp, price
                    cur_sh = price
                    if (armed and seq_dir == -1 and prev_hp is not None
                            and price < prev_hp
                            and allow(i, False)):          # lower-high pullback
                        stop = price + buf
                        if stop > c[i]:
                            out.append(dict(bar_idx=i, side=OrderSide.SELL,
                                            stop=stop, bos_count=bos_count))
                            if self.one_shot:
                                armed = False
                else:
                    prev_lp, last_lp = last_lp, price
                    cur_sl = price
                    if (armed and seq_dir == 1 and prev_lp is not None
                            and price > prev_lp
                            and allow(i, True)):           # higher-low pullback
                        stop = price - buf
                        if stop < c[i]:
                            out.append(dict(bar_idx=i, side=OrderSide.BUY,
                                            stop=stop, bos_count=bos_count))
                            if self.one_shot:
                                armed = False

            buf = max(self.buffer_atr * (atr_arr[i] if atr_arr[i] > 0 else 0.0),
                      self.min_stop_pts * 0.5)
            if cur_sh is not None and c[i] > cur_sh:       # close-break up
                cur_sh = None                              # consumed until new SH
                if seq_dir == 1:
                    bos_count += 1
                    if bos_count == 1:
                        trend = 1
                    if bos_count >= self.arm_after_bos:
                        armed = True
                        if (self.entry_on_break and last_lp is not None
                                and last_lp < c[i] and allow(i, True)):
                            out.append(dict(bar_idx=i, side=OrderSide.BUY,
                                            stop=last_lp - buf,
                                            bos_count=bos_count))
                elif trend <= 0:
                    seq_dir, bos_count, armed = 1, 0, False        # CHOCH up
            if cur_sl is not None and c[i] < cur_sl:       # close-break down
                cur_sl = None
                if seq_dir == -1:
                    bos_count += 1
                    if bos_count == 1:
                        trend = -1
                    if bos_count >= self.arm_after_bos:
                        armed = True
                        if (self.entry_on_break and last_hp is not None
                                and last_hp > c[i] and allow(i, False)):
                            out.append(dict(bar_idx=i, side=OrderSide.SELL,
                                            stop=last_hp + buf,
                                            bos_count=bos_count))
                elif trend >= 0:
                    seq_dir, bos_count, armed = -1, 0, False       # CHOCH down
        return out

    def _one_position_filter(self, bars: pd.DataFrame,
                             signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep only the signals a one-position-at-a-time account would take.

        Each kept signal opens a VIRTUAL trade at the next bar's open with the
        signal's structural SL and TP = rr × stop distance; subsequent signals
        are dropped until that trade resolves (SL-first intrabar tie-break,
        same as the research simulator). A trade still open at the window end
        keeps suppressing — matching a real open position."""
        o = bars['open'].to_numpy(float)
        h = bars['high'].to_numpy(float)
        l = bars['low'].to_numpy(float)
        n = len(bars)
        kept: List[Dict[str, Any]] = []
        busy_until = -1          # bar index when the virtual trade resolved
        open_ended = False       # virtual trade never resolved in-window
        for s in signals:
            i = s['bar_idx']
            if open_ended or i < busy_until:
                continue
            kept.append(s)
            eb = i + 1
            if eb >= n:          # fired on the last bar — nothing to replay
                open_ended = True
                continue
            entry = o[eb]
            stop = float(s['stop'])
            long = s['side'] == OrderSide.BUY
            dist = (entry - stop) if long else (stop - entry)
            if dist <= 0:        # degenerate after next-bar gap; treat as flat
                busy_until = eb
                continue
            tp = entry + self.rr * dist if long else entry - self.rr * dist
            resolved = False
            for j in range(eb, n):
                if long:
                    if l[j] <= stop or h[j] >= tp:      # SL-first same bar
                        resolved = True
                else:
                    if h[j] >= stop or l[j] <= tp:
                        resolved = True
                if resolved:
                    busy_until = j + 1
                    break
            if not resolved:
                open_ended = True
        return kept

    # ------------------------------------------------------------------
    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        min_bars = 6 * self.pivot_bars + max(self.atr_period, 20) + 10
        if not self.enabled or len(bars) < min_bars:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None   # validated on XAUUSD only — never trade other symbols

        atr = Indicators.atr(bars, period=self.atr_period)
        signals = self._walk_structure(bars, atr)
        if self.single_position:
            signals = self._one_position_filter(bars, signals)
        last_idx = len(bars) - 1
        fired = [s for s in signals if s['bar_idx'] == last_idx]
        if not fired:
            self._log_no_signal("no confirmed pullback pivot at this bar")
            return None
        sig = fired[-1]

        ts = self._bar_timestamp(bars)
        if ts is not None and self._last_signal_ts is not None:
            elapsed_min = (ts - self._last_signal_ts).total_seconds() / 60.0
            if elapsed_min < self.cooldown_bars * self.timeframe_minutes:
                return None

        c = float(bars['close'].iloc[-1])
        stop = float(sig['stop'])
        dist = (c - stop) if sig['side'] == OrderSide.BUY else (stop - c)
        if dist < self.min_stop_pts:
            self._log_no_signal("structural stop too tight")
            return None
        target = c + self.rr * dist if sig['side'] == OrderSide.BUY else c - self.rr * dist

        self._last_signal_ts = ts

        return self._create_signal(
            side=sig['side'],
            strength=min(1.0, 0.5 + 0.1 * sig['bos_count']),
            regime=MarketRegime.TREND,
            entry_price=c,
            stop_loss=stop,
            take_profit=target,
            metadata={
                'strategy': 'bos_structure',
                'mode': 'bos_pullback',
                'stop_price': stop,           # RiskProcessor honors this verbatim
                'take_profit_price': target,
                # The STRUCTURAL stop + fixed RR is the validated geometry — keep
                # the execution-layer BudgetSL from shrinking it to the $ budget.
                'preserve_structural_sl': True,
                'bos_count': sig['bos_count'],
                'pivot_bars': self.pivot_bars,
                'rr': self.rr,
            },
        )

    @staticmethod
    def _bar_timestamp(bars: pd.DataFrame) -> Optional[pd.Timestamp]:
        idx = bars.index[-1]
        if hasattr(idx, 'hour'):
            return pd.Timestamp(idx)
        if 'timestamp' in bars.columns and len(bars) > 0:
            try:
                return pd.Timestamp(bars['timestamp'].iloc[-1])
            except Exception:
                return None
        return None
