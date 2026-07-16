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

import numpy as np
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


# ---------------------------------------------------- where-are-the-orders

@dataclass(frozen=True)
class DefendedLevel:
    """EVIDENCE layer: a price band where absorption keeps recurring —
    someone's resting orders are eating flow there."""
    price: float
    side: str          # "buyers" (selling absorbed) / "sellers" (buying absorbed)
    touches: int
    last_ts: pd.Timestamp


def defended_levels(events: list[FlowEvent],
                    band_pts: float = 1.0) -> list[DefendedLevel]:
    clusters: dict[tuple, dict] = {}
    for e in events:
        if not e.kind.startswith("absorption"):
            continue
        key = (round(e.price / band_pts) * band_pts, e.kind)
        c = clusters.setdefault(key, {"touches": 0, "last_ts": e.ts, "prices": []})
        c["touches"] += 1
        c["last_ts"] = max(c["last_ts"], e.ts)
        c["prices"].append(e.price)
    out = []
    for (_, kind), c in clusters.items():
        side = "buyers" if kind == "absorption_of_selling" else "sellers"
        out.append(DefendedLevel(price=float(np.mean(c["prices"])), side=side,
                                 touches=c["touches"], last_ts=c["last_ts"]))
    return sorted(out, key=lambda d: (-d.touches, d.price))


@dataclass(frozen=True)
class LiquidityPool:
    """HEURISTIC (inferred) layer: where stops/limits statistically cluster —
    un-swept swings, equal extremes, round numbers. Inference, not data."""
    price: float
    side: str          # "buy_side" (above price) / "sell_side" (below price)
    kind: str          # swing_high | swing_low | equal_highs | equal_lows | round


def liquidity_pools(bars: pd.DataFrame, swing_bars: int = 5,
                    eq_tol_pts: float = 0.5, round_step: float = 5.0,
                    max_levels: int = 12) -> list[LiquidityPool]:
    if len(bars) < 2 * swing_bars + 1:
        return []
    high, low, close = bars["high"], bars["low"], bars["close"]
    last = float(close.iloc[-1])

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(swing_bars, len(bars) - swing_bars):
        h = float(high.iloc[i])
        window_h = high.iloc[i - swing_bars:i + swing_bars + 1]
        if h == float(window_h.max()) and float(high.iloc[i + 1:].max()) < h:
            swing_highs.append(h)                     # confirmed AND un-swept
        l = float(low.iloc[i])
        window_l = low.iloc[i - swing_bars:i + swing_bars + 1]
        if l == float(window_l.min()) and float(low.iloc[i + 1:].min()) > l:
            swing_lows.append(l)

    def cluster(levels: list[float], eq_kind: str, solo_kind: str) -> list[tuple]:
        out, group = [], []
        for lvl in sorted(levels):
            if group and lvl - group[-1] > eq_tol_pts:
                out.append((float(np.mean(group)),
                            eq_kind if len(group) >= 2 else solo_kind))
                group = []
            group.append(lvl)
        if group:
            out.append((float(np.mean(group)),
                        eq_kind if len(group) >= 2 else solo_kind))
        return out

    pools: list[LiquidityPool] = []
    for price, kind in cluster(swing_highs, "equal_highs", "swing_high"):
        pools.append(LiquidityPool(price, "buy_side" if price > last else "sell_side",
                                   kind))
    for price, kind in cluster(swing_lows, "equal_lows", "swing_low"):
        pools.append(LiquidityPool(price, "buy_side" if price > last else "sell_side",
                                   kind))
    if round_step > 0:
        base = round(last / round_step) * round_step
        for k in (-2, -1, 0, 1, 2):
            lvl = base + k * round_step
            if abs(lvl - last) < 1e-9:
                continue
            pools.append(LiquidityPool(float(lvl),
                                       "buy_side" if lvl > last else "sell_side",
                                       "round"))
    pools.sort(key=lambda p: abs(p.price - last))
    return pools[:max_levels]
