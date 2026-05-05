"""Unit tests for backtest news-blackout replay (backtest.md §3.4).

Locks down:
  • Date-aware lookup — events on different dates don't bleed into each other.
  • Asymmetric -15/+30 min window.
  • Multi-event overlap is handled (one event ending while another is starting).
  • Empty CSV / missing CSV degrades gracefully.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytz

from src.backtest.news_replay import NewsBlackoutReplay


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Two events on different days; both NFP-style 08:30 IST."""
    p = tmp_path / "news.csv"
    p.write_text(
        "Date,Time,Currency,Impact,Event\n"
        "03-01-2026,08:30am,USD,High,NFP\n"
        "03-15-2026,02:00pm,USD,High,FOMC\n"
        "03-15-2026,06:00pm,EUR,High,ECB\n"  # filtered out (EUR)
        "03-15-2026,00:00am,USD,Low,Speech\n"  # filtered out (placeholder time + low impact)
    )
    return p


def _ist(date_s: str, hh: int, mm: int) -> datetime:
    """Helper: build a tz-aware Asia/Kolkata datetime."""
    return pytz.timezone("Asia/Kolkata").localize(
        datetime(2026, *map(int, date_s.split("-")), hh, mm)
    )


def test_loads_only_filtered_events(csv_path):
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    assert len(replay) == 2  # NFP + FOMC; EUR/low-impact dropped


def test_inside_window_is_active(csv_path):
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    # NFP @ 08:30 IST on Mar 1.
    # -15 min window: 08:15 IST should be active.
    assert replay.is_active(_ist("3-1", 8, 15))
    # +30 min: 09:00 IST still active.
    assert replay.is_active(_ist("3-1", 9, 0))


def test_outside_window_inactive(csv_path):
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    # 08:14 IST = 1 min before the -15min boundary → inactive.
    assert not replay.is_active(_ist("3-1", 8, 14))
    # 09:01 IST = 1 min after the +30min boundary → inactive.
    assert not replay.is_active(_ist("3-1", 9, 1))


def test_date_aware(csv_path):
    """Live news_filter would fire NFP every day at 08:30. Replay must not."""
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    # Mar 2 at 08:30 — no event scheduled that day.
    assert not replay.is_active(_ist("3-2", 8, 30))
    # Mar 1 at 08:30 — yes.
    assert replay.is_active(_ist("3-1", 8, 30))


def test_asymmetric_window(csv_path):
    """Default window is -15/+30; verify both legs."""
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    # 16 min before NFP → inactive
    assert not replay.is_active(_ist("3-1", 8, 14))
    # 31 min after NFP → inactive
    assert not replay.is_active(_ist("3-1", 9, 1))


def test_naive_timestamp_treated_as_utc(csv_path):
    """Date math should work even when bar timestamps are tz-naive."""
    replay = NewsBlackoutReplay.from_csv(csv_path, currency="USD", impacts=["high"])
    # NFP @ 08:30 IST = 03:00 UTC.
    naive = datetime(2026, 3, 1, 3, 0)  # tz-naive UTC
    assert replay.is_active(naive)


def test_empty_replay_is_inactive():
    replay = NewsBlackoutReplay()
    assert not replay.is_active(_ist("3-1", 8, 30))


def test_missing_csv_degrades_gracefully(tmp_path):
    replay = NewsBlackoutReplay.from_csv(tmp_path / "nope.csv")
    assert len(replay) == 0
    assert not replay.is_active(_ist("3-1", 8, 30))


def test_custom_window():
    """User can widen the window; spec-defaults are not the only option."""
    # Build directly to avoid CSV parse logic.
    import pytz as _pytz
    from src.backtest.news_replay import NewsBlackoutReplay
    ev = _pytz.UTC.localize(datetime(2026, 3, 1, 12, 0))
    replay = NewsBlackoutReplay(
        events_utc=[ev],
        pre=timedelta(hours=1),
        post=timedelta(hours=2),
    )
    inside = _pytz.UTC.localize(datetime(2026, 3, 1, 13, 30))
    outside = _pytz.UTC.localize(datetime(2026, 3, 1, 14, 30))
    assert replay.is_active(inside)
    assert not replay.is_active(outside)


def test_multi_event_overlap():
    """Two back-to-back events: window of one closes inside the other."""
    import pytz as _pytz
    from src.backtest.news_replay import NewsBlackoutReplay
    e1 = _pytz.UTC.localize(datetime(2026, 3, 1, 12, 0))
    e2 = _pytz.UTC.localize(datetime(2026, 3, 1, 12, 20))  # 20 min later
    replay = NewsBlackoutReplay(events_utc=[e1, e2])  # default -15/+30

    # e1 covers [11:45, 12:30]; e2 covers [12:05, 12:50].
    # Time 12:35 is past e1+30 but inside e2's window.
    t = _pytz.UTC.localize(datetime(2026, 3, 1, 12, 35))
    assert replay.is_active(t)

    # 13:00 is past both windows.
    t2 = _pytz.UTC.localize(datetime(2026, 3, 1, 13, 0))
    assert not replay.is_active(t2)
