"""
Backtest-side news-blackout replay — backtest.md §3.4.

The live news_filter only checks today's events anchored to the wall clock,
which is correct in production but wrong for replay. This module:
  • parses Date + Time from the ForexFactory CSV(s)
  • supports an asymmetric blackout window (−15 / +30 min by default)
  • exposes O(log n) lookup keyed by the bar timestamp

Same currency/impact filtering as load_ff_events; same Asia/Kolkata default
timezone (the live news CSVs are written in IST per fetch_daily_news.py).

Behavior in the backtest path:
  • New signals during a blackout are DROPPED (matches live).
  • Open positions stay open (matches live).
  • Spread is multiplied by 3× inside the window (handled in fill_model).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

import pandas as pd
import pytz


# §3.4 / production parity: -15 min before, +30 min after.
DEFAULT_PRE_MINUTES: int = 15
DEFAULT_POST_MINUTES: int = 30
DEFAULT_TZ: str = "Asia/Kolkata"


def _parse_date_time(date_s: str, time_s: str, tz: pytz.tzinfo.BaseTzInfo) -> Optional[datetime]:
    """Parse a single (Date, Time) row into a tz-aware UTC datetime.

    The CSV uses MM-DD-YYYY dates and 12-hour times with am/pm suffix
    (e.g. "08:30am"). Placeholder "00:00am" / "All Day" / "Tentative"
    rows are skipped.
    """
    t = (time_s or "").strip().upper()
    if t in {"", "NAN", "00:00AM", "ALL DAY", "TENTATIVE"}:
        return None
    try:
        # Two known historical formats; try each.
        for fmt in ("%m-%d-%Y %I:%M%p", "%Y-%m-%d %I:%M%p", "%m/%d/%Y %I:%M%p"):
            try:
                naive = datetime.strptime(f"{date_s.strip()} {t}", fmt)
                return tz.localize(naive).astimezone(pytz.UTC)
            except ValueError:
                continue
    except Exception:
        return None
    return None


@dataclass
class NewsBlackoutReplay:
    """Date-aware news blackout lookup for backtests.

    Build once at the start of the backtest, then call .is_active(ts) per bar.
    """
    events_utc: List[datetime] = field(default_factory=list)
    pre: timedelta = field(default_factory=lambda: timedelta(minutes=DEFAULT_PRE_MINUTES))
    post: timedelta = field(default_factory=lambda: timedelta(minutes=DEFAULT_POST_MINUTES))

    @classmethod
    def from_csv(
        cls,
        csv_paths: Union[str, Path, Sequence[Union[str, Path]]],
        currency: Union[str, Iterable[str]] = ("USD",),
        impacts: Optional[Iterable[str]] = None,
        timezone: str = DEFAULT_TZ,
        pre_minutes: int = DEFAULT_PRE_MINUTES,
        post_minutes: int = DEFAULT_POST_MINUTES,
    ) -> "NewsBlackoutReplay":
        """Load one or more ForexFactory CSVs and pre-compute UTC event times.

        Args:
            csv_paths: single CSV path or a list (e.g. one per month / year).
            currency: keep only rows for these currencies.
            impacts: keep only these impact levels (default ['high', 'red']).
            timezone: timezone the CSVs are stored in (Asia/Kolkata in our CSVs).
            pre_minutes / post_minutes: asymmetric blackout window.
        """
        if isinstance(csv_paths, (str, Path)):
            paths = [Path(csv_paths)]
        else:
            paths = [Path(p) for p in csv_paths]

        if isinstance(currency, str):
            currencies = {currency.upper()}
        else:
            currencies = {str(c).upper() for c in currency}

        impacts_lower = {i.lower() for i in (impacts or ["high", "red"])}
        tz = pytz.timezone(timezone)

        events_utc: List[datetime] = []
        for path in paths:
            if not path.exists():
                continue
            df = pd.read_csv(path)
            df.columns = [c.strip().lower() for c in df.columns]
            if "date" not in df.columns or "time" not in df.columns:
                continue
            # Currency / impact filters mirror load_ff_events.
            if "currency" in df.columns:
                df = df[df["currency"].astype(str).str.strip().str.upper().isin(currencies)]
            if "impact" in df.columns:
                df = df[df["impact"].astype(str).str.strip().str.lower().isin(impacts_lower)]

            for date_s, time_s in zip(df["date"].astype(str), df["time"].astype(str)):
                dt_utc = _parse_date_time(date_s, time_s, tz)
                if dt_utc is not None:
                    events_utc.append(dt_utc)

        events_utc.sort()
        return cls(
            events_utc=events_utc,
            pre=timedelta(minutes=pre_minutes),
            post=timedelta(minutes=post_minutes),
        )

    def is_active(self, ts: datetime) -> bool:
        """Return True iff ts falls in any [event - pre, event + post]."""
        if not self.events_utc:
            return False
        # Coerce to UTC tz-aware so binary search compares apples-to-apples.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=pytz.UTC)
        else:
            ts = ts.astimezone(pytz.UTC)

        # Find the rightmost event whose time is <= ts + self.pre. Any earlier
        # event won't reach ts; that one + later ones are the only candidates.
        idx = bisect.bisect_right(self.events_utc, ts + self.pre) - 1
        # Walk backward through the (at most 2-3) overlapping events. In
        # practice an event window is 45 min so at most a handful overlap.
        while idx >= 0:
            ev = self.events_utc[idx]
            if ev + self.post < ts:
                return False  # this event already ended; nothing earlier reaches ts
            if ev - self.pre <= ts <= ev + self.post:
                return True
            idx -= 1
        return False

    def is_active_at_bar(self, bar) -> bool:
        """Adapter: pulls the timestamp out of a pandas Series or Bar dataclass."""
        ts = getattr(bar, "name", None)
        if ts is None:
            ts = getattr(bar, "timestamp", None)
        if ts is None:
            return False
        # Convert pandas.Timestamp -> python datetime (already tz-aware if index has tz).
        return self.is_active(pd.Timestamp(ts).to_pydatetime())

    def __len__(self) -> int:
        return len(self.events_utc)
