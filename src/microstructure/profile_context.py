"""
Auction Market Theory interpretation of a built volume profile.

`volume_profile.py` builds the object; this module reads it -- P/b/D shape,
open type, value migration, balance regime. Kept separate because the two have
genuinely different jobs and each stays small enough to hold in context.

Everything here DESCRIBES the auction that already happened. Nothing here
forecasts, and no label may be presented as a prediction.

Design: docs/superpowers/specs/2026-08-17-volume-profile-indicator-design.md
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .volume_profile import Histogram, Profile

SHAPE_P = "P"
SHAPE_B = "b"
SHAPE_D = "D"
SHAPE_UNKNOWN = "UNCLASSIFIED"


@dataclass(frozen=True)
class ContextParams:
    # The ONLY constant the shape classification turns on. Calibrated by
    # scripts/calibrate_profile_shape.py against Dalton's ~50% base rate for
    # balanced days. 0.0 is the UNCALIBRATED sentinel -- callers must surface it.
    skew_threshold: float = 0.0
    min_rows_for_shape: int = 5
    regime_min_elapsed_pct: float = 0.50


@dataclass(frozen=True)
class ShapeRead:
    shape: str
    skew: float | None
    poc_position: float
    va_width_frac: float
    upper_tail_frac: float
    lower_tail_frac: float


def profile_skew(hist: Histogram) -> float | None:
    """Standardised third central moment of the volume-weighted price
    distribution.

    Chosen over POC position because it is dimensionless (comparable across
    sessions, volatility regimes and instruments), uses the WHOLE distribution
    rather than a single argmax row that a handful of ticks can move, and has a
    natural zero -- a symmetric profile is exactly a D.

    Sign convention, stated because it is easy to invert:
        negative -> mass at HIGH prices, thin tail below -> P
        positive -> mass at LOW prices,  thin tail above -> b
    """
    total = float(hist.volumes.sum())
    if hist.volumes.size < 2 or total <= 0:
        return None
    rows = hist.min_row + np.arange(hist.volumes.size)
    mids = (rows + 0.5) * hist.row_size
    w = hist.volumes / total
    mean = float((w * mids).sum())
    var = float((w * (mids - mean) ** 2).sum())
    if var <= 0:
        return None
    sd = math.sqrt(var)
    m3 = float((w * (mids - mean) ** 3).sum())
    return m3 / (sd ** 3)


def classify_shape(prof: Profile, params: ContextParams = ContextParams()) -> ShapeRead:
    """P / b / D from skew alone, with the geometry reported alongside.

    Every non-degenerate session gets a letter. That is safe ONLY because the
    skew value travels with it -- a session at skew -0.02 is visibly marginal
    and one at -1.40 visibly is not. Marginality is read from the number, not
    hidden inside a fuzzy middle band.
    """
    hist = prof.hist
    span = prof.high - prof.low
    n_occupied = int(np.count_nonzero(hist.volumes))
    total = float(hist.volumes.sum())

    poc_position = (prof.vpoc - prof.low) / span if span > 0 else 0.0
    va_width_frac = (prof.vah - prof.val) / span if span > 0 else 0.0
    above = float(hist.volumes[prof.vah_row - hist.min_row + 1:].sum())
    below = float(hist.volumes[:prof.val_row - hist.min_row].sum())
    upper_tail_frac = above / total if total > 0 else 0.0
    lower_tail_frac = below / total if total > 0 else 0.0

    skew = profile_skew(hist)
    if skew is None or n_occupied < params.min_rows_for_shape:
        shape = SHAPE_UNKNOWN
        skew = None
    elif skew <= -params.skew_threshold:
        shape = SHAPE_P
    elif skew >= params.skew_threshold:
        shape = SHAPE_B
    else:
        shape = SHAPE_D

    return ShapeRead(shape=shape, skew=skew, poc_position=poc_position,
                     va_width_frac=va_width_frac,
                     upper_tail_frac=upper_tail_frac,
                     lower_tail_frac=lower_tail_frac)


REGIME_BY_SHAPE = {
    SHAPE_D: "BALANCED",
    SHAPE_P: "OUT_OF_BALANCE_UP",
    SHAPE_B: "OUT_OF_BALANCE_DOWN",
    SHAPE_UNKNOWN: "UNCLEAR",
}


def classify_open_type(open_price: float, prior: Profile) -> str:
    """Where today opened relative to yesterday's value area.

    Value-area boundaries are INCLUSIVE: an open exactly on VAH or VAL is
    inside value. The range boundaries are exclusive, so an open exactly on the
    prior high is above value but not above range.
    """
    if open_price > prior.high:
        return "OPEN_ABOVE_RANGE"
    if open_price < prior.low:
        return "OPEN_BELOW_RANGE"
    if open_price > prior.vah:
        return "OPEN_ABOVE_VA"
    if open_price < prior.val:
        return "OPEN_BELOW_VA"
    return "OPEN_INSIDE_VA"


def classify_value_migration(today: Profile, prior: Profile) -> str:
    """Today's value area against the prior session's.

    Containment is checked BEFORE direction, so an inside or engulfing day is
    never mislabelled as a drift.
    """
    if today.val >= prior.val and today.vah <= prior.vah:
        return "INSIDE"
    if today.val <= prior.val and today.vah >= prior.vah:
        return "ENGULFING"
    if today.val > prior.vah:
        return "HIGHER"
    if today.vah < prior.val:
        return "LOWER"
    return "OVERLAPPING_HIGHER" if today.vah > prior.vah else "OVERLAPPING_LOWER"


def classify_regime(shape: str, elapsed_pct: float, params: ContextParams,
                    is_developing: bool) -> str:
    """Balance vs out-of-balance -- the word that gates strategy family.

    A developing session reports FORMING until it is far enough along. Every
    session looks like a P or a b for its first couple of hours purely because
    it has only travelled one way so far; without this guard the live label
    would be confidently wrong every morning.

    This DESCRIBES the auction so far. It does not forecast.
    """
    if is_developing and elapsed_pct < params.regime_min_elapsed_pct:
        return "FORMING"
    return REGIME_BY_SHAPE.get(shape, "UNCLEAR")
