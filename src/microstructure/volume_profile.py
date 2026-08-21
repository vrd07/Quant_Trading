"""
The Python definition of the XAUUSD volume profile — pure functions, no I/O.

This module is load-bearing twice over: `GoldenChart_VolumeProfile.mq5`
re-implements it in MQL5, and `scripts/check_volume_profile_parity.py` diffs the
two against what is written here. Anything ambiguous in this file becomes a
silent drift bug on the chart, so every rule below is stated in a form that has
exactly one possible implementation.

What this module builds is a TICK-DENSITY profile. Gold trades as a broker CFD:
there are no trade prints and no real volume, so a "volume" here is a count of
quote updates, never a count of contracts. Nothing downstream may present it as
traded volume.

Interpretation of a built profile — shape, regime, open type — lives in
`profile_context.py`. This module has no concept of "P", "b" or "D".

Design: docs/superpowers/specs/2026-08-17-volume-profile-indicator-design.md
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ProfileParams:
    """All distances are in PRICE UNITS (USD), never in broker 'points'.

    XAUUSD quotes at 2 or 3 digits depending on the broker, so a
    points-denominated value silently means $1.00 on one feed and $0.10 on
    another.
    """
    row_size: float = 0.10
    value_area_pct: float = 0.70
    va_algorithm: str = "single_row"       # "single_row" | "two_row"
    tick_price_mode: str = "mid"           # "mid" | "bid" | "ask"
    max_spread_usd: float = 1.00
    min_session_ticks: int = 5_000
    min_rows_for_shape: int = 5


@dataclass(frozen=True)
class Histogram:
    """Volume per absolute price row. `volumes[i]` is row `min_row + i`."""
    min_row: int
    volumes: np.ndarray
    row_size: float
    accepted: int
    rejected: int

    @property
    def total(self) -> float:
        return float(self.volumes.sum())

    @property
    def max_row(self) -> int:
        return self.min_row + self.volumes.size - 1


# Rows are snapped to the integer when price/row_size lands within this many
# rows of it. In price terms that is 1e-6 * row_size = 1e-7 USD at the default
# -- orders of magnitude below any real gold quote granularity.
_SNAP = 1e-6


def _rows_from_quotients(q):
    """Floor, but snap to the integer when we are within _SNAP of one.

    Why this is not a plain floor: 4493.20 / 0.10 evaluates to
    44931.99999999999 in IEEE754, so floor() drops the price a whole row --
    and it does so precisely at the round numbers price gravitates to. The
    MQL5 port must apply the identical rule or the two silently disagree at
    exactly those prices.
    """
    r = np.round(q)
    return np.where(np.abs(q - r) < _SNAP, r, np.floor(q)).astype(np.int64)


def row_index(price: float, row_size: float) -> int:
    """Absolute grid: a price maps to the same row in every session, forever.

    Deliberately NOT session-anchored. Anchoring to a session low shifts the
    grid by a random sub-cent offset each day, so the same price falls in a
    different row on different days and VPOCs stop being comparable.
    """
    return int(_rows_from_quotients(np.asarray(price, dtype=float) / row_size))


def row_low(row: int, row_size: float) -> float:
    return row * row_size


def row_mid(row: int, row_size: float) -> float:
    return (row + 0.5) * row_size


def row_high(row: int, row_size: float) -> float:
    return (row + 1) * row_size


def _tick_prices(bid: np.ndarray, ask: np.ndarray, mode: str) -> np.ndarray:
    if mode == "mid":
        return (bid + ask) / 2.0
    if mode == "bid":
        return bid
    if mode == "ask":
        return ask
    raise ValueError(f"unknown tick_price_mode: {mode!r}")


def _histogram_from_rows(rows: np.ndarray, weights: np.ndarray, params: ProfileParams,
                         accepted: int, rejected: int) -> Histogram:
    if rows.size == 0:
        return Histogram(0, np.zeros(0), params.row_size, accepted, rejected)
    lo, hi = int(rows.min()), int(rows.max())
    volumes = np.zeros(hi - lo + 1, dtype=float)
    np.add.at(volumes, rows - lo, weights)
    return Histogram(lo, volumes, params.row_size, accepted, rejected)


def accumulate_ticks(bid: np.ndarray, ask: np.ndarray,
                     params: ProfileParams = ProfileParams()) -> Histogram:
    """Each accepted tick contributes weight 1.0 at its price.

    Ticks whose spread exceeds `max_spread_usd` are rejected: at the daily
    rollover and on news, gold's spread blows past $1 and the mid price lands
    in a row where nothing actually traded. Rejections are counted, never
    silently dropped.
    """
    bid = np.asarray(bid, dtype=float)
    ask = np.asarray(ask, dtype=float)
    valid = (bid > 0) & (ask > 0) & ((ask - bid) <= params.max_spread_usd)
    accepted = int(valid.sum())
    rejected = int(bid.size - accepted)
    prices = _tick_prices(bid[valid], ask[valid], params.tick_price_mode)
    rows = _rows_from_quotients(prices / params.row_size)
    return _histogram_from_rows(rows, np.ones(rows.size), params, accepted, rejected)


def accumulate_m1_bars(high: np.ndarray, low: np.ndarray, tick_volume: np.ndarray,
                       params: ProfileParams = ProfileParams()) -> Histogram:
    """Fallback when tick history is unavailable.

    Each bar's tick_volume is spread UNIFORMLY across the rows its high-low
    spans. Uniform is the standard and is reproducible; no OHLC weighting.
    """
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    tick_volume = np.asarray(tick_volume, dtype=float)
    all_rows: list[np.ndarray] = []
    all_w: list[np.ndarray] = []
    for h, lo_p, v in zip(high, low, tick_volume):
        r0 = row_index(lo_p, params.row_size)
        r1 = row_index(h, params.row_size)
        rows = np.arange(r0, r1 + 1, dtype=np.int64)
        all_rows.append(rows)
        all_w.append(np.full(rows.size, v / rows.size))
    if not all_rows:
        return Histogram(0, np.zeros(0), params.row_size, 0, 0)
    rows = np.concatenate(all_rows)
    weights = np.concatenate(all_w)
    n = int(high.size)
    return _histogram_from_rows(rows, weights, params, accepted=n, rejected=0)


@dataclass(frozen=True)
class Profile:
    hist: Histogram
    poc_row: int
    val_row: int
    vah_row: int
    vpoc: float
    val: float
    vah: float
    low: float
    high: float


def poc_index(volumes: np.ndarray) -> int:
    """Row of maximum volume. Ties resolve toward the middle of the occupied
    range (CQG rule), then to the lower index. Fully deterministic.

    Comparing indices is equivalent to comparing row mid prices, because rows
    are uniformly spaced — so this needs no row_size.
    """
    vmax = volumes.max()
    cands = np.flatnonzero(volumes == vmax)
    if cands.size == 1:
        return int(cands[0])
    occupied = np.flatnonzero(volumes > 0)
    mid = (occupied[0] + occupied[-1]) / 2.0
    # argmin returns the FIRST minimum, i.e. the lower index on a tie.
    return int(cands[int(np.argmin(np.abs(cands - mid)))])


def value_area(volumes: np.ndarray, poc_i: int, target_frac: float = 0.70,
               algorithm: str = "single_row") -> tuple[int, int]:
    """Expand from the POC until the band holds `target_frac` of total volume.

    "single_row" is the TradingView/CQG standard: absorb whichever adjacent row
    is larger; on a tie take the row nearer the POC; if equidistant take the
    higher row.

    "two_row" is the classic Steidlmayer/CBOT method used by Sierra Chart and
    ThinkOrSwim: compare the SUM of the two rows above against the two below and
    absorb the winning pair. On a tie it takes the upper pair.
    """
    n = volumes.size
    total = float(volumes.sum())
    if n == 0 or total <= 0:
        return poc_i, poc_i
    target = total * target_frac
    lo = hi = poc_i
    acc = float(volumes[poc_i])

    while acc < target:
        up_avail = hi + 1 < n
        dn_avail = lo - 1 >= 0
        if not up_avail and not dn_avail:
            break

        if algorithm == "single_row":
            if not dn_avail:
                take_up = True
            elif not up_avail:
                take_up = False
            else:
                above, below = volumes[hi + 1], volumes[lo - 1]
                if above != below:
                    take_up = above > below
                else:
                    d_up = (hi + 1) - poc_i
                    d_dn = poc_i - (lo - 1)
                    take_up = d_up <= d_dn          # equidistant -> upper row
            if take_up:
                hi += 1
                acc += float(volumes[hi])
            else:
                lo -= 1
                acc += float(volumes[lo])

        elif algorithm == "two_row":
            up_sum = float(volumes[hi + 1:hi + 3].sum()) if up_avail else -1.0
            dn_sum = float(volumes[max(lo - 2, 0):lo].sum()) if dn_avail else -1.0
            if not dn_avail or (up_avail and up_sum >= dn_sum):
                hi = min(hi + 2, n - 1)
            else:
                lo = max(lo - 2, 0)
            acc = float(volumes[lo:hi + 1].sum())

        else:
            raise ValueError(f"unknown va_algorithm: {algorithm!r}")

    return lo, hi


def build_profile(hist: Histogram, params: ProfileParams = ProfileParams()) -> Profile | None:
    """Resolve a histogram into levels. Returns None for an empty profile.

    Level definitions are explicit because platforms disagree:
      VPOC = mid of the POC row
      VAH  = UPPER edge of the highest absorbed row
      VAL  = LOWER edge of the lowest absorbed row
    so the band genuinely contains its >= target_frac of volume.
    """
    if hist.volumes.size == 0 or hist.total <= 0:
        return None
    poc_i = poc_index(hist.volumes)
    lo_i, hi_i = value_area(hist.volumes, poc_i, params.value_area_pct,
                            params.va_algorithm)
    rs = hist.row_size
    occupied = np.flatnonzero(hist.volumes > 0)
    return Profile(
        hist=hist,
        poc_row=hist.min_row + poc_i,
        val_row=hist.min_row + lo_i,
        vah_row=hist.min_row + hi_i,
        vpoc=row_mid(hist.min_row + poc_i, rs),
        val=row_low(hist.min_row + lo_i, rs),
        vah=row_high(hist.min_row + hi_i, rs),
        low=row_low(hist.min_row + int(occupied[0]), rs),
        high=row_high(hist.min_row + int(occupied[-1]), rs),
    )


class TickCursor:
    """Retain-and-replay cursor for incremental tick fetching.

    Why this exists. `CopyTicksRange` is INCLUSIVE on both `from_msc` and
    `to_msc`, and multiple DISTINCT ticks can share one millisecond. So the
    obvious cursor -- `from_msc = last_seen_msc` -- re-returns every tick at
    that millisecond on every refresh. On a 5s timer that is a compounding
    double-count which drags VPOC toward whatever price was busiest at the last
    refresh boundary. Skipping a single tick does not fix it, because the
    duplicates are genuinely different ticks.

    Invariant: nothing at or after `cursor_msc` has been processed.

    Each cycle processes only ticks strictly BELOW the batch's maximum
    millisecond and parks the cursor there, so the final partial millisecond is
    deferred one cycle -- irrelevant at a 5s cadence.

    Side benefit: this is inherently gap-healing. If the terminal disconnects
    the cursor does not advance, and the next successful call backfills the
    missed span with no special reconnect path.
    """

    def __init__(self, start_msc: int) -> None:
        self.cursor_msc = int(start_msc)

    def split(self, time_msc: np.ndarray) -> int:
        """Given a batch sorted ascending, return how many leading ticks are
        safe to process, and advance the cursor to the deferred boundary."""
        if time_msc.size == 0:
            return 0
        boundary = int(time_msc[-1])
        n_safe = int(np.searchsorted(time_msc, boundary, side="left"))
        self.cursor_msc = boundary
        return n_safe


@dataclass(frozen=True)
class NodeParams:
    """WARNING: these three thresholds are UNCALIBRATED display heuristics.

    Unlike the skew threshold in profile_context -- which is calibrated against
    a published base rate -- nothing was fitted to produce these. They are a
    reasonable default for reading a chart and nothing more. Do not treat them
    as validated parameters, and do not build a trading rule on them without
    taking it through the full backtest.md gate.
    """
    hvn_prominence_pct: float = 0.15
    lvn_ratio: float = 0.50
    min_separation_rows: int = 10


def find_nodes(prof: Profile, params: NodeParams = NodeParams()) -> tuple[list[float], list[float]]:
    """High- and low-volume nodes.

    LVN is the load-bearing one: the course material treats low-volume nodes as
    where absorption happens -- the thin gaps left in the auction. HVN is
    secondary context.
    """
    v = prof.hist.volumes
    rs = prof.hist.row_size
    n = v.size
    if n < 3:
        return [], []
    peak_floor = float(v.max()) * params.hvn_prominence_pct

    # Local maxima above the prominence floor, greedily thinned by separation.
    cands = [i for i in range(1, n - 1)
             if v[i] >= v[i - 1] and v[i] > v[i + 1] and v[i] >= peak_floor]
    cands.sort(key=lambda i: float(v[i]), reverse=True)
    kept: list[int] = []
    for i in cands:
        if all(abs(i - j) >= params.min_separation_rows for j in kept):
            kept.append(i)
    kept.sort()

    hvn = [row_mid(prof.hist.min_row + i, rs) for i in kept]

    lvn: list[float] = []
    for a, b in zip(kept, kept[1:]):
        seg = v[a + 1:b]
        if seg.size == 0:
            continue
        j = a + 1 + int(np.argmin(seg))
        if float(v[j]) <= params.lvn_ratio * min(float(v[a]), float(v[b])):
            lvn.append(row_mid(prof.hist.min_row + j, rs))

    return hvn, lvn


def naked_pocs(session_pocs: list[tuple[str, float, int]],
               bar_high: np.ndarray, bar_low: np.ndarray,
               bar_index_of_session_end: dict[str, int]) -> list[tuple[str, float]]:
    """POCs that no LATER bar has traded through.

    These are the "left-side levels" the methodology uses to judge whether a
    setup's risk-to-reward is viable. A bar whose [low, high] contains the POC
    price tags it -- touching an extreme exactly counts as a tag.
    """
    out: list[tuple[str, float]] = []
    for label, price, _row in session_pocs:
        start = bar_index_of_session_end.get(label, 0) + 1
        if start >= bar_high.size:
            out.append((label, price))
            continue
        hi = bar_high[start:]
        lo = bar_low[start:]
        if not np.any((lo <= price) & (hi >= price)):
            out.append((label, price))
    return out
