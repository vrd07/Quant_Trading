"""Unit tests for the Dukascopy tick decoder — no network."""
import lzma
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from scripts.fetch_dukascopy_ticks import (
    TICK_RECORD,
    TickFetchError,
    day_path,
    decode_bi5,
    ensure_ticks,
)


def test_decode_bi5_two_ticks():
    base = datetime(2026, 7, 1, 9, tzinfo=timezone.utc)
    # Records are big-endian: offset_ms, ask_points, bid_points, ask_vol, bid_vol
    raw = TICK_RECORD.pack(250, 3350120, 3350050, 1.25, 2.5) + \
          TICK_RECORD.pack(1500, 3350200, 3350150, 0.5, 0.75)
    df = decode_bi5(lzma.compress(raw), base, point=0.001)
    assert list(df.columns) == ["ts", "bid", "ask", "bid_vol", "ask_vol"]
    assert len(df) == 2
    assert df.loc[0, "ts"] == pd.Timestamp("2026-07-01 09:00:00.250", tz="UTC")
    assert df.loc[0, "bid"] == pytest.approx(3350.050)
    assert df.loc[0, "ask"] == pytest.approx(3350.120)
    assert df.loc[0, "bid_vol"] == pytest.approx(2.5)
    assert df.loc[1, "ask_vol"] == pytest.approx(0.5)


def test_decode_bi5_empty_hour():
    base = datetime(2026, 7, 1, 3, tzinfo=timezone.utc)
    df = decode_bi5(lzma.compress(b""), base, point=0.001)
    assert df.empty
    assert list(df.columns) == ["ts", "bid", "ask", "bid_vol", "ask_vol"]


def test_day_path_layout(tmp_path):
    p = day_path("XAUUSD", date(2026, 7, 1), ticks_dir=tmp_path)
    assert p == tmp_path / "XAUUSD" / "2026-07-01.parquet"


def test_ensure_ticks_skips_holed_day_on_fetch_error(tmp_path, monkeypatch):
    """A transient fetch failure must not write a partial Parquet, and must
    not be cached as 'done' — ensure_ticks should skip the day and return []."""
    import scripts.fetch_dukascopy_ticks as fdt

    def _boom(symbol, day, point, workers=6):
        raise TickFetchError(f"{day} 00h: simulated network failure")

    monkeypatch.setattr(fdt, "fetch_day_ticks", _boom)

    day = date(2026, 7, 1)  # Wednesday — not the Saturday-skip branch
    paths = fdt.ensure_ticks("XAUUSD", day, day, point=0.001, ticks_dir=tmp_path)

    assert paths == []
    assert not (tmp_path / "XAUUSD" / f"{day.isoformat()}.parquet").exists()
