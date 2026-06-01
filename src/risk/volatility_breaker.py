"""
Volatility Breaker — stand-aside guard for abnormal-volatility / shock regimes.

Geopolitical shocks (war headlines, etc.) are NOT on the economic calendar, so the
ForexFactory news blackout never catches them. They show up only as one thing the
rest of the regime stack ignores: raw volatility *magnitude*. The TREND/RANGE/VOLATILE
classifier keys off the *shape* of the move (direction vs range), so a violent but
directional safe-haven spike lands in TREND and the bot leans straight into it.

This breaker is purely magnitude-based and price-only (no news feed):

    ratio = current_ATR / median(ATR over baseline_window)

    inactive → active   when ratio >= trigger_mult
    active   → inactive  when ratio <= release_mult   (hysteresis, no flapping)

While active the caller pauses NEW entries and moves open green stops to breakeven.
Failure mode is fail-OPEN (insufficient data ⇒ inactive) — this is a protective
overlay, not a kill switch; it must never wedge trading shut on a data hiccup.
"""

from typing import Optional
import pandas as pd

from ..monitoring.logger import get_logger

logger = get_logger(__name__)


class VolatilityBreaker:
    def __init__(self, config: dict):
        cfg = (config.get('risk', {}) or {}).get('volatility_breaker', {}) or {}
        self.enabled: bool = bool(cfg.get('enabled', False))
        self.timeframe: str = cfg.get('timeframe', '15m')
        self.atr_period: int = int(cfg.get('atr_period', 14))
        self.baseline_window: int = int(cfg.get('baseline_window', 50))
        self.trigger_mult: float = float(cfg.get('trigger_mult', 2.5))
        self.release_mult: float = float(cfg.get('release_mult', 1.5))

        self.active: bool = False
        self.last_ratio: float = 0.0
        # Set True for exactly one update() call on the inactive→active edge so
        # the caller can fire the one-shot move-to-breakeven without repeating it.
        self.just_activated: bool = False

    @staticmethod
    def _wilder_atr(bars: pd.DataFrame, period: int) -> Optional[float]:
        """Latest Wilder ATR value, or None if not enough clean bars."""
        if bars is None or len(bars) < period + 1:
            return None
        high, low, close = bars['high'], bars['low'], bars['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
        val = atr.iloc[-1]
        return float(val) if pd.notna(val) else None

    def update(self, bars: pd.DataFrame) -> bool:
        """Recompute the volatility ratio and apply hysteresis. Returns active."""
        self.just_activated = False
        if not self.enabled:
            self.active = False
            return False

        # Need baseline_window ATR points to form a stable median.
        need = self.atr_period + self.baseline_window
        if bars is None or len(bars) < need:
            return self.active  # fail-open: hold current state, don't block on thin data

        high, low, close = bars['high'], bars['low'], bars['close']
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.ewm(alpha=1.0 / self.atr_period, adjust=False).mean()

        current = float(atr_series.iloc[-1])
        baseline = float(atr_series.iloc[-self.baseline_window:].median())
        if baseline <= 0:
            return self.active

        self.last_ratio = current / baseline

        if not self.active and self.last_ratio >= self.trigger_mult:
            self.active = True
            self.just_activated = True
            logger.critical(
                f"[VolatilityBreaker] 🚨 SHOCK MODE ON — ATR {self.last_ratio:.2f}× "
                f"baseline (>= {self.trigger_mult}×). Pausing new entries; "
                f"green stops → breakeven."
            )
        elif self.active and self.last_ratio <= self.release_mult:
            self.active = False
            logger.warning(
                f"[VolatilityBreaker] ✅ SHOCK MODE OFF — ATR back to "
                f"{self.last_ratio:.2f}× baseline (<= {self.release_mult}×). "
                f"Resuming normal trading."
            )

        return self.active
