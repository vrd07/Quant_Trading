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
