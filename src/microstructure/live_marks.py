"""
Pure live-marking engine over a stitched day frame.

Closed-candle gating: a mark becomes visible only once its bar (of the
viewing timeframe) has closed — it then never un-happens in the FEED, which
is append-only and persisted as jsonl (the paper-trail for judging live
usefulness, and any future Stage-2 labeling). The chart's detector output
may still drift as day-percentiles evolve; the feed does not.

No I/O here except SignalFeed's jsonl append. All quantities remain proxies.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from src.microstructure import features as ft
from src.microstructure.features import FlowEvent


def closed_candle_events(df: pd.DataFrame, timeframe: str, params: dict,
                         now: pd.Timestamp) -> list[FlowEvent]:
    """Run all five detectors; keep only events whose bar has closed.

    In the tap-covered window only divergence/absorption/imbalance can fire
    (a 1 Hz tap gives a constant arrival rate, so the sweep burst leg and
    withdrawal rate leg are inert there); sweeps/withdrawals firm up as
    Dukascopy hours backfill.
    """
    if df.empty:
        return []
    bars = ft.resample_bars(df, timeframe)
    delta = ft.bar_delta(df, timeframe)
    events: list[FlowEvent] = []
    events += ft.delta_divergence(bars, delta, lookback=int(params["lookback"]))
    events += ft.absorption_zones(df, band_pts=params["band_pts"],
                                  flow_pctile=params["flow_pctile"])
    events += ft.imbalance_events(df, freq=timeframe,
                                  price_bin=params["price_bin"], ratio=params["ratio"])
    events += ft.sweep_events(df, burst_pctile=params["burst_pctile"])
    events += ft.liquidity_withdrawal(df, spread_pctile=params["spread_pctile"])
    td = pd.Timedelta(timeframe)
    closed = [e for e in events if e.ts.floor(timeframe) + td <= now]
    return sorted(closed, key=lambda e: (e.ts, e.kind))


@dataclass(frozen=True)
class FeedEntry:
    emitted_at: str
    bar_ts: str
    kind: str
    price: float
    strength: float


class SignalFeed:
    """Append-only signal log with dedup on (kind, bar_ts, price_bin)."""

    def __init__(self, path: Path | None, price_bin: float = 0.5):
        self.path = Path(path) if path is not None else None
        self.price_bin = price_bin
        self.entries: list[FeedEntry] = []
        self._seen: set[tuple] = set()
        if self.path is not None and self.path.exists():
            for line in self.path.read_text().splitlines():
                d = json.loads(line)
                entry = FeedEntry(**d)
                self.entries.append(entry)
                self._seen.add(self._key_from(entry.kind, entry.bar_ts, entry.price))

    def _key_from(self, kind: str, bar_ts_iso: str, price: float) -> tuple:
        return (kind, bar_ts_iso, round(price / self.price_bin) * self.price_bin)

    def ingest(self, events: list[FlowEvent],
               now: pd.Timestamp | None = None) -> list[FeedEntry]:
        stamp = (now if now is not None else pd.Timestamp.now(tz="UTC")).isoformat()
        new: list[FeedEntry] = []
        for e in events:
            key = self._key_from(e.kind, e.ts.isoformat(), e.price)
            if key in self._seen:
                continue
            self._seen.add(key)
            entry = FeedEntry(emitted_at=stamp, bar_ts=e.ts.isoformat(),
                              kind=e.kind, price=float(e.price),
                              strength=float(e.strength))
            self.entries.append(entry)
            new.append(entry)
        if new and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a") as f:
                for entry in new:
                    f.write(json.dumps(asdict(entry)) + "\n")
        return new
