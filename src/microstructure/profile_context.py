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
