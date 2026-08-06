"""
The Python definition of the liquidity level set — pure functions, no I/O.

This module is load-bearing twice over: the MQL5 indicator re-implements it in
another language, and `scripts/check_liquidity_parity.py` diffs the two against
what is written here. Anything ambiguous in this file becomes a silent drift bug
on the chart, so every rule below is stated in a form that has exactly one
possible implementation.

Detection timeframe is 15m and the scan window is `scan_bars`; both are fixed to
the calibration values and must be identical on both sides of the port.

⚠️ Not to be confused with `src/monitoring/liquidity_levels.py`, an unrelated
older module of ICT reference levels used for stop adjustment.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURE_NAMES: list[str] = [
    "log_dist_atr",
    "n_closer_same_side",
    "side_up",
    "type_equal",
    "type_session",
    "log_age_bars",
    "touch_count",
    "trend_align",
    "atr_pctile",
    "session_london",
    "session_ny",
    "dist_x_atrpctile",
]

EQUAL_KINDS = ("equal_highs", "equal_lows")
SWING_KINDS = ("swing_high", "swing_low")
SESSION_KINDS = ("asia_high", "asia_low", "london_high", "london_low",
                 "ny_high", "ny_low", "pd_high", "pd_low", "pw_high", "pw_low")

# Display tags, used by the report and mirrored by the indicator's labels.
KIND_TAGS = {
    "swing_high": "SwH", "swing_low": "SwL",
    "equal_highs": "EQH", "equal_lows": "EQL",
    "asia_high": "ASIAH", "asia_low": "ASIAL",
    "london_high": "LONH", "london_low": "LONL",
    "ny_high": "NYH", "ny_low": "NYL",
    "pd_high": "PDH", "pd_low": "PDL",
    "pw_high": "PWH", "pw_low": "PWL",
}


@dataclass(frozen=True)
class LevelParams:
    pivot_n: int = 5
    eq_tol_atr: float = 0.10
    merge_tol_atr: float = 0.10
    levels_per_side: int = 6
    max_dist_atr: float = 8.0
    forming_band_atr: float = 0.25
    touch_band_atr: float = 0.25
    scan_bars: int = 1000
    touch_lookback: int = 500
    atr_period: int = 14
    atr_pctile_window: int = 500
    ema_period: int = 50
    ema_slope_bars: int = 3


DEFAULTS = LevelParams()


@dataclass(frozen=True)
class Level:
    price: float
    kind: str
    formation_idx: int   # integer bar index into the frame the level was built from
    side: str            # "up" (above close) | "down" (below close)


# --------------------------------------------------------------- primitives

def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               period: int = 14) -> np.ndarray:
    """Wilder ATR seeded with bar 0's own true range.

    Seeding matters for parity: MQL5's built-in iATR seeds with a simple mean of
    the first `period` true ranges, so the indicator computes ATR with this exact
    recursion instead of calling iATR. After the 1000-bar warmup the two agree to
    far below the 1e-4 parity tolerance anyway (0.9286^1000 ~ 1e-33), but only one
    of them is written down, and this is it.
    """
    n = len(high)
    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    if n > 1:
        prev_close = close[:-1]
        tr[1:] = np.maximum(high[1:] - low[1:],
                            np.maximum(np.abs(high[1:] - prev_close),
                                       np.abs(low[1:] - prev_close)))
    out = np.empty(n, dtype=float)
    alpha = 1.0 / period
    acc = tr[0]
    out[0] = acc
    for i in range(1, n):
        acc += alpha * (tr[i] - acc)
        out[i] = acc
    return out


def ema(values: np.ndarray, period: int) -> np.ndarray:
    """EMA seeded with the first value (matches MQL5's manual recursion)."""
    n = len(values)
    out = np.empty(n, dtype=float)
    alpha = 2.0 / (period + 1.0)
    acc = float(values[0])
    out[0] = acc
    for i in range(1, n):
        acc += alpha * (float(values[i]) - acc)
        out[i] = acc
    return out


def rolling_pctile(values: np.ndarray, window: int) -> np.ndarray:
    """Fraction of the trailing `window` values (inclusive) at or below each value.

    Range (0, 1]. Windows shorter than `window` at the head use what exists.
    """
    n = len(values)
    out = np.empty(n, dtype=float)
    for i in range(n):
        lo = max(0, i - window + 1)
        w = values[lo:i + 1]
        out[i] = float(np.count_nonzero(w <= values[i])) / float(len(w))
    return out


def pivot_masks(high: np.ndarray, low: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """N-bar fractal pivots: bar i is a swing high when high[i] == max(high[i-n..i+n]).

    Confirms n bars late, so a caller at snapshot t may only use pivots with i+n <= t.
    Ties are allowed by `==` (a flat double top marks both bars); the un-swept rule
    then removes the earlier one, since ties sweep.
    """
    size = len(high)
    is_ph = np.zeros(size, dtype=bool)
    is_pl = np.zeros(size, dtype=bool)
    if size < 2 * n + 1:
        return is_ph, is_pl
    for i in range(n, size - n):
        w_hi = high[i - n:i + n + 1]
        if high[i] >= w_hi.max():
            is_ph[i] = True
        w_lo = low[i - n:i + n + 1]
        if low[i] <= w_lo.min():
            is_pl[i] = True
    return is_ph, is_pl


def next_ge_index(values: np.ndarray) -> np.ndarray:
    """For each i, the first j > i with values[j] >= values[i], else len(values).

    O(n) monotonic stack. This is the sweep index for swing highs: ties sweep, per
    the spec's "trades at or above it".
    """
    n = len(values)
    out = np.full(n, n, dtype=np.int64)
    stack: list[int] = []
    for j in range(n):
        while stack and values[j] >= values[stack[-1]]:
            out[stack.pop()] = j
        stack.append(j)
    return out


def next_le_index(values: np.ndarray) -> np.ndarray:
    """For each i, the first j > i with values[j] <= values[i], else len(values)."""
    n = len(values)
    out = np.full(n, n, dtype=np.int64)
    stack: list[int] = []
    for j in range(n):
        while stack and values[j] <= values[stack[-1]]:
            out[stack.pop()] = j
        stack.append(j)
    return out


# ----------------------------------------------------------------- context

@dataclass
class FrameContext:
    """Everything derived once per frame, so a snapshot costs O(scan_bars).

    The arrays are all length-n and index-aligned to `bars`. `ph_death[i]` is the
    bar index that swept the swing high at i (n if never), which is what makes the
    un-swept test O(1) per pivot.
    """
    bars: pd.DataFrame
    ts: np.ndarray            # datetime64[ns, UTC] index values
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    atr_pctile: np.ndarray
    ema_slope: np.ndarray     # ema[i] - ema[i - slope_bars], 0.0 at the head
    is_ph: np.ndarray
    is_pl: np.ndarray
    ph_death: np.ndarray
    pl_death: np.ndarray
    hour: np.ndarray          # UTC hour of each bar
    day_id: np.ndarray        # UTC calendar date ordinal
    week_id: np.ndarray       # ISO year*100 + ISO week


def build_context(bars: pd.DataFrame, params: LevelParams = DEFAULTS) -> FrameContext:
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars must be indexed by a UTC DatetimeIndex")
    if bars.index.tz is None:
        raise ValueError("bars index must be timezone-aware (UTC)")
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    atr = wilder_atr(high, low, close, params.atr_period)
    e = ema(close, params.ema_period)
    slope = np.zeros(len(e), dtype=float)
    k = params.ema_slope_bars
    if len(e) > k:
        slope[k:] = e[k:] - e[:-k]
    is_ph, is_pl = pivot_masks(high, low, params.pivot_n)
    idx = bars.index
    iso = idx.isocalendar()
    return FrameContext(
        bars=bars,
        ts=idx.values,
        high=high, low=low, close=close,
        atr=atr,
        atr_pctile=rolling_pctile(atr, params.atr_pctile_window),
        ema_slope=slope,
        is_ph=is_ph, is_pl=is_pl,
        ph_death=next_ge_index(high),
        pl_death=next_le_index(low),
        hour=idx.hour.to_numpy(dtype=np.int64),
        day_id=np.asarray([d.toordinal() for d in idx.date], dtype=np.int64),
        week_id=(np.asarray(iso.year, dtype=np.int64) * 100
                 + np.asarray(iso.week, dtype=np.int64)),
    )


def live_swings(ctx: FrameContext, t: int, params: LevelParams = DEFAULTS) -> list[Level]:
    """Confirmed, un-swept solo swing pivots visible at snapshot bar `t`.

    A pivot at bar i qualifies when it has confirmed (i + pivot_n <= t), it lies in
    the scan window (i > t - scan_bars), and nothing has swept it yet (death > t).
    """
    n = params.pivot_n
    lo = max(0, t - params.scan_bars + 1)
    hi = t - n                       # inclusive: last bar that can have confirmed
    out: list[Level] = []
    if hi < lo:
        return out
    c = ctx.close[t]
    for i in range(lo, hi + 1):
        if ctx.is_ph[i] and ctx.ph_death[i] > t:
            p = float(ctx.high[i])
            if p != c:
                out.append(Level(p, "swing_high", i, "up" if p > c else "down"))
        if ctx.is_pl[i] and ctx.pl_death[i] > t:
            p = float(ctx.low[i])
            if p != c:
                out.append(Level(p, "swing_low", i, "up" if p > c else "down"))
    return out


# ---------------------------------------------------------------- clustering

def _cluster_one_side(levels: list[Level], tol: float, eq_kind: str,
                      take_max: bool, close: float) -> list[Level]:
    """Chain-linkage grouping of a single pivot family, ascending by price.

    Chain linkage (break when the gap to the *previous* member exceeds tol) rather
    than centroid linkage: it is order-independent given a sorted input and it is
    trivially reproducible in MQL5, which matters more here than cluster elegance.
    """
    out: list[Level] = []
    group: list[Level] = []

    def flush() -> None:
        if not group:
            return
        if len(group) >= 2:
            price = max(g.price for g in group) if take_max else min(g.price for g in group)
            out.append(Level(float(price), eq_kind,
                             max(g.formation_idx for g in group),
                             "up" if price > close else "down"))
        else:
            g = group[0]
            out.append(Level(g.price, g.kind, g.formation_idx,
                             "up" if g.price > close else "down"))

    for lv in sorted(levels, key=lambda x: (x.price, x.formation_idx)):
        if group and (lv.price - group[-1].price) > tol:
            flush()
            group = []
        group.append(lv)
    flush()
    return out


def cluster_equal(levels: list[Level], tol: float, close: float) -> list[Level]:
    """Collapse near-equal solo swings into equal_highs / equal_lows.

    A cluster of 2+ is priced at the extreme — the price at which every stop in the
    cluster is actually taken — not the mean. Non-swing kinds pass through untouched.
    """
    highs = [lv for lv in levels if lv.kind == "swing_high"]
    lows = [lv for lv in levels if lv.kind == "swing_low"]
    rest = [lv for lv in levels if lv.kind not in SWING_KINDS]
    out = _cluster_one_side(highs, tol, "equal_highs", True, close)
    out += _cluster_one_side(lows, tol, "equal_lows", False, close)
    out += rest
    return out


# ------------------------------------------------------- session / period

# UTC session windows, half-open [start, end). The Asia window matches what
# london_breakout already uses; London/NY overlap 13:00-16:00 by design.
SESSIONS = (
    ("asia", 0, 7),
    ("london", 7, 16),
    ("ny", 13, 21),
)


def _session_mask(hour: np.ndarray, start: int, end: int) -> np.ndarray:
    return (hour >= start) & (hour < end)


def _group_runs(ids: np.ndarray, lo: int, hi: int) -> list[tuple[int, int]]:
    """Contiguous [start, end] index runs of equal non-negative id in [lo, hi]."""
    runs: list[tuple[int, int]] = []
    i = lo
    while i <= hi:
        if ids[i] < 0:
            i += 1
            continue
        j = i
        while j + 1 <= hi and ids[j + 1] == ids[i]:
            j += 1
        runs.append((i, j))
        i = j + 1
    return runs


def _extreme_level(ctx: FrameContext, lo: int, hi: int, t: int, want_high: bool,
                   kind: str, forming: bool, params: LevelParams) -> Level | None:
    """Build one extreme level from bars [lo, hi], applying un-swept and forming rules."""
    if hi < lo:
        return None
    if want_high:
        seg = ctx.high[lo:hi + 1]
        k = int(np.argmax(seg))
        price = float(seg[k])
        # un-swept: nothing after the extreme bar, up to t, may have reached it
        after = ctx.high[lo + k + 1:t + 1]
        if after.size and float(after.max()) >= price:
            return None
    else:
        seg = ctx.low[lo:hi + 1]
        k = int(np.argmin(seg))
        price = float(seg[k])
        after = ctx.low[lo + k + 1:t + 1]
        if after.size and float(after.min()) <= price:
            return None
    c = ctx.close[t]
    if price == c:
        return None
    if forming:
        # A forming extreme sitting on top of price is not a pool, it IS price.
        if abs(price - c) < params.forming_band_atr * ctx.atr[t]:
            return None
    return Level(price, kind, lo + k, "up" if price > c else "down")


def session_levels(ctx: FrameContext, t: int,
                   params: LevelParams = DEFAULTS) -> list[Level]:
    """Prior-completed and forming session extremes plus prior day/week extremes."""
    lo = max(0, t - params.scan_bars + 1)
    out: list[Level] = []

    for name, h0, h1 in SESSIONS:
        mask = _session_mask(ctx.hour, h0, h1)
        ids = np.where(mask, ctx.day_id, -1)
        runs = _group_runs(ids, lo, t)
        if not runs:
            continue
        current = runs[-1] if runs[-1][1] == t else None
        prior = runs[-2] if current is not None and len(runs) >= 2 else (
            runs[-1] if current is None else None)
        if prior is not None:
            p_lo, p_hi = prior
            for want_high, suffix in ((True, "high"), (False, "low")):
                lv = _extreme_level(ctx, p_lo, p_hi, t, want_high,
                                    f"{name}_{suffix}", False, params)
                if lv is not None:
                    out.append(lv)
        if current is not None:
            c_lo, c_hi = current
            for want_high, suffix in ((True, "high"), (False, "low")):
                lv = _extreme_level(ctx, c_lo, c_hi, t, want_high,
                                    f"{name}_{suffix}", True, params)
                if lv is not None:
                    out.append(lv)

    for ids, prefix in ((ctx.day_id, "pd"), (ctx.week_id, "pw")):
        runs = _group_runs(ids, lo, t)
        if len(runs) < 2:
            continue
        p_lo, p_hi = runs[-2]           # the last fully completed period
        for want_high, suffix in ((True, "high"), (False, "low")):
            lv = _extreme_level(ctx, p_lo, p_hi, t, want_high,
                                f"{prefix}_{suffix}", False, params)
            if lv is not None:
                out.append(lv)

    return out


# ---------------------------------------------------------------- choice set

def kind_priority(kind: str) -> int:
    """Lower wins a merge: equal-cluster > session/period extreme > solo swing."""
    if kind in EQUAL_KINDS:
        return 0
    if kind in SESSION_KINDS:
        return 1
    return 2


def _merge_by_priority(levels: list[Level], tol: float, close: float) -> list[Level]:
    """Collapse levels within `tol` of each other, keeping the highest priority.

    Ascending sweep with chain linkage, same shape as _cluster_one_side so the two
    behave alike. Within a group the winner is (priority, formation_idx, price) —
    fully deterministic, which is what the parity harness needs.
    """
    out: list[Level] = []
    group: list[Level] = []

    def flush() -> None:
        if not group:
            return
        best = min(group, key=lambda lv: (kind_priority(lv.kind), lv.formation_idx, lv.price))
        out.append(Level(best.price, best.kind, best.formation_idx,
                         "up" if best.price > close else "down"))

    for lv in sorted(levels, key=lambda x: (x.price, kind_priority(x.kind), x.formation_idx)):
        if group and (lv.price - group[-1].price) > tol:
            flush()
            group = []
        group.append(lv)
    flush()
    return out


def build_choice_set(ctx: FrameContext, t: int,
                     params: LevelParams = DEFAULTS) -> list[Level]:
    """The competing alternatives at snapshot bar `t`.

    Returned sorted by (up-side first, then ascending distance from close, then
    price). That ordering is a parity contract — the MQL5 export writes rows in
    this sequence and check_liquidity_parity.py compares positionally.
    """
    atr = float(ctx.atr[t])
    if not np.isfinite(atr) or atr <= 0.0:
        return []
    close = float(ctx.close[t])

    swings = live_swings(ctx, t, params)
    levels = cluster_equal(swings, params.eq_tol_atr * atr, close)
    levels += session_levels(ctx, t, params)

    max_dist = params.max_dist_atr * atr
    levels = [lv for lv in levels if 0.0 < abs(lv.price - close) <= max_dist]
    levels = _merge_by_priority(levels, params.merge_tol_atr * atr, close)

    ups = sorted([lv for lv in levels if lv.side == "up"],
                 key=lambda lv: (lv.price - close, lv.price))[:params.levels_per_side]
    downs = sorted([lv for lv in levels if lv.side == "down"],
                   key=lambda lv: (close - lv.price, lv.price))[:params.levels_per_side]
    return ups + downs


# ------------------------------------------------------------------ features

def _touch_count(ctx: FrameContext, t: int, lv: Level, band: float,
                 lookback: int) -> float:
    """Prior approaches within `band` that did not breach the level."""
    start = max(lv.formation_idx + 1, t - lookback + 1, 0)
    if start > t:
        return 0.0
    if lv.side == "up":
        seg = ctx.high[start:t + 1]
        return float(np.count_nonzero((seg >= lv.price - band) & (seg < lv.price)))
    seg = ctx.low[start:t + 1]
    return float(np.count_nonzero((seg <= lv.price + band) & (seg > lv.price)))


def feature_matrix(ctx: FrameContext, t: int, levels: list[Level],
                   params: LevelParams = DEFAULTS) -> np.ndarray:
    """Raw (un-z-scored) feature rows, one per level, in FEATURE_NAMES order."""
    k = len(levels)
    X = np.zeros((k, len(FEATURE_NAMES)), dtype=float)
    if k == 0:
        return X

    atr = float(ctx.atr[t])
    close = float(ctx.close[t])
    atr_pct = float(ctx.atr_pctile[t])
    band = params.touch_band_atr * atr
    slope = float(ctx.ema_slope[t])
    hour = int(ctx.hour[t])
    s_london = 1.0 if 7 <= hour < 16 else 0.0
    s_ny = 1.0 if 13 <= hour < 21 else 0.0

    dists = np.array([abs(lv.price - close) for lv in levels], dtype=float)
    for i, lv in enumerate(levels):
        up = lv.side == "up"
        n_closer = float(sum(1 for j, other in enumerate(levels)
                             if j != i and other.side == lv.side and dists[j] < dists[i]))
        log_dist = float(np.log1p(dists[i] / atr))
        aligned = 1.0 if ((up and slope > 0.0) or ((not up) and slope < 0.0)) else 0.0
        X[i, 0] = log_dist
        X[i, 1] = n_closer
        X[i, 2] = 1.0 if up else 0.0
        X[i, 3] = 1.0 if lv.kind in EQUAL_KINDS else 0.0
        X[i, 4] = 1.0 if lv.kind in SESSION_KINDS else 0.0
        X[i, 5] = float(np.log1p(max(0, t - lv.formation_idx)))
        X[i, 6] = _touch_count(ctx, t, lv, band, params.touch_lookback)
        X[i, 7] = aligned
        X[i, 8] = atr_pct
        X[i, 9] = s_london
        X[i, 10] = s_ny
        X[i, 11] = log_dist * atr_pct
    return X


# ---------------------------------------------------------- labels / dataset

def first_touch(ctx: FrameContext, t: int, levels: list[Level],
                horizon: int = 96) -> int:
    """Index of the first level touched in [t+1, t+horizon], else -1.

    Wicks count — that is the stop run. When one bar spans several levels the
    NEARER one is credited: within-bar ordering is unknowable from OHLC, and
    crediting the farther level would bias the model toward distance.
    """
    if not levels:
        return -1
    n = len(ctx.close)
    end = min(n - 1, t + horizon)
    if end <= t:
        return -1
    close = float(ctx.close[t])
    prices = np.array([lv.price for lv in levels], dtype=float)
    up = np.array([lv.side == "up" for lv in levels], dtype=bool)
    dist = np.abs(prices - close)
    for j in range(t + 1, end + 1):
        hi, lo = ctx.high[j], ctx.low[j]
        hit = np.where(up, hi >= prices, lo <= prices)
        if hit.any():
            cand = np.flatnonzero(hit)
            return int(cand[np.argmin(dist[cand])])
    return -1


def build_dataset(bars: pd.DataFrame, params: LevelParams = DEFAULTS,
                  horizon: int = 96, warmup: int | None = None,
                  stride: int = 1, progress=None):
    """Walk the frame, building one choice set + label per snapshot bar.

    Returns (ChoiceData, meta) where meta has one row per (snapshot, level) aligned
    positionally with ChoiceData.X — the report and the parity harness both need the
    human-readable side of each row.

    `warmup` defaults to params.scan_bars: the pivot scan, the ATR percentile window
    and a full prior week all have to be populated before a snapshot means anything.
    """
    from src.microstructure.liquidity_race import ChoiceData   # local: avoid a cycle

    ctx = build_context(bars, params)
    n = len(bars)
    if warmup is None:
        warmup = params.scan_bars
    last = n - horizon - 1

    rows: list[np.ndarray] = []
    snap_ids: list[int] = []
    chosen: list[int] = []
    day_ids: list[int] = []
    meta_rows: list[dict] = []
    cursor = 0
    snap = 0

    for t in range(warmup, last + 1, stride):
        levels = build_choice_set(ctx, t, params)
        if not levels:
            continue
        X = feature_matrix(ctx, t, levels, params)
        pick = first_touch(ctx, t, levels, horizon)
        rows.append(X)
        k = len(levels)
        snap_ids.extend([snap] * k)
        chosen.append(-1 if pick < 0 else cursor + pick)
        day_ids.append(int(ctx.day_id[t]))
        ts = bars.index[t]
        close = float(ctx.close[t])
        atr = float(ctx.atr[t])
        for lv in levels:
            meta_rows.append({
                "snap_id": snap,
                "snapshot_ts": ts,
                "price": lv.price,
                "kind": lv.kind,
                "formation_idx": lv.formation_idx,
                "formation_ts": bars.index[lv.formation_idx],
                "side": lv.side,
                "dist_atr": (lv.price - close) / atr,
            })
        cursor += k
        snap += 1
        if progress is not None and snap % 5000 == 0:
            progress(snap, t, n)

    if not rows:
        empty = np.zeros((0, len(FEATURE_NAMES)))
        return (ChoiceData(empty, np.zeros(0, np.int64), np.zeros(0, np.int64),
                           np.zeros(0, np.int64)), pd.DataFrame(columns=[
                               "snap_id", "snapshot_ts", "price", "kind",
                               "formation_idx", "formation_ts", "side", "dist_atr"]))

    data = ChoiceData(X=np.vstack(rows),
                      snap_id=np.array(snap_ids, dtype=np.int64),
                      chosen=np.array(chosen, dtype=np.int64),
                      day_id=np.array(day_ids, dtype=np.int64))
    return data, pd.DataFrame(meta_rows)
