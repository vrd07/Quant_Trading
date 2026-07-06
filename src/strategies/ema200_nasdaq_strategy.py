"""
EMA 200 NASDAQ Strategy — NASDAQ-100 CFD, 5m, one entry per day.

User-authored rule (new_strategies.md #2):

  - ANCHOR candle = the 5m candle at 19:10 IST = 13:40 UTC (IST has no DST →
    fixed UTC time year-round).
  - Anchor closes ABOVE EMA(200) → BUY setup: the FIRST later candle closing
    above the anchor's close, within the 19:10–21:10 IST window (trigger close
    ≤ 15:40 UTC), is the entry. Mirror below the EMA for SELL.
  - STRICTLY one entry per day.
  - SL = anchor candle's opposite extreme (low for BUY / high for SELL);
    TP = `rr` × stop distance (spec RR 1:2).

⚠️ Research verdict (scripts/research_ema200_nas.py, 2026-07-07, 2.5y strict
fills): FAILED the promotion gate — full-span PF 1.04; 2024 PF 0.90 / 2025 PF
1.41 / 2026 PF 0.79; raw DD −31.5%; enforced $5k run halts at PF 0.41. 2025's
good year coincides with the one-way NASDAQ bull leg (regime beta, not edge).
SHIPPED ANYWAY BY USER DECISION 2026-07-07 ("we can tune later") — treat as an
experimental stream, size at min lot, expect bleed outside strong trend years.

Symbol is CONFIGURABLE: the broker lists NASDAQ-100 under its own ticker, which
the user inputs from the start script (runtime_setup writes
`strategies.ema200_nasdaq.allowed_symbols`). Prefix match handles broker
suffixes. Default 'NAS100' (the research/Dukascopy ticker).

Stateless: the day's anchor + first-trigger scan is recomputed from the bars
window every on_bar; only a dedup latch is kept on the instance.
"""

from typing import Any, Dict, Optional

import pandas as pd

from ..core.constants import MarketRegime, OrderSide
from ..core.types import Signal, Symbol
from ..data.indicators import Indicators
from .base_strategy import BaseStrategy


class EMA200NasdaqStrategy(BaseStrategy):
    """13:40 UTC EMA(200) anchor-break, one entry/day. NASDAQ-100 only."""

    def __init__(self, symbol: Symbol, config: Dict[str, Any]):
        super().__init__(symbol, config)
        self.ema_period = int(config.get('ema_period', 200))
        self.anchor_hour_utc = int(config.get('anchor_hour_utc', 13))     # 19:10 IST
        self.anchor_minute_utc = int(config.get('anchor_minute_utc', 40))
        self.entry_end_hour_utc = int(config.get('entry_end_hour_utc', 15))   # 21:10 IST
        self.entry_end_minute_utc = int(config.get('entry_end_minute_utc', 40))
        self.rr = float(config.get('rr', 2.0))            # spec: RR 1:2
        self.min_stop_pts = float(config.get('min_stop_pts', 1.0))
        self.timeframe_minutes = int(config.get('timeframe_minutes', 5))
        self.allowed_symbol_prefixes = tuple(
            s.upper() for s in config.get('allowed_symbols', ['NAS100'])
        )
        self._last_signal_ts = None   # dedup latch (one-entry/day is recomputed)

    def get_name(self) -> str:
        return "ema200_nasdaq"

    def on_bar(self, bars: pd.DataFrame) -> Optional[Signal]:
        # EMA(200) needs real warmup before the anchor to match a long-history EMA.
        min_bars = 3 * self.ema_period + 30
        if not self.enabled or len(bars) < min_bars:
            return None
        if not self.symbol.ticker.upper().startswith(self.allowed_symbol_prefixes):
            return None

        ts = self._bar_timestamp(bars)
        if ts is None:
            return None
        anchor_min = self.anchor_hour_utc * 60 + self.anchor_minute_utc
        # Trigger candle must CLOSE by the window end → its open time is at most
        # one bar earlier.
        last_trigger_open = (self.entry_end_hour_utc * 60
                             + self.entry_end_minute_utc - self.timeframe_minutes)
        now_min = ts.hour * 60 + ts.minute
        if not (anchor_min < now_min <= last_trigger_open):
            self._log_no_signal("outside entry window")
            return None

        # Today's bars (the last bar's UTC date), in window order.
        idx = pd.DatetimeIndex(bars.index)
        today = idx[-1].date()
        day_pos = [i for i, t in enumerate(idx) if t.date() == today]
        minutes = idx.hour * 60 + idx.minute
        anchor_pos = None
        for i in day_pos:
            if minutes[i] == anchor_min:
                anchor_pos = i
                break
        if anchor_pos is None or anchor_pos < self.ema_period:
            self._log_no_signal("no anchor candle (or insufficient EMA warmup)")
            return None

        ema = Indicators.ema(bars, period=self.ema_period)
        anchor_close = float(bars['close'].iloc[anchor_pos])
        anchor_ema = float(ema.iloc[anchor_pos])
        if pd.isna(anchor_ema):
            return None
        if anchor_close > anchor_ema:
            side = OrderSide.BUY
            sl = float(bars['low'].iloc[anchor_pos])
        elif anchor_close < anchor_ema:
            side = OrderSide.SELL
            sl = float(bars['high'].iloc[anchor_pos])
        else:
            self._log_no_signal("anchor close exactly on EMA")
            return None

        # ONE entry per day: only the FIRST triggering candle counts. Scan the
        # bars between anchor and now — if any earlier bar already triggered,
        # today is done regardless of what the current bar does.
        closes = bars['close'].to_numpy(float)
        last = len(bars) - 1
        for i in range(anchor_pos + 1, last):
            if minutes[i] > last_trigger_open:
                break
            broke = (closes[i] > anchor_close if side == OrderSide.BUY
                     else closes[i] < anchor_close)
            if broke:
                self._log_no_signal("today's single entry already triggered")
                return None

        c = closes[last]
        triggered = (c > anchor_close if side == OrderSide.BUY
                     else c < anchor_close)
        if not triggered:
            self._log_no_signal("anchor close not broken yet")
            return None

        dist = (c - sl) if side == OrderSide.BUY else (sl - c)
        if dist < self.min_stop_pts:
            self._log_no_signal("anchor stop too tight")
            return None

        if self._last_signal_ts is not None and pd.Timestamp(ts).date() == \
                pd.Timestamp(self._last_signal_ts).date():
            return None   # dedup latch (belt-and-braces on top of the scan)

        target = c + self.rr * dist if side == OrderSide.BUY else c - self.rr * dist
        self._last_signal_ts = ts

        return self._create_signal(
            side=side,
            strength=0.6,
            regime=MarketRegime.TREND,
            entry_price=c,
            stop_loss=sl,
            take_profit=target,
            metadata={
                'strategy': 'ema200_nasdaq',
                'mode': 'anchor_break',
                'stop_price': sl,             # RiskProcessor honors this verbatim
                'take_profit_price': target,
                # The anchor-extreme stop + fixed RR IS the spec — keep BudgetSL
                # from rewriting it.
                'preserve_structural_sl': True,
                'anchor_close': anchor_close,
                'anchor_ema': anchor_ema,
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
