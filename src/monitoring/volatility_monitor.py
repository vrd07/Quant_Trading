"""
Real-time session-open volatility monitor — "Beast mode" scalp-alert logic.

"Beast mode" is NOT a strategy in the trading system — it is an alert-only
composite for manual scalping at the London/NY opens. A symbol triggers when
ALL of the following hold (every threshold lives in BeastConfig):

  1. SESSION    in a session-open window (defaults: London 07:00-09:00 UTC,
                NY 12:30-14:30 UTC)
  2. EXPANSION  last completed 1m bar true range >= range_expansion_mult x
                median true range of the baseline window
  3. MOMENTUM   |close - close[momentum_bars ago]| >= momentum_atr_mult x
                baseline ATR (sign gives the BUY/SELL bias)
  4. SPREAD     spread <= max_spread_frac x baseline ATR, and <= the broker
                max_spread from config when provided (scalp viability)

The baseline deliberately excludes the trigger bar, so a quiet pre-open
period is exactly the right reference for an open expansion. Detection is
pure (bars in, verdict out); the only mutable state is the bar builder and
the alert cooldown governor. Driven live by scripts/volatility_monitor.py,
which feeds it from the EA status file (passive read — never the command
channel, which is single-owner with the bot's 250ms loop).
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MinuteBar:
    """One completed 1-minute mid-price bar."""

    start_epoch: int  # epoch seconds of the minute boundary
    open: float
    high: float
    low: float
    close: float
    samples: int


class MinuteBarBuilder:
    """Aggregates (epoch, mid) samples into 1m bars.

    update() returns the just-completed bar when the minute rolls over,
    else None. Samples arriving out of order within the forming minute are
    fine; samples older than the forming minute are dropped.
    """

    def __init__(self, max_bars: int = 240):
        self.bars: Deque[MinuteBar] = deque(maxlen=max_bars)
        self._cur_start: Optional[int] = None
        self._o = self._h = self._l = self._c = 0.0
        self._n = 0

    def update(self, epoch: float, mid: float) -> Optional[MinuteBar]:
        minute_start = int(epoch) - (int(epoch) % 60)
        completed: Optional[MinuteBar] = None

        if self._cur_start is None:
            self._start_bar(minute_start, mid)
            return None

        if minute_start < self._cur_start:
            return None  # stale sample
        if minute_start > self._cur_start:
            completed = MinuteBar(self._cur_start, self._o, self._h, self._l, self._c, self._n)
            self.bars.append(completed)
            self._start_bar(minute_start, mid)
            return completed

        self._h = max(self._h, mid)
        self._l = min(self._l, mid)
        self._c = mid
        self._n += 1
        return None

    def _start_bar(self, minute_start: int, mid: float) -> None:
        self._cur_start = minute_start
        self._o = self._h = self._l = self._c = mid
        self._n = 1


# ---------------------------------------------------------------------------
# Config / sessions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionWindow:
    name: str
    start_minute: int  # minutes since UTC midnight, inclusive
    end_minute: int    # exclusive

    def contains(self, now_utc: datetime) -> bool:
        m = now_utc.hour * 60 + now_utc.minute
        return self.start_minute <= m < self.end_minute


DEFAULT_SESSIONS: Tuple[SessionWindow, ...] = (
    SessionWindow("LONDON_OPEN", 7 * 60, 9 * 60),
    SessionWindow("NY_OPEN", 12 * 60 + 30, 14 * 60 + 30),
)


@dataclass(frozen=True)
class BeastConfig:
    baseline_bars: int = 30          # bars in the baseline window (trigger bar excluded)
    min_baseline_bars: int = 15      # leave WARMING once this many baseline bars exist
    range_expansion_mult: float = 2.0
    momentum_bars: int = 3
    momentum_atr_mult: float = 1.5
    max_spread_frac: float = 0.35    # spread cap as a fraction of baseline ATR
    cooldown_sec: float = 300.0
    sessions: Tuple[SessionWindow, ...] = DEFAULT_SESSIONS


def active_session(now_utc: datetime, sessions: Sequence[SessionWindow]) -> Optional[str]:
    for s in sessions:
        if s.contains(now_utc):
            return s.name
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# Verdict states
WARMING = "WARMING"
OFF_SESSION = "OFF_SESSION"
QUIET = "QUIET"
HOT = "HOT"
BEAST = "BEAST"


@dataclass
class Verdict:
    state: str
    session: Optional[str] = None
    direction: Optional[str] = None          # "BUY" / "SELL" when momentum fires
    range_ratio: Optional[float] = None      # trigger TR / median baseline TR
    momentum_atr: Optional[float] = None     # signed momentum move in baseline-ATR units
    spread_frac: Optional[float] = None      # spread / baseline ATR
    reasons: List[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return self.state == BEAST


def _true_ranges(bars: Sequence[MinuteBar]) -> List[float]:
    trs: List[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            trs.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            trs.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return trs


def evaluate(
    bars: Sequence[MinuteBar],
    spread: float,
    now_utc: datetime,
    cfg: BeastConfig = BeastConfig(),
    broker_max_spread: Optional[float] = None,
) -> Verdict:
    """Evaluate Beast-mode conditions on completed bars.

    bars must be completed 1m bars in time order; the last one is the
    trigger bar, everything before it feeds the baseline.
    """
    session = active_session(now_utc, cfg.sessions)

    if len(bars) < cfg.min_baseline_bars + 1:
        return Verdict(state=WARMING, session=session,
                       reasons=[f"warming: {max(0, len(bars) - 1)}/{cfg.min_baseline_bars} baseline bars"])

    window = bars[-(cfg.baseline_bars + 1):]
    trs = _true_ranges(window)
    baseline_trs = trs[:-1]
    trigger_tr = trs[-1]

    median_tr = statistics.median(baseline_trs)
    atr = sum(baseline_trs) / len(baseline_trs)
    if median_tr <= 0 or atr <= 0:
        return Verdict(state=QUIET, session=session, reasons=["flat baseline (zero range)"])

    range_ratio = trigger_tr / median_tr

    m = min(cfg.momentum_bars, len(bars) - 1)
    move = bars[-1].close - bars[-1 - m].close
    momentum_atr = move / atr

    spread_frac = spread / atr

    expansion_ok = range_ratio >= cfg.range_expansion_mult
    momentum_ok = abs(momentum_atr) >= cfg.momentum_atr_mult
    spread_ok = spread_frac <= cfg.max_spread_frac and (
        broker_max_spread is None or spread <= broker_max_spread
    )

    direction = ("BUY" if move > 0 else "SELL") if momentum_ok else None

    reasons: List[str] = []
    if expansion_ok:
        reasons.append(f"range {range_ratio:.1f}x baseline median")
    if momentum_ok:
        reasons.append(f"momentum {momentum_atr:+.1f} ATR over {m}m")
    if not spread_ok:
        reasons.append(f"spread too wide ({spread_frac:.2f} ATR)")

    verdict = Verdict(
        state=QUIET,
        session=session,
        direction=direction,
        range_ratio=range_ratio,
        momentum_atr=momentum_atr,
        spread_frac=spread_frac,
        reasons=reasons,
    )

    if session is None:
        verdict.state = OFF_SESSION
        return verdict

    if expansion_ok and momentum_ok and spread_ok:
        verdict.state = BEAST
    elif expansion_ok or momentum_ok:
        verdict.state = HOT
    return verdict


# ---------------------------------------------------------------------------
# Alert cooldown
# ---------------------------------------------------------------------------


class AlertGovernor:
    """Per-(symbol, direction) cooldown so one burst doesn't spam alerts."""

    def __init__(self, cooldown_sec: float = 300.0):
        self.cooldown_sec = cooldown_sec
        self._last_fired: Dict[Tuple[str, str], float] = {}

    def should_fire(self, symbol: str, direction: str, now_epoch: float) -> bool:
        key = (symbol, direction or "?")
        last = self._last_fired.get(key)
        if last is not None and now_epoch - last < self.cooldown_sec:
            return False
        self._last_fired[key] = now_epoch
        return True


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
