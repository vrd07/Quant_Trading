# Volume Profile Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an XAUUSD volume profile indicator that marks VAH/VAL/VPOC with named labels on the live MT5 chart, plus the Auction Market Theory context read (P/b/D shape, open type, balance regime, initial balance, composite), with a Python reference definition and a parity harness proving the two agree.

**Architecture:** Three units. `src/microstructure/volume_profile.py` builds the profile object (grid, tick accumulation, POC, value area, nodes) and `src/microstructure/profile_context.py` interprets it (skew, shape, open type, migration, regime) — both pure, no I/O, no MT5 import. `mt5_indicators/GoldenChart_VolumeProfile.mq5` is a port of both. `scripts/check_volume_profile_parity.py` proves the port agrees by recomputing every derived value from the indicator's own exported histogram.

**Tech Stack:** Python 3 (numpy, pandas, pytest), MQL5. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-17-volume-profile-indicator-design.md`

> **Refinement of spec §21.** The spec lists one Python module. This plan splits it into `volume_profile.py` (builds the profile) and `profile_context.py` (interprets it). The boundary is crisp — `volume_profile.py` has no concept of "P/b/D" — and it keeps each file small enough to hold in context. Everything else in §21 is unchanged.

## Global Constraints

Copied verbatim from the spec. Every task's requirements implicitly include these.

- **Row grid is absolute**, never session-anchored: `row_index(p) = floor(p / row_size)`. (spec §5)
- **Default row size `0.10`** USD. Value area **`0.70`**. (spec §5, §8.2)
- **All distance inputs are in price units (USD), never "points"** — XAUUSD quotes at 2 or 3 digits depending on broker, so `_Point` is ambiguous. (spec §6.1)
- **Volume is tick density, never traded volume.** No UI text may imply contracts. (spec §1.1)
- **No footprint, delta, CVD, or tape-speed surrogate.** Deliberately absent. (spec §2, §22)
- **MQL5 object prefix `GC_VP_`**, all removed in `OnDeinit`. (spec §16)
- **Parity tolerances:** levels and labels exact, volumes `1e-4`. Do not relax them to make it pass. (spec §19)
- **`pytest.approx` uses `max(rel*expected, 1e-12)`** — use `abs=0.0` on any near-zero comparison or it passes vacuously. (spec §20)
- **Scope boundary:** neither Python module may import `src.strategies`, `src.risk`, `src.execution`, `src.portfolio`, `src.connectors`. (spec §3)
- **Sign convention:** negative skew = mass at high prices = **P** (bullish). Positive skew = mass at low prices = **b** (bearish). A published script (BackQuant) reads this geometry the opposite way; follow the spec. (spec §9.1)

---

### Task 1: Profile grid, tick accumulation, and the scope boundary

**Files:**
- Create: `src/microstructure/volume_profile.py`
- Create: `tests/unit/test_volume_profile.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ProfileParams` (frozen dataclass), `Histogram` (frozen dataclass with fields `min_row: int`, `volumes: np.ndarray`, `row_size: float`, `accepted: int`, `rejected: int`), `row_low(row: int, row_size: float) -> float`, `row_mid(row, row_size) -> float`, `row_high(row, row_size) -> float`, `row_index(price: float, row_size: float) -> int`, `accumulate_ticks(bid: np.ndarray, ask: np.ndarray, params: ProfileParams) -> Histogram`, `accumulate_m1_bars(high, low, tick_volume, params) -> Histogram`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_volume_profile.py`:

```python
"""Unit tests for src/microstructure/volume_profile.py — synthetic inputs only."""
import numpy as np
import pytest

from src.microstructure import volume_profile as vp


class TestGrid:
    def test_row_index_is_absolute_not_session_anchored(self):
        # The same price must land in the same row regardless of what else traded.
        assert vp.row_index(4493.15, 0.10) == vp.row_index(4493.15, 0.10)
        assert vp.row_index(4493.15, 0.10) == 44931
        assert vp.row_index(4493.19, 0.10) == 44931
        assert vp.row_index(4493.20, 0.10) == 44932

    def test_price_exactly_on_a_row_boundary_goes_to_the_upper_row(self):
        assert vp.row_index(4493.20, 0.10) == 44932
        assert vp.row_low(44932, 0.10) == pytest.approx(4493.20, abs=1e-9)

    def test_row_edges_and_mid(self):
        assert vp.row_low(44931, 0.10) == pytest.approx(4493.10, abs=1e-9)
        assert vp.row_mid(44931, 0.10) == pytest.approx(4493.15, abs=1e-9)
        assert vp.row_high(44931, 0.10) == pytest.approx(4493.20, abs=1e-9)


class TestTickAccumulation:
    def test_each_tick_adds_one_at_its_mid_price(self):
        bid = np.array([100.00, 100.00, 100.20])
        ask = np.array([100.02, 100.02, 100.22])
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams(row_size=0.10))
        # mids are 100.01, 100.01, 100.21 -> rows 1000, 1000, 1002
        assert h.accepted == 3
        assert h.rejected == 0
        assert h.volumes.sum() == pytest.approx(3.0, abs=0.0)
        assert h.volumes[vp.row_index(100.01, 0.10) - h.min_row] == pytest.approx(2.0, abs=0.0)

    def test_wide_spread_ticks_are_rejected_and_counted(self):
        bid = np.array([100.00, 100.00])
        ask = np.array([100.02, 101.50])   # second spread is 1.50 > 1.00
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams(row_size=0.10,
                                                          max_spread_usd=1.00))
        assert h.accepted == 1
        assert h.rejected == 1
        assert h.volumes.sum() == pytest.approx(1.0, abs=0.0)

    def test_sum_of_rows_always_equals_accepted(self):
        rng = np.random.default_rng(7)
        bid = 4000 + rng.normal(0, 5, 5000)
        ask = bid + rng.uniform(0.01, 0.30, 5000)
        h = vp.accumulate_ticks(bid, ask, vp.ProfileParams())
        assert h.volumes.sum() == pytest.approx(float(h.accepted), abs=0.0)

    def test_bid_and_ask_price_modes(self):
        bid = np.array([100.00])
        ask = np.array([100.40])
        hb = vp.accumulate_ticks(bid, ask, vp.ProfileParams(tick_price_mode="bid"))
        ha = vp.accumulate_ticks(bid, ask, vp.ProfileParams(tick_price_mode="ask"))
        assert hb.min_row == vp.row_index(100.00, 0.10)
        assert ha.min_row == vp.row_index(100.40, 0.10)


class TestM1Fallback:
    def test_bar_volume_spreads_uniformly_across_touched_rows(self):
        high = np.array([100.25])
        low = np.array([100.00])
        vol = np.array([300.0])
        h = vp.accumulate_m1_bars(high, low, vol, vp.ProfileParams(row_size=0.10))
        # rows 1000, 1001, 1002 -> three rows, 100 each
        assert h.volumes.size == 3
        assert h.volumes == pytest.approx([100.0, 100.0, 100.0], abs=1e-9)
        assert h.volumes.sum() == pytest.approx(300.0, abs=1e-9)

    def test_single_row_bar_gets_all_its_volume(self):
        h = vp.accumulate_m1_bars(np.array([100.05]), np.array([100.01]),
                                  np.array([42.0]), vp.ProfileParams(row_size=0.10))
        assert h.volumes.size == 1
        assert h.volumes[0] == pytest.approx(42.0, abs=1e-9)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_volume_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.microstructure.volume_profile'`

- [ ] **Step 3: Write the implementation**

Create `src/microstructure/volume_profile.py`:

```python
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


def row_index(price: float, row_size: float) -> int:
    """Absolute grid: a price maps to the same row in every session, forever.

    Deliberately NOT session-anchored. Anchoring to a session low shifts the
    grid by a random sub-cent offset each day, so the same price falls in a
    different row on different days and VPOCs stop being comparable.
    """
    return int(np.floor(price / row_size))


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
    rows = np.floor(prices / params.row_size).astype(np.int64)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_volume_profile.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Add the scope-boundary test**

Append to `tests/unit/test_volume_profile.py`:

```python
from pathlib import Path


class TestScopeBoundary:
    """The spec: chart and research only. Nothing crosses into the trading path."""

    _MODULES = (
        "src/microstructure/volume_profile.py",
        "src/microstructure/profile_context.py",
        "scripts/check_volume_profile_parity.py",
        "scripts/calibrate_profile_shape.py",
    )
    _FORBIDDEN = ("src.strategies", "src.risk", "src.execution",
                  "src.portfolio", "src.connectors")

    def _root(self):
        return Path(__file__).parent.parent.parent

    def test_profile_modules_do_not_import_the_trading_path(self):
        for rel in self._MODULES:
            path = self._root() / rel
            if not path.exists():      # later tasks create these
                continue
            src = path.read_text()
            for mod in self._FORBIDDEN:
                assert mod not in src, f"{rel} must not reference {mod}"

    def test_the_trading_path_does_not_import_the_profile_modules(self):
        root = self._root()
        for sub in ("src/strategies", "src/risk", "src/execution"):
            for py in (root / sub).rglob("*.py"):
                src = py.read_text()
                assert "volume_profile" not in src, f"{py} must not import volume_profile"
                assert "profile_context" not in src, f"{py} must not import profile_context"
```

- [ ] **Step 6: Run the full test file**

Run: `pytest tests/unit/test_volume_profile.py -v`
Expected: PASS (13 tests)

- [ ] **Step 7: Commit**

```bash
git add src/microstructure/volume_profile.py tests/unit/test_volume_profile.py
git commit -m "feat(profile): absolute-grid binning and tick accumulation

Grid is anchored to absolute price, not the session low, so a given price
lands in the same row every session and VPOCs stay comparable.

Spread filter rejects quote artifacts at rollover and on news, and counts
them rather than dropping them silently."
```

---

### Task 2: POC and value area

**Files:**
- Modify: `src/microstructure/volume_profile.py`
- Modify: `tests/unit/test_volume_profile.py`

**Interfaces:**
- Consumes: `Histogram`, `ProfileParams`, `row_low`, `row_mid`, `row_high` from Task 1.
- Produces: `poc_index(volumes: np.ndarray) -> int`, `value_area(volumes: np.ndarray, poc_i: int, target_frac: float, algorithm: str) -> tuple[int, int]`, `Profile` (frozen dataclass with fields `hist: Histogram`, `poc_row: int`, `val_row: int`, `vah_row: int`, `vpoc: float`, `val: float`, `vah: float`, `low: float`, `high: float`), `build_profile(hist: Histogram, params: ProfileParams) -> Profile | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_volume_profile.py`:

```python
def hist_from(volumes, min_row=1000, row_size=0.10):
    v = np.asarray(volumes, dtype=float)
    return vp.Histogram(min_row, v, row_size, accepted=int(v.sum()), rejected=0)


class TestPOC:
    def test_poc_is_the_max_volume_row(self):
        assert vp.poc_index(np.array([1.0, 5.0, 2.0])) == 1

    def test_poc_tie_goes_to_the_row_nearest_the_range_midpoint(self):
        # rows 0..4 occupied; midpoint index is 2. Tie between 1 and 3 -> ...
        # |1-2| == |3-2|, so the argmin picks the LOWER index per spec.
        v = np.array([1.0, 5.0, 1.0, 5.0, 1.0])
        assert vp.poc_index(v) == 1

    def test_poc_tie_resolves_toward_the_midpoint_when_distances_differ(self):
        # occupied 0..6, midpoint 3. Tie between 1 and 4 -> 4 is nearer.
        v = np.array([1.0, 5.0, 1.0, 1.0, 5.0, 1.0, 1.0])
        assert vp.poc_index(v) == 4


class TestValueArea:
    def test_single_row_expansion_absorbs_the_bigger_neighbour(self):
        #            0    1    2    3    4
        v = np.array([1.0, 8.0, 10.0, 2.0, 1.0])   # total 22, target 15.4
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # start 10; above=2 below=8 -> take below (18 >= 15.4). stop.
        assert (lo, hi) == (1, 2)

    def test_tie_goes_to_the_row_nearer_the_poc(self):
        #            0    1    2     3    4
        v = np.array([9.0, 3.0, 10.0, 3.0, 9.0])   # total 34, target 23.8
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # 10; above=3 below=3 tie, equidistant -> above (13); then above=9 below=3
        # -> above (22); then below=3 -> (25) >= 23.8
        assert (lo, hi) == (1, 4)

    def test_equidistant_tie_takes_the_higher_row(self):
        v = np.array([1.0, 4.0, 10.0, 4.0, 1.0])   # total 20, target 14
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="single_row")
        # 10; above=4 below=4, both distance 1 -> take ABOVE -> 14 >= 14, stop
        assert (lo, hi) == (2, 3)

    def test_one_side_exhausted_keeps_taking_the_other(self):
        v = np.array([10.0, 3.0, 3.0, 3.0])        # total 19, target 13.3
        lo, hi = vp.value_area(v, poc_i=0, target_frac=0.70, algorithm="single_row")
        assert (lo, hi) == (0, 2)

    def test_two_row_algorithm_absorbs_a_pair(self):
        v = np.array([1.0, 1.0, 10.0, 4.0, 4.0])   # total 20, target 14
        lo, hi = vp.value_area(v, poc_i=2, target_frac=0.70, algorithm="two_row")
        # above pair 4+4=8 vs below pair 1+1=2 -> take above -> 18 >= 14
        assert (lo, hi) == (2, 4)

    @pytest.mark.parametrize("seed", range(25))
    def test_value_area_always_contains_at_least_the_target_fraction(self, seed):
        rng = np.random.default_rng(seed)
        v = rng.random(60) * 100
        poc = vp.poc_index(v)
        lo, hi = vp.value_area(v, poc, 0.70, "single_row")
        assert v[lo:hi + 1].sum() >= 0.70 * v.sum() - 1e-9

    def test_flat_profile_still_terminates(self):
        v = np.full(10, 5.0)
        lo, hi = vp.value_area(v, vp.poc_index(v), 0.70, "single_row")
        assert v[lo:hi + 1].sum() >= 0.70 * v.sum() - 1e-9

    def test_single_row_profile(self):
        v = np.array([7.0])
        assert vp.value_area(v, 0, 0.70, "single_row") == (0, 0)


class TestBuildProfile:
    def test_levels_use_row_edges_so_the_band_contains_its_volume(self):
        prof = vp.build_profile(hist_from([1.0, 8.0, 10.0, 2.0, 1.0]), vp.ProfileParams())
        assert prof.vpoc == pytest.approx(vp.row_mid(1002, 0.10), abs=1e-9)
        assert prof.val == pytest.approx(vp.row_low(1001, 0.10), abs=1e-9)
        assert prof.vah == pytest.approx(vp.row_high(1002, 0.10), abs=1e-9)
        assert prof.low == pytest.approx(vp.row_low(1000, 0.10), abs=1e-9)
        assert prof.high == pytest.approx(vp.row_high(1004, 0.10), abs=1e-9)

    def test_empty_histogram_returns_none(self):
        assert vp.build_profile(hist_from([]), vp.ProfileParams()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_volume_profile.py -k "POC or ValueArea or BuildProfile" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'poc_index'`

- [ ] **Step 3: Write the implementation**

Append to `src/microstructure/volume_profile.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_volume_profile.py -v`
Expected: PASS (all, including the 25 parametrized containment cases)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/volume_profile.py tests/unit/test_volume_profile.py
git commit -m "feat(profile): POC and value area, both platform standards

Single-row expansion matches TradingView/CQG; two-row pairs match Sierra
Chart and ThinkOrSwim. Both tie branches are pinned by test, since the two
standards give different VAH/VAL on identical data.

VAH/VAL take row EDGES so the band provably contains its 70%."
```

---

### Task 3: The incremental tick cursor

This is the live-correctness core. `CopyTicksRange` is inclusive on both bounds and distinct ticks can share a millisecond, so the obvious cursor double-counts on every refresh.

**Files:**
- Modify: `src/microstructure/volume_profile.py`
- Modify: `tests/unit/test_volume_profile.py`

**Interfaces:**
- Consumes: `ProfileParams` from Task 1.
- Produces: `TickCursor` class with `__init__(self, start_msc: int)`, attribute `cursor_msc: int`, and method `split(self, time_msc: np.ndarray) -> int` returning the count of leading ticks in the batch that are safe to process.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_volume_profile.py`:

```python
class TestTickCursor:
    def test_defers_the_final_partial_millisecond(self):
        cur = vp.TickCursor(start_msc=0)
        t = np.array([10, 11, 12, 12], dtype=np.int64)
        n = cur.split(t)
        assert n == 2                 # ticks at msc 12 are held back
        assert cur.cursor_msc == 12

    def test_a_batch_entirely_within_one_millisecond_processes_nothing(self):
        cur = vp.TickCursor(start_msc=0)
        assert cur.split(np.array([5, 5, 5], dtype=np.int64)) == 0
        assert cur.cursor_msc == 5

    def test_empty_batch_is_a_noop(self):
        cur = vp.TickCursor(start_msc=3)
        assert cur.split(np.array([], dtype=np.int64)) == 0
        assert cur.cursor_msc == 3

    @pytest.mark.parametrize("n_batches", [1, 2, 3, 5, 11, 37])
    def test_arbitrary_batch_splits_equal_one_shot_processing(self, n_batches):
        """The regression test for the double-count bug.

        Feeding the same tick stream in any number of chunks -- including
        chunks that split INSIDE a millisecond -- must produce exactly the
        histogram that one-shot processing produces.
        """
        rng = np.random.default_rng(11)
        n = 4000
        # Deliberately few distinct milliseconds so ties are common.
        msc = np.sort(rng.integers(0, 400, n)).astype(np.int64)
        bid = 4000 + rng.normal(0, 2, n)
        ask = bid + 0.02

        params = vp.ProfileParams()
        one_shot = vp.accumulate_ticks(bid, ask, params)

        cur = vp.TickCursor(start_msc=int(msc[0]))
        rows: list[np.ndarray] = []
        start = 0
        for edge in np.array_split(np.arange(n), n_batches):
            if edge.size == 0:
                continue
            end = int(edge[-1]) + 1
            # A real CopyTicksRange call returns everything from cursor_msc on.
            sel = np.flatnonzero((msc >= cur.cursor_msc) & (np.arange(n) < end))
            if sel.size == 0:
                continue
            take = cur.split(msc[sel])
            if take:
                rows.append(sel[:take])
            start = end
        # Flush: at the true end of the session there is no more data coming,
        # so the held-back tail is processed.
        sel = np.flatnonzero(msc >= cur.cursor_msc)
        if sel.size:
            rows.append(sel)

        idx = np.unique(np.concatenate(rows)) if rows else np.array([], dtype=int)
        incremental = vp.accumulate_ticks(bid[idx], ask[idx], params)

        assert incremental.min_row == one_shot.min_row
        assert incremental.volumes == pytest.approx(one_shot.volumes, abs=0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_volume_profile.py -k TickCursor -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'TickCursor'`

- [ ] **Step 3: Write the implementation**

Append to `src/microstructure/volume_profile.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_volume_profile.py -k TickCursor -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/volume_profile.py tests/unit/test_volume_profile.py
git commit -m "feat(profile): retain-and-replay tick cursor

CopyTicksRange is inclusive on both bounds and distinct ticks can share a
millisecond, so a naive cursor re-counts the boundary millisecond on every
refresh and inflates the developing profile.

Process strictly below the batch max, park the cursor there, defer the
partial millisecond one cycle. Also gap-heals across disconnects.

Regression test feeds identical streams in 1..37 chunks including splits
inside a millisecond and requires byte-identical histograms."
```

---

### Task 4: Skew and P/b/D shape classification

**Files:**
- Create: `src/microstructure/profile_context.py`
- Create: `tests/unit/test_profile_context.py`

**Interfaces:**
- Consumes: `Profile`, `Histogram`, `row_mid` from `volume_profile`.
- Produces: `ContextParams` (frozen dataclass), `profile_skew(hist: Histogram) -> float | None`, `ShapeRead` (frozen dataclass with fields `shape: str`, `skew: float | None`, `poc_position: float`, `va_width_frac: float`, `upper_tail_frac: float`, `lower_tail_frac: float`), `classify_shape(prof: Profile, params: ContextParams) -> ShapeRead`. `shape` is one of `"P"`, `"b"`, `"D"`, `"UNCLASSIFIED"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_profile_context.py`:

```python
"""Unit tests for src/microstructure/profile_context.py — synthetic profiles only."""
import numpy as np
import pytest

from src.microstructure import volume_profile as vp
from src.microstructure import profile_context as pc


def prof_from(volumes, min_row=1000, row_size=0.10):
    v = np.asarray(volumes, dtype=float)
    h = vp.Histogram(min_row, v, row_size, accepted=int(v.sum()), rejected=0)
    return vp.build_profile(h, vp.ProfileParams(row_size=row_size))


class TestSkew:
    def test_symmetric_profile_has_exactly_zero_skew(self):
        h = vp.Histogram(1000, np.array([1.0, 4.0, 9.0, 4.0, 1.0]), 0.10, 19, 0)
        assert pc.profile_skew(h) == pytest.approx(0.0, abs=1e-12)

    def test_mirrored_profile_has_exactly_negated_skew(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v[::-1].copy(), 0.10, 27, 0))
        assert a == pytest.approx(-b, abs=1e-12)

    def test_invariant_under_uniform_price_rescale(self):
        """Dimensionless: this is why skew beats POC position."""
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v, 1.00, 27, 0))
        assert a == pytest.approx(b, rel=1e-12)

    def test_invariant_under_uniform_volume_rescale(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0])
        a = pc.profile_skew(vp.Histogram(1000, v, 0.10, 27, 0))
        b = pc.profile_skew(vp.Histogram(1000, v * 1000.0, 0.10, 27000, 0))
        assert a == pytest.approx(b, rel=1e-12)

    def test_degenerate_profiles_return_none(self):
        assert pc.profile_skew(vp.Histogram(1000, np.array([5.0]), 0.10, 5, 0)) is None
        assert pc.profile_skew(vp.Histogram(1000, np.zeros(0), 0.10, 0, 0)) is None


class TestShapeClassification:
    def params(self, t=0.30):
        return pc.ContextParams(skew_threshold=t)

    def test_mass_at_highs_with_thin_tail_below_is_P(self):
        """SIGN CONVENTION PIN. Mass high + tail below => negative skew => P.

        A published indicator (BackQuant Volume Profile Skew) reads this same
        geometry as bullish 'accumulation' via the opposite sign. An inversion
        here would flip every regime call on the chart, so this is asserted
        directly rather than inferred.
        """
        v = np.array([1.0, 1.0, 1.0, 2.0, 20.0, 25.0, 22.0])
        prof = prof_from(v)
        read = pc.classify_shape(prof, self.params())
        assert read.skew < 0
        assert read.shape == "P"

    def test_mass_at_lows_with_thin_tail_above_is_b(self):
        v = np.array([22.0, 25.0, 20.0, 2.0, 1.0, 1.0, 1.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert read.skew > 0
        assert read.shape == "b"

    def test_symmetric_profile_is_D(self):
        v = np.array([1.0, 5.0, 12.0, 20.0, 12.0, 5.0, 1.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert read.shape == "D"

    def test_profile_with_too_few_rows_is_unclassified(self):
        read = pc.classify_shape(prof_from([3.0, 4.0]), self.params())
        assert read.shape == "UNCLASSIFIED"
        assert read.skew is None

    def test_corroborating_measures_are_populated(self):
        v = np.array([1.0, 1.0, 1.0, 2.0, 20.0, 25.0, 22.0])
        read = pc.classify_shape(prof_from(v), self.params())
        assert 0.0 <= read.poc_position <= 1.0
        assert 0.0 < read.va_width_frac <= 1.0
        assert read.poc_position > 0.5          # POC sits high in a P

    def test_threshold_controls_the_D_band(self):
        v = np.array([1.0, 2.0, 3.0, 12.0, 9.0, 4.0, 2.0])
        loose = pc.classify_shape(prof_from(v), self.params(t=5.0))
        tight = pc.classify_shape(prof_from(v), self.params(t=0.001))
        assert loose.shape == "D"
        assert tight.shape in ("P", "b")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_profile_context.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.microstructure.profile_context'`

- [ ] **Step 3: Write the implementation**

Create `src/microstructure/profile_context.py`:

```python
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

from .volume_profile import Histogram, Profile, row_mid

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
```

Note the ordering of the `elif` chain: with `skew_threshold == 0.0` (the uncalibrated sentinel) a negative skew still resolves to `P` and a positive to `b`, and only an exactly-zero skew reaches `D`. That is deliberate — the sentinel must not silently classify everything as balanced.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_profile_context.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/profile_context.py tests/unit/test_profile_context.py
git commit -m "feat(profile): skew-based P/b/D classification

No published numeric cutoffs for P/b/D exist -- NinjaTrader, QuantVue and
the TradingView material all teach the shapes visually. So the constant is
chosen, and this reduces the choice to exactly one.

Skew over POC position: dimensionless, whole-distribution, natural zero.
Price-scale and volume-scale invariance asserted, not assumed.

Sign convention pinned by test -- a published script reads identical
geometry with the opposite meaning."
```

---

### Task 5: Open type, value migration, and balance regime

**Files:**
- Modify: `src/microstructure/profile_context.py`
- Modify: `tests/unit/test_profile_context.py`

**Interfaces:**
- Consumes: `Profile`, `ShapeRead`, `ContextParams`, shape constants from Task 4.
- Produces: `classify_open_type(open_price: float, prior: Profile) -> str`, `classify_value_migration(today: Profile, prior: Profile) -> str`, `classify_regime(shape: str, elapsed_pct: float, params: ContextParams, is_developing: bool) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_profile_context.py`:

```python
class TestOpenType:
    """Prior session: low 1000.0, VAL 1001.0, VAH 1003.0, high 1004.0."""

    def prior(self):
        # rows 10000..10039 at row_size 0.10 -> 1000.00 .. 1004.00
        v = np.ones(40)
        v[10:30] = 50.0            # value area sits in the middle
        return vp.build_profile(vp.Histogram(10000, v, 0.10, int(v.sum()), 0),
                                vp.ProfileParams(row_size=0.10))

    def test_open_above_the_prior_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.high + 0.5, p) == "OPEN_ABOVE_RANGE"

    def test_open_above_value_but_inside_range(self):
        p = self.prior()
        assert pc.classify_open_type((p.vah + p.high) / 2, p) == "OPEN_ABOVE_VA"

    def test_open_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type((p.val + p.vah) / 2, p) == "OPEN_INSIDE_VA"

    def test_open_below_value_but_inside_range(self):
        p = self.prior()
        assert pc.classify_open_type((p.low + p.val) / 2, p) == "OPEN_BELOW_VA"

    def test_open_below_the_prior_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.low - 0.5, p) == "OPEN_BELOW_RANGE"

    def test_open_exactly_on_vah_counts_as_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type(p.vah, p) == "OPEN_INSIDE_VA"

    def test_open_exactly_on_val_counts_as_inside_value(self):
        p = self.prior()
        assert pc.classify_open_type(p.val, p) == "OPEN_INSIDE_VA"

    def test_open_exactly_on_prior_high_is_above_value_not_above_range(self):
        p = self.prior()
        assert pc.classify_open_type(p.high, p) == "OPEN_ABOVE_VA"


def va_profile(val_row, vah_row):
    """Build a profile whose value area spans exactly [val_row, vah_row]."""
    lo, hi = val_row - 2, vah_row + 2
    v = np.ones(hi - lo + 1)
    v[(val_row - lo):(vah_row - lo + 1)] = 100.0
    return vp.build_profile(vp.Histogram(lo, v, 0.10, int(v.sum()), 0),
                            vp.ProfileParams(row_size=0.10))


class TestValueMigration:
    def test_higher_when_there_is_no_overlap(self):
        prior = va_profile(10000, 10010)
        today = va_profile(10020, 10030)
        assert pc.classify_value_migration(today, prior) == "HIGHER"

    def test_lower_when_there_is_no_overlap(self):
        prior = va_profile(10020, 10030)
        today = va_profile(10000, 10010)
        assert pc.classify_value_migration(today, prior) == "LOWER"

    def test_overlapping_higher(self):
        prior = va_profile(10000, 10010)
        today = va_profile(10005, 10015)
        assert pc.classify_value_migration(today, prior) == "OVERLAPPING_HIGHER"

    def test_overlapping_lower(self):
        prior = va_profile(10005, 10015)
        today = va_profile(10000, 10010)
        assert pc.classify_value_migration(today, prior) == "OVERLAPPING_LOWER"

    def test_inside(self):
        prior = va_profile(10000, 10020)
        today = va_profile(10005, 10015)
        assert pc.classify_value_migration(today, prior) == "INSIDE"

    def test_engulfing(self):
        prior = va_profile(10005, 10015)
        today = va_profile(10000, 10020)
        assert pc.classify_value_migration(today, prior) == "ENGULFING"

    def test_identical_value_areas_are_inside(self):
        prior = va_profile(10000, 10010)
        today = va_profile(10000, 10010)
        assert pc.classify_value_migration(today, prior) == "INSIDE"


class TestRegime:
    def params(self):
        return pc.ContextParams(skew_threshold=0.30, regime_min_elapsed_pct=0.50)

    def test_shape_maps_to_regime(self):
        p = self.params()
        assert pc.classify_regime("D", 1.0, p, False) == "BALANCED"
        assert pc.classify_regime("P", 1.0, p, False) == "OUT_OF_BALANCE_UP"
        assert pc.classify_regime("b", 1.0, p, False) == "OUT_OF_BALANCE_DOWN"
        assert pc.classify_regime("UNCLASSIFIED", 1.0, p, False) == "UNCLEAR"

    def test_developing_session_below_the_elapsed_floor_is_forming(self):
        """Every session looks like a P or a b before it has traded both ways."""
        p = self.params()
        assert pc.classify_regime("P", 0.20, p, is_developing=True) == "FORMING"
        assert pc.classify_regime("P", 0.80, p, is_developing=True) == "OUT_OF_BALANCE_UP"

    def test_completed_sessions_ignore_the_elapsed_floor(self):
        p = self.params()
        assert pc.classify_regime("P", 0.10, p, is_developing=False) == "OUT_OF_BALANCE_UP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_profile_context.py -k "OpenType or ValueMigration or Regime" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'classify_open_type'`

- [ ] **Step 3: Write the implementation**

Append to `src/microstructure/profile_context.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_profile_context.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/profile_context.py tests/unit/test_profile_context.py
git commit -m "feat(profile): open type, value migration, balance regime

Containment is checked before direction so inside/engulfing days are never
mislabelled as drift. Value-area edges are inclusive, range edges are not.

FORMING guard: a developing session reports no regime until half elapsed,
because every session looks directional before it has traded both ways."
```

---

### Task 6: HVN / LVN nodes and naked POC

**Files:**
- Modify: `src/microstructure/volume_profile.py`
- Modify: `tests/unit/test_volume_profile.py`

**Interfaces:**
- Consumes: `Profile`, `Histogram`, `row_mid` from Tasks 1–2.
- Produces: `NodeParams` (frozen dataclass with `hvn_prominence_pct: float = 0.15`, `lvn_ratio: float = 0.50`, `min_separation_rows: int = 10`), `find_nodes(prof: Profile, params: NodeParams) -> tuple[list[float], list[float]]` returning `(hvn_prices, lvn_prices)`, `naked_pocs(session_pocs: list[tuple[str, float, int]], bar_high: np.ndarray, bar_low: np.ndarray, bar_index_of_session_end: dict[str, int]) -> list[tuple[str, float]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_volume_profile.py`:

```python
class TestNodes:
    def test_two_peaks_separated_by_a_valley_give_two_hvn_and_one_lvn(self):
        v = np.array([1.0, 20.0, 10.0, 2.0, 1.0, 2.0, 10.0, 22.0, 3.0])
        prof = vp.build_profile(hist_from(v), vp.ProfileParams())
        hvn, lvn = vp.find_nodes(prof, vp.NodeParams(min_separation_rows=2))
        assert len(hvn) == 2
        assert len(lvn) == 1
        # the LVN sits in the valley around index 4
        assert lvn[0] == pytest.approx(vp.row_mid(1004, 0.10), abs=1e-9)

    def test_a_single_peak_yields_no_lvn(self):
        v = np.array([1.0, 5.0, 20.0, 5.0, 1.0])
        prof = vp.build_profile(hist_from(v), vp.ProfileParams())
        hvn, lvn = vp.find_nodes(prof, vp.NodeParams(min_separation_rows=2))
        assert len(lvn) == 0

    def test_node_detection_is_deterministic(self):
        rng = np.random.default_rng(3)
        v = rng.random(80) * 50
        prof = vp.build_profile(hist_from(v), vp.ProfileParams())
        a = vp.find_nodes(prof, vp.NodeParams())
        b = vp.find_nodes(prof, vp.NodeParams())
        assert a == b


class TestNakedPOC:
    def test_a_poc_later_traded_through_is_tagged_and_dropped(self):
        pocs = [("2026-08-10", 100.00, 0), ("2026-08-11", 200.00, 2)]
        high = np.array([101.0, 101.0, 150.0, 150.0])
        low = np.array([ 99.0,  99.0, 140.0, 140.0])
        ends = {"2026-08-10": 0, "2026-08-11": 2}
        naked = vp.naked_pocs(pocs, high, low, ends)
        # 100.00 was traded through by bar 1 -> tagged. 200.00 never touched.
        assert [p for _, p in naked] == [200.00]

    def test_a_poc_touched_exactly_at_a_bar_extreme_is_tagged(self):
        pocs = [("2026-08-10", 100.00, 0)]
        high = np.array([99.0, 100.00])
        low = np.array([98.0, 99.0])
        naked = vp.naked_pocs(pocs, high, low, {"2026-08-10": 0})
        assert naked == []

    def test_bars_before_the_session_end_do_not_tag_it(self):
        pocs = [("2026-08-11", 100.00, 5)]
        high = np.array([100.0] * 5 + [90.0])
        low = np.array([100.0] * 5 + [80.0])
        naked = vp.naked_pocs(pocs, high, low, {"2026-08-11": 5})
        assert [p for _, p in naked] == [100.00]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_volume_profile.py -k "Nodes or NakedPOC" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'NodeParams'`

- [ ] **Step 3: Write the implementation**

Append to `src/microstructure/volume_profile.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_volume_profile.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/volume_profile.py tests/unit/test_volume_profile.py
git commit -m "feat(profile): HVN/LVN nodes and naked POC tracking

LVN is the load-bearing node type -- the course treats low-volume nodes as
where absorption happens. HVN is secondary.

The three node thresholds are uncalibrated display heuristics and say so
in the dataclass docstring."
```

---

### Task 7: Composite profile and initial balance

**Files:**
- Modify: `src/microstructure/volume_profile.py`
- Modify: `tests/unit/test_volume_profile.py`

**Interfaces:**
- Consumes: `Histogram`, `ProfileParams` from Task 1.
- Produces: `merge_histograms(hists: list[Histogram]) -> Histogram`, `initial_balance(ts_minutes: np.ndarray, price: np.ndarray, ib_minutes: int) -> tuple[float, float] | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_volume_profile.py`:

```python
class TestComposite:
    def test_merge_equals_the_sum_of_its_parts(self):
        a = hist_from([1.0, 2.0, 3.0], min_row=1000)
        b = hist_from([4.0, 5.0], min_row=1002)
        m = vp.merge_histograms([a, b])
        assert m.min_row == 1000
        # rows 1000,1001,1002,1003 -> 1, 2, 3+4, 5
        assert m.volumes == pytest.approx([1.0, 2.0, 7.0, 5.0], abs=1e-9)
        assert m.total == pytest.approx(a.total + b.total, abs=1e-9)

    def test_merge_of_disjoint_histograms_fills_the_gap_with_zeros(self):
        a = hist_from([1.0], min_row=1000)
        b = hist_from([1.0], min_row=1004)
        m = vp.merge_histograms([a, b])
        assert m.volumes.size == 5
        assert m.volumes == pytest.approx([1.0, 0.0, 0.0, 0.0, 1.0], abs=1e-9)

    def test_merge_of_an_empty_list_is_empty(self):
        assert vp.merge_histograms([]).volumes.size == 0

    def test_merge_preserves_accepted_and_rejected_counts(self):
        a = vp.Histogram(1000, np.array([2.0]), 0.10, accepted=2, rejected=1)
        b = vp.Histogram(1000, np.array([3.0]), 0.10, accepted=3, rejected=4)
        m = vp.merge_histograms([a, b])
        assert (m.accepted, m.rejected) == (5, 5)


class TestInitialBalance:
    def test_ib_covers_only_the_first_n_minutes(self):
        mins = np.array([0.0, 30.0, 59.9, 61.0, 120.0])
        px = np.array([100.0, 105.0, 95.0, 200.0, 50.0])
        assert vp.initial_balance(mins, px, 60) == (95.0, 105.0)

    def test_ib_boundary_minute_is_exclusive(self):
        mins = np.array([0.0, 60.0])
        px = np.array([100.0, 999.0])
        assert vp.initial_balance(mins, px, 60) == (100.0, 100.0)

    def test_no_ticks_in_the_window_returns_none(self):
        assert vp.initial_balance(np.array([90.0]), np.array([100.0]), 60) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_volume_profile.py -k "Composite or InitialBalance" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'merge_histograms'`

- [ ] **Step 3: Write the implementation**

Append to `src/microstructure/volume_profile.py`:

```python
def merge_histograms(hists: list[Histogram]) -> Histogram:
    """Sum session histograms onto the shared absolute grid.

    This is what makes the composite nearly free: no tick data is re-read, the
    cached per-session histograms are simply added. It works only because the
    grid is absolute -- session-anchored bins could not be summed like this.
    """
    live = [h for h in hists if h.volumes.size > 0]
    if not live:
        return Histogram(0, np.zeros(0), hists[0].row_size if hists else 0.10, 0, 0)
    row_size = live[0].row_size
    lo = min(h.min_row for h in live)
    hi = max(h.max_row for h in live)
    out = np.zeros(hi - lo + 1, dtype=float)
    for h in live:
        off = h.min_row - lo
        out[off:off + h.volumes.size] += h.volumes
    return Histogram(lo, out, row_size,
                     accepted=sum(h.accepted for h in live),
                     rejected=sum(h.rejected for h in live))


def initial_balance(minutes_from_open: np.ndarray, price: np.ndarray,
                    ib_minutes: int = 60) -> tuple[float, float] | None:
    """High and low of the first `ib_minutes` of the session.

    NOTE: this is standard range-based Initial Balance. It is NOT Fabio
    Valentini's IVB (Initial Volume Breakout), whose rule incorporates volume
    and is not recoverable from the available course material. Do not label it
    as IVB anywhere in the UI or docs.
    """
    mask = minutes_from_open < ib_minutes
    if not np.any(mask):
        return None
    window = price[mask]
    return float(window.min()), float(window.max())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_volume_profile.py -v && pytest tests/unit/test_profile_context.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/microstructure/volume_profile.py tests/unit/test_volume_profile.py
git commit -m "feat(profile): composite merge and initial balance

Composite sums cached session histograms with no tick re-read, which works
only because the grid is absolute.

IB is standard range-based and documented as NOT being Fabio's IVB."
```

---

### Task 8: Calibrate the skew threshold

**Files:**
- Create: `scripts/calibrate_profile_shape.py`
- Create: `reports/volume_profile_shape_calibration.md` (generated)

**Interfaces:**
- Consumes: `volume_profile` and `profile_context` from Tasks 1–7.
- Produces: a calibrated `skew_threshold` value, written into the report and into the `ContextParams` default.

- [ ] **Step 1: Write the calibration script**

Create `scripts/calibrate_profile_shape.py`:

```python
#!/usr/bin/env python3
"""
Calibrate the ONE constant the P/b/D classifier turns on.

No published numeric cutoffs for these shapes exist -- the field teaches them
visually. So the threshold cannot be looked up, only chosen. This grounds the
choice against a published FREQUENCY instead of taste:

  Objective: pick the threshold whose resulting D (balanced) frequency is
  closest to 50% -- Dalton's base rate for normal/rotational days in
  "Mind Over Markets", the most-cited number in that taxonomy.

Why this objective:
  * it targets a published frequency, not a preference
  * it is OUTCOME-FREE -- no trade results enter it, so it is structurally
    incapable of overfitting to profit (see project_rsi_reversal_m1)
  * it is calibrated on GOLD, not inherited from index futures

Stated limits of the anchor: Dalton's day types are a different taxonomy from
P/b/D (his Normal Day is defined by initial-balance extension, not skew), and
his rates come from equity-index RTH sessions, not 23-hour gold. 50% is a
defensible prior, not ground truth. The P/b/UNCLASSIFIED split is REPORTED,
never targeted, and the sweep is judged on having a broad plateau.

Usage:
  python scripts/calibrate_profile_shape.py
  python scripts/calibrate_profile_shape.py --ticks data/ticks/XAUUSD --row-size 0.10
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.microstructure import volume_profile as vp          # noqa: E402
from src.microstructure import profile_context as pc         # noqa: E402

TARGET_D_FREQ = 0.50
SWEEP = np.round(np.arange(0.05, 1.51, 0.05), 2)


def session_skews(tick_dir: Path, row_size: float, max_spread: float) -> list[float]:
    """One skew per session file. Sessions below the tick floor are skipped."""
    params = vp.ProfileParams(row_size=row_size, max_spread_usd=max_spread)
    out: list[float] = []
    files = sorted(tick_dir.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no .parquet tick files under {tick_dir}")
    for f in files:
        df = pd.read_parquet(f, columns=["bid", "ask"])
        hist = vp.accumulate_ticks(df.bid.to_numpy(), df.ask.to_numpy(), params)
        if hist.accepted < params.min_session_ticks:
            print(f"  skip {f.name}: {hist.accepted} ticks < {params.min_session_ticks}")
            continue
        s = pc.profile_skew(hist)
        if s is not None:
            out.append(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticks", default="data/ticks/XAUUSD")
    ap.add_argument("--row-size", type=float, default=0.10)
    ap.add_argument("--max-spread", type=float, default=1.00)
    ap.add_argument("--out", default="reports/volume_profile_shape_calibration.md")
    args = ap.parse_args()

    skews = session_skews(PROJECT_ROOT / args.ticks, args.row_size, args.max_spread)
    if not skews:
        raise SystemExit("no usable sessions")
    arr = np.asarray(skews)
    print(f"{arr.size} sessions, skew range {arr.min():+.3f} .. {arr.max():+.3f}")

    rows = []
    for t in SWEEP:
        d = int(np.sum(np.abs(arr) < t))
        p = int(np.sum(arr <= -t))
        b = int(np.sum(arr >= t))
        rows.append((float(t), d / arr.size, p / arr.size, b / arr.size))

    best = min(rows, key=lambda r: abs(r[1] - TARGET_D_FREQ))
    chosen = best[0]

    # Plateau width: how wide a band of thresholds keeps D within 5pp of target.
    near = [r[0] for r in rows if abs(r[1] - TARGET_D_FREQ) <= 0.05]
    plateau = (min(near), max(near)) if near else (chosen, chosen)

    lines = [
        "# Volume Profile Shape Calibration",
        "",
        "**Generated by `scripts/calibrate_profile_shape.py` — do not hand-edit.**",
        "Hand-tuning the threshold below turns a calibrated constant back into a guess.",
        "",
        f"- Sessions used: **{arr.size}**",
        f"- Row size: `{args.row_size}` · max spread: `{args.max_spread}`",
        f"- Skew range: `{arr.min():+.3f}` .. `{arr.max():+.3f}`",
        f"- Objective: D frequency closest to **{TARGET_D_FREQ:.0%}** (Dalton, *Mind Over Markets*)",
        "",
        f"## Chosen threshold: `{chosen:.2f}`",
        "",
        f"- Achieved D frequency: **{best[1]:.1%}** (target {TARGET_D_FREQ:.0%})",
        f"- Resulting P: {best[2]:.1%} · b: {best[3]:.1%}  *(reported, not targeted)*",
        f"- Plateau within 5pp of target: `{plateau[0]:.2f}` .. `{plateau[1]:.2f}`",
        "",
        "⚠️ Dalton's day types are a DIFFERENT taxonomy from P/b/D, and his base",
        "rates come from equity-index RTH sessions rather than 23-hour gold. The",
        "50% target is a defensible prior, not ground truth. Judge this",
        "calibration on the width of the plateau above, not on hitting 50%.",
        "",
        "## Sweep",
        "",
        "| threshold | D | P | b |",
        "|---:|---:|---:|---:|",
    ]
    for t, d, p, b in rows:
        mark = " **<-**" if t == chosen else ""
        lines.append(f"| {t:.2f}{mark} | {d:.1%} | {p:.1%} | {b:.1%} |")

    out_path = PROJECT_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"\nchosen threshold {chosen:.2f} (D={best[1]:.1%}, plateau "
          f"{plateau[0]:.2f}..{plateau[1]:.2f}) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify the tick data is readable**

Run: `source venv/bin/activate && python -c "import pandas as pd; d=pd.read_parquet('data/ticks/XAUUSD/2026-01-07.parquet', columns=['bid','ask']); print(len(d), d.bid.iloc[0], d.ask.iloc[0])"`
Expected: prints a row count around 391382 and two prices near 4493.

If this fails with a pyarrow ImportError, run `pip install pyarrow` first — the venv needs a parquet engine.

- [ ] **Step 3: Run the calibration**

Run: `source venv/bin/activate && python scripts/calibrate_profile_shape.py`
Expected: prints the session count, the chosen threshold, and writes `reports/volume_profile_shape_calibration.md`.

- [ ] **Step 4: Read the report and sanity-check the plateau**

Open `reports/volume_profile_shape_calibration.md`. The calibration is trustworthy only if the plateau spans several sweep steps. **If the plateau is a single threshold value, stop and report that** — it means D frequency is hypersensitive to the cutoff, and a knife-edge constant is exactly what this exercise was meant to avoid.

- [ ] **Step 5: Write the chosen value into the default**

Edit `src/microstructure/profile_context.py`, replacing the sentinel in `ContextParams`:

```python
    # Calibrated by scripts/calibrate_profile_shape.py on 151 XAUUSD sessions.
    # See reports/volume_profile_shape_calibration.md. Re-run if row_size or
    # the session definition changes -- both alter the histogram this is
    # computed from.
    skew_threshold: float = <value from the report>
```

- [ ] **Step 6: Run the full suite**

Run: `pytest tests/unit/test_volume_profile.py tests/unit/test_profile_context.py -v`
Expected: PASS. The `test_threshold_controls_the_D_band` test passes explicit thresholds, so the new default cannot break it.

- [ ] **Step 7: Commit**

```bash
git add scripts/calibrate_profile_shape.py src/microstructure/profile_context.py
git add -f reports/volume_profile_shape_calibration.md
git commit -m "feat(profile): calibrate the skew threshold on 151 gold sessions

Objective is D frequency closest to Dalton's ~50% base rate for balanced
days. Outcome-free -- no trade results enter it, so it cannot overfit to
profit.

Limits of the anchor are recorded in the report: different taxonomy,
equity-RTH origin. Judged on plateau width, not on hitting 50%."
```

---

### Task 9: MQL5 indicator — profile core

**Files:**
- Create: `mt5_indicators/GoldenChart_VolumeProfile.mq5`

**Interfaces:**
- Consumes: the algorithms defined in Tasks 1–3, ported.
- Produces: MQL5 functions `RowIndex`, `RowLow`, `RowMid`, `RowHigh`, `POCIndex`, `ValueArea`, `ProfileSkew`, struct `SessionProfile`, and the `PENDING`/`M1` source state machine.

- [ ] **Step 1: Write the indicator header and inputs**

Create `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//+------------------------------------------------------------------+
//|                                 GoldenChart_VolumeProfile.mq5    |
//|   XAUUSD volume profile: VAH/VAL/VPOC plus the AMT context read  |
//|   (P/b/D shape, open type, balance regime, IB, composite).       |
//|                                                                  |
//|   This is a PORT of two Python modules and must agree with them: |
//|     src/microstructure/volume_profile.py                         |
//|     src/microstructure/profile_context.py                        |
//|   scripts/check_volume_profile_parity.py is what proves it does. |
//|                                                                  |
//|   WHAT THIS MEASURES: a gold CFD has no traded volume. MT5's     |
//|   volume/volume_real are zero or synthetic for XAUUSD, so every  |
//|   number here is a TICK DENSITY, never a contract count. No      |
//|   label in this file may imply otherwise.                        |
//+------------------------------------------------------------------+
#property copyright "Quant_trading"
#property link      "https://github.com/varadbandekar/Quant_trading"
#property version   "1.00"
#property strict
#property indicator_chart_window
#property indicator_buffers 0
#property indicator_plots   0

//--- Profile geometry ----------------------------------------------
input double InpRowSize            = 0.10;   // Row height (USD, not points)
input double InpValueAreaPct       = 0.70;   // Value area fraction
input int    InpVAAlgorithm        = 0;      // 0=single-row (TradingView), 1=two-row (Steidlmayer)
input int    InpProfileDays        = 10;     // Completed sessions to draw
input int    InpCompositeDays      = 5;      // Sessions in the composite
input int    InpSessionUTCOverride = -1;     // -1 = use broker D1 boundary
//--- Tick handling -------------------------------------------------
input double InpMaxSpreadUSD       = 1.00;   // Reject ticks wider than this
input int    InpTickPriceMode      = 0;      // 0=mid, 1=bid, 2=ask
input int    InpMinSessionTicks    = 5000;   // Below this, skip the session
input int    InpTickRetryLimit     = 12;     // Retries before falling back to M1
//--- Context -------------------------------------------------------
input double InpSkewThreshold      = 0.0;    // 0 = UNCALIBRATED (panel will warn)
input int    InpMinRowsForShape    = 5;      // Fewer rows => UNCLASSIFIED
input double InpRegimeMinElapsed   = 0.50;   // Developing regime reports FORMING below this
input int    InpIBMinutes          = 60;     // Initial balance window
//--- Nodes (UNCALIBRATED display heuristics) -----------------------
input bool   InpShowLVN            = true;   // Low-volume nodes (absorption zones)
input bool   InpShowHVN            = false;  // High-volume nodes
input double InpHVNProminencePct   = 0.15;
input double InpLVNRatio           = 0.50;
input int    InpNodeMinSepRows     = 10;
//--- Display -------------------------------------------------------
input int    InpRefreshSec         = 5;
input bool   InpShowPanel          = true;
input int    InpHistogramProfiles  = 2;      // Sessions drawn as a histogram
input double InpHistogramWidthPct  = 0.35;
input int    InpMaxObjects         = 3000;
input color  InpVPOCColor          = clrGold;
input color  InpVAHColor           = clrDeepSkyBlue;
input color  InpVALColor           = clrTomato;
input color  InpNakedPOCColor      = clrMagenta;
input color  InpCompositeColor     = clrOrchid;
input color  InpIBColor            = clrSlateGray;
input color  InpLVNColor           = clrDarkOrange;
input color  InpHVNColor           = clrDarkSlateGray;
//--- Alerts --------------------------------------------------------
input bool   InpAlertsOn           = false;
input bool   InpAlertDeveloping    = false;
input bool   InpAlertLVN           = false;
input double InpAlertRearmUSD      = 0.50;
input bool   InpSendNotifications  = false;
//--- Parity harness ------------------------------------------------
input bool   InpExportCSV          = false;

const string PFX = "GC_VP_";

#define SRC_PENDING 0
#define SRC_TICK    1
#define SRC_M1      2

#define SHAPE_UNKNOWN 0
#define SHAPE_P       1
#define SHAPE_B       2
#define SHAPE_D       3

string ShapeName(const int s)
{
   switch(s)
   {
      case SHAPE_P: return "P";
      case SHAPE_B: return "b";
      case SHAPE_D: return "D";
   }
   return "UNCLASSIFIED";
}

string SourceName(const int s)
{
   switch(s)
   {
      case SRC_TICK: return "TICK";
      case SRC_M1:   return "M1";
   }
   return "PENDING";
}

struct SessionProfile
{
   datetime start;
   datetime end;
   int      min_row;
   double   volumes[];
   int      accepted;
   int      rejected;
   int      source;
   int      retries;
   ulong    cursor_msc;      // retain-and-replay cursor (developing only)
   // resolved
   bool     valid;
   int      poc_row, val_row, vah_row;
   double   vpoc, val, vah, low, high;
   double   skew;
   bool     has_skew;
   int      shape;
   double   ib_low, ib_high;
   bool     has_ib;
   double   open_price;
};
```

- [ ] **Step 2: Port the grid and value-area algorithms**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//--- Grid. Absolute, never session-anchored: a price maps to the same
//    row in every session, so VPOCs stay comparable across days.
int    RowIndex(const double price) { return (int)MathFloor(price / InpRowSize); }
double RowLow (const int row)       { return row * InpRowSize; }
double RowMid (const int row)       { return (row + 0.5) * InpRowSize; }
double RowHigh(const int row)       { return (row + 1) * InpRowSize; }

//--- POC: max volume; ties resolve toward the middle of the occupied
//    range, then to the lower index. Matches poc_index() in Python.
int POCIndex(const double &v[])
{
   int n = ArraySize(v);
   if(n == 0) return -1;
   double vmax = 0.0;
   for(int i = 0; i < n; i++) if(v[i] > vmax) vmax = v[i];
   if(vmax <= 0.0) return -1;

   int first = -1, last = -1;
   for(int i = 0; i < n; i++) if(v[i] > 0.0) { if(first < 0) first = i; last = i; }
   double mid = (first + last) / 2.0;

   int best = -1; double bestDist = 0.0;
   for(int i = 0; i < n; i++)
   {
      if(v[i] != vmax) continue;
      double d = MathAbs(i - mid);
      if(best < 0 || d < bestDist) { best = i; bestDist = d; }  // strict < keeps the LOWER index on a tie
   }
   return best;
}

//--- Value area. Mirrors value_area() in Python, including both tie
//    branches: nearer-to-POC wins, and equidistant takes the UPPER row.
void ValueArea(const double &v[], const int poc_i, double target_frac,
               const int algorithm, int &out_lo, int &out_hi)
{
   int n = ArraySize(v);
   out_lo = poc_i; out_hi = poc_i;
   if(n == 0 || poc_i < 0) return;

   double total = 0.0;
   for(int i = 0; i < n; i++) total += v[i];
   if(total <= 0.0) return;

   double target = total * target_frac;
   double acc = v[poc_i];

   while(acc < target)
   {
      bool up = (out_hi + 1 < n);
      bool dn = (out_lo - 1 >= 0);
      if(!up && !dn) break;

      if(algorithm == 0)   // single-row, TradingView/CQG
      {
         bool takeUp;
         if(!dn)      takeUp = true;
         else if(!up) takeUp = false;
         else
         {
            double a = v[out_hi + 1], b = v[out_lo - 1];
            if(a != b) takeUp = (a > b);
            else
            {
               int dUp = (out_hi + 1) - poc_i;
               int dDn = poc_i - (out_lo - 1);
               takeUp = (dUp <= dDn);        // equidistant -> upper row
            }
         }
         if(takeUp) { out_hi++; acc += v[out_hi]; }
         else       { out_lo--; acc += v[out_lo]; }
      }
      else                 // two-row pairs, Steidlmayer/Sierra/ToS
      {
         double upSum = -1.0, dnSum = -1.0;
         if(up)
         {
            upSum = v[out_hi + 1];
            if(out_hi + 2 < n) upSum += v[out_hi + 2];
         }
         if(dn)
         {
            dnSum = v[out_lo - 1];
            if(out_lo - 2 >= 0) dnSum += v[out_lo - 2];
         }
         if(!dn || (up && upSum >= dnSum)) out_hi = MathMin(out_hi + 2, n - 1);
         else                              out_lo = MathMax(out_lo - 2, 0);

         acc = 0.0;
         for(int i = out_lo; i <= out_hi; i++) acc += v[i];
      }
   }
}

//--- Skew: standardised third central moment. Sign convention:
//      negative -> mass at HIGH prices -> P
//      positive -> mass at LOW  prices -> b
//    A published indicator reads this geometry the opposite way. Follow
//    the spec, not that script.
bool ProfileSkew(const double &v[], const int min_row, double &out_skew)
{
   int n = ArraySize(v);
   if(n < 2) return false;
   double total = 0.0;
   for(int i = 0; i < n; i++) total += v[i];
   if(total <= 0.0) return false;

   double mean = 0.0;
   for(int i = 0; i < n; i++) mean += (v[i] / total) * RowMid(min_row + i);

   double var = 0.0;
   for(int i = 0; i < n; i++)
   {
      double d = RowMid(min_row + i) - mean;
      var += (v[i] / total) * d * d;
   }
   if(var <= 0.0) return false;
   double sd = MathSqrt(var);

   double m3 = 0.0;
   for(int i = 0; i < n; i++)
   {
      double d = RowMid(min_row + i) - mean;
      m3 += (v[i] / total) * d * d * d;
   }
   out_skew = m3 / (sd * sd * sd);
   return true;
}
```

- [ ] **Step 3: Port the tick cursor and the PENDING state machine**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//--- Incremental tick fetch.
//
//    CopyTicksRange is INCLUSIVE on both from_msc and to_msc, and distinct
//    ticks can share a millisecond. The obvious cursor (from = last seen)
//    therefore re-counts the boundary millisecond on EVERY refresh -- a
//    compounding double-count that drags VPOC toward whatever price was
//    busiest at the last refresh. Skipping one tick does not fix it.
//
//    Invariant: nothing at or after cursor_msc has been processed.
//    Process strictly below the batch max, park the cursor there, and defer
//    the partial millisecond one cycle. Also gap-heals across disconnects:
//    if the terminal drops, the cursor does not advance and the next call
//    backfills automatically.
//
//    Returns: number of ticks accumulated, or -1 if history is not ready.
int AccumulateTicksIncremental(SessionProfile &p, const datetime to_time)
{
   MqlTick ticks[];
   ulong to_msc = (ulong)to_time * 1000 + 999;
   int n = CopyTicksRange(_Symbol, ticks, COPY_TICKS_ALL, p.cursor_msc, to_msc);
   if(n < 0)
   {
      // History still synchronising. Do NOT read this as "no ticks" and fall
      // back to M1 -- that would silently pin the profile at reduced fidelity
      // for the rest of the session.
      p.retries++;
      PrintFormat("%s tick history not ready (err %d), retry %d/%d",
                  PFX, GetLastError(), p.retries, InpTickRetryLimit);
      return -1;
   }
   if(n == 0) return 0;

   ulong boundary = ticks[n - 1].time_msc;
   int   safe = 0;
   while(safe < n && ticks[safe].time_msc < boundary) safe++;
   p.cursor_msc = boundary;

   int added = 0;
   for(int i = 0; i < safe; i++)
   {
      double bid = ticks[i].bid, ask = ticks[i].ask;
      if(bid <= 0.0 || ask <= 0.0) { p.rejected++; continue; }
      if((ask - bid) > InpMaxSpreadUSD) { p.rejected++; continue; }

      double px = (bid + ask) / 2.0;
      if(InpTickPriceMode == 1) px = bid;
      else if(InpTickPriceMode == 2) px = ask;

      if(p.accepted == 0 && added == 0) p.open_price = px;
      AddToRow(p, RowIndex(px), 1.0);
      p.accepted++;
      added++;
   }
   return added;
}

//--- Grow the row array on demand, keeping min_row aligned to the
//    absolute grid.
void AddToRow(SessionProfile &p, const int row, const double w)
{
   int n = ArraySize(p.volumes);
   if(n == 0)
   {
      ArrayResize(p.volumes, 1);
      p.volumes[0] = w;
      p.min_row = row;
      return;
   }
   if(row < p.min_row)
   {
      int shift = p.min_row - row;
      ArrayResize(p.volumes, n + shift);
      for(int i = n - 1; i >= 0; i--) p.volumes[i + shift] = p.volumes[i];
      for(int i = 0; i < shift; i++)  p.volumes[i] = 0.0;
      p.min_row = row;
      p.volumes[0] += w;
      return;
   }
   int idx = row - p.min_row;
   if(idx >= n)
   {
      ArrayResize(p.volumes, idx + 1);
      for(int i = n; i <= idx; i++) p.volumes[i] = 0.0;
   }
   p.volumes[idx] += w;
}
```

- [ ] **Step 4: Compile**

Copy the file to the MT5 `MQL5/Indicators/` directory and compile with MetaEditor, or from the repo root:

Run: `ls -la mt5_indicators/GoldenChart_VolumeProfile.mq5`
Expected: the file exists. Compilation happens in MetaEditor — fix any errors it reports before continuing. Per `project_goldhtf_ea_v3`, headless compile works with relative paths if a `metaeditor` CLI is available.

- [ ] **Step 5: Commit**

```bash
git add mt5_indicators/GoldenChart_VolumeProfile.mq5
git commit -m "feat(indicator): volume profile core ported to MQL5

Grid, POC, value area (both standards) and skew ported from the Python
definition with the tie branches and sign convention preserved.

Tick cursor is retain-and-replay: CopyTicksRange is inclusive on both
bounds and distinct ticks share milliseconds, so the naive cursor
double-counts every refresh.

PENDING state on a negative return -- history still syncing must not be
read as 'no ticks' and silently pinned to M1 fidelity."
```

---

### Task 10: MQL5 indicator — context, rendering, panel, alerts

**Files:**
- Modify: `mt5_indicators/GoldenChart_VolumeProfile.mq5`

**Interfaces:**
- Consumes: `SessionProfile`, `ProfileSkew`, `ValueArea`, `POCIndex` from Task 9.
- Produces: `ClassifyShape`, `ClassifyOpenType`, `ClassifyValueMigration`, `ClassifyRegime`, `DrawProfile`, `DrawPanel`, `CheckAlerts`, plus `OnInit`/`OnTimer`/`OnCalculate`/`OnDeinit`.

- [ ] **Step 1: Port the context classifiers**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
int ClassifyShape(SessionProfile &p)
{
   int occupied = 0;
   for(int i = 0; i < ArraySize(p.volumes); i++) if(p.volumes[i] > 0.0) occupied++;
   if(!p.has_skew || occupied < InpMinRowsForShape) return SHAPE_UNKNOWN;
   if(p.skew <= -InpSkewThreshold) return SHAPE_P;
   if(p.skew >=  InpSkewThreshold) return SHAPE_B;
   return SHAPE_D;
}

string ClassifyOpenType(const double open_price, const SessionProfile &prior)
{
   if(open_price > prior.high) return "OPEN_ABOVE_RANGE";
   if(open_price < prior.low)  return "OPEN_BELOW_RANGE";
   if(open_price > prior.vah)  return "OPEN_ABOVE_VA";
   if(open_price < prior.val)  return "OPEN_BELOW_VA";
   return "OPEN_INSIDE_VA";
}

string ClassifyValueMigration(const SessionProfile &today, const SessionProfile &prior)
{
   // Containment BEFORE direction, so inside/engulfing days are never
   // mislabelled as drift.
   if(today.val >= prior.val && today.vah <= prior.vah) return "INSIDE";
   if(today.val <= prior.val && today.vah >= prior.vah) return "ENGULFING";
   if(today.val >  prior.vah) return "HIGHER";
   if(today.vah <  prior.val) return "LOWER";
   return (today.vah > prior.vah) ? "OVERLAPPING_HIGHER" : "OVERLAPPING_LOWER";
}

string ClassifyRegime(const int shape, const double elapsed_pct, const bool is_developing)
{
   // Every session looks like a P or a b before it has traded both ways.
   if(is_developing && elapsed_pct < InpRegimeMinElapsed) return "FORMING";
   switch(shape)
   {
      case SHAPE_D: return "BALANCED";
      case SHAPE_P: return "OUT OF BALANCE UP";
      case SHAPE_B: return "OUT OF BALANCE DOWN";
   }
   return "UNCLEAR";
}
```

- [ ] **Step 2: Add rendering with the object budget guard**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
int g_objects = 0;

bool BudgetOK()
{
   if(g_objects < InpMaxObjects) return true;
   static bool warned = false;
   if(!warned)
   {
      PrintFormat("%s object budget %d reached -- suppressing further drawing",
                  PFX, InpMaxObjects);
      warned = true;
   }
   return false;
}

void DrawLevel(const string id, const datetime t0, const datetime t1,
               const double price, const color clr, const int width,
               const ENUM_LINE_STYLE style, const string label)
{
   if(!BudgetOK()) return;
   string name = PFX + id;
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_TREND, 0, t0, price, t1, price);
      g_objects++;
   }
   ObjectMove(0, name, 0, t0, price);
   ObjectMove(0, name, 1, t1, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);

   if(label == "") return;
   string lname = PFX + id + "_lbl";
   if(ObjectFind(0, lname) < 0)
   {
      ObjectCreate(0, lname, OBJ_TEXT, 0, t1, price);
      g_objects++;
   }
   ObjectMove(0, lname, 0, t1, price);
   ObjectSetString(0, lname, OBJPROP_TEXT, label);
   ObjectSetInteger(0, lname, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lname, OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, lname, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, lname, OBJPROP_SELECTABLE, false);
}

//--- Histogram rows grow rightward from the session's left edge and never
//    overflow into the next session. Only the most recent
//    InpHistogramProfiles sessions get one: ~400 rows/session means ten
//    sessions would be 4,000 objects and the chart would crawl.
void DrawHistogram(const SessionProfile &p, const int seq)
{
   double vmax = 0.0;
   for(int i = 0; i < ArraySize(p.volumes); i++) if(p.volumes[i] > vmax) vmax = p.volumes[i];
   if(vmax <= 0.0) return;
   long span = (long)(p.end - p.start);

   for(int i = 0; i < ArraySize(p.volumes); i++)
   {
      if(p.volumes[i] <= 0.0) continue;
      if(!BudgetOK()) return;
      int row = p.min_row + i;
      bool inVA = (row >= p.val_row && row <= p.vah_row);
      double frac = p.volumes[i] / vmax;
      datetime t1 = p.start + (datetime)(long)(span * frac * InpHistogramWidthPct);

      string name = StringFormat("%sHIST_%d_%d", PFX, seq, row);
      if(ObjectFind(0, name) < 0)
      {
         ObjectCreate(0, name, OBJ_RECTANGLE, 0, p.start, RowLow(row), t1, RowHigh(row));
         g_objects++;
      }
      ObjectMove(0, name, 0, p.start, RowLow(row));
      ObjectMove(0, name, 1, t1, RowHigh(row));
      ObjectSetInteger(0, name, OBJPROP_COLOR, inVA ? clrSteelBlue : clrDimGray);
      ObjectSetInteger(0, name, OBJPROP_FILL, true);
      ObjectSetInteger(0, name, OBJPROP_BACK, true);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   }
}
```

- [ ] **Step 3: Add the panel**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//--- The panel's honesty markers -- source, skew values and the
//    UNCALIBRATED warning -- are NOT suppressible. A letter is never shown
//    without its skew value beside it.
void PanelLine(const int idx, const string text, const color clr)
{
   string name = StringFormat("%sPANEL_%d", PFX, idx);
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 18 + idx * 13);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      g_objects++;
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

void DrawPanel(SessionProfile &dev, SessionProfile &prior, SessionProfile &comp,
               const double elapsed_pct)
{
   if(!InpShowPanel) return;
   int r = 0;

   string calib = (InpSkewThreshold <= 0.0)
                  ? "!! UNCALIBRATED"
                  : StringFormat("skewT %.2f", InpSkewThreshold);
   PanelLine(r++, StringFormat("%s VOLUME PROFILE (tick density)  src %s  %s",
                               _Symbol, SourceName(dev.source), calib),
             (InpSkewThreshold <= 0.0) ? clrOrangeRed : clrSilver);

   PanelLine(r++, "- DEVELOPING ----------------------", clrDimGray);
   if(dev.valid)
   {
      string sk = dev.has_skew ? StringFormat("%+.2f", dev.skew) : "n/a";
      PanelLine(r++, StringFormat(" shape %-13s skew %s", ShapeName(dev.shape), sk), clrWhite);
      PanelLine(r++, StringFormat(" regime %s",
                                  ClassifyRegime(dev.shape, elapsed_pct, true)), clrWhite);
      if(prior.valid)
         PanelLine(r++, StringFormat(" open  %s", ClassifyOpenType(dev.open_price, prior)), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  dev.vpoc, dev.vah, dev.val), clrWhite);
      if(dev.has_ib)
         PanelLine(r++, StringFormat(" IB   %.2f - %.2f (%dm)",
                                     dev.ib_low, dev.ib_high, InpIBMinutes), clrWhite);
   }

   if(prior.valid)
   {
      PanelLine(r++, "- PRIOR SESSION -------------------", clrDimGray);
      string sk = prior.has_skew ? StringFormat("%+.2f", prior.skew) : "n/a";
      string mig = dev.valid ? ClassifyValueMigration(dev, prior) : "n/a";
      PanelLine(r++, StringFormat(" shape %-4s skew %s  value %s",
                                  ShapeName(prior.shape), sk, mig), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  prior.vpoc, prior.vah, prior.val), clrWhite);
   }

   if(comp.valid)
   {
      PanelLine(r++, StringFormat("- COMPOSITE %dd -------------------", InpCompositeDays), clrDimGray);
      string sk = comp.has_skew ? StringFormat("%+.2f", comp.skew) : "n/a";
      PanelLine(r++, StringFormat(" shape %-4s skew %s", ShapeName(comp.shape), sk), clrWhite);
      PanelLine(r++, StringFormat(" VPOC %.2f  VAH %.2f  VAL %.2f",
                                  comp.vpoc, comp.vah, comp.val), clrWhite);
   }

   PanelLine(r++, StringFormat(" ticks %d  rejected %d  elapsed %.0f%%",
                               dev.accepted, dev.rejected, elapsed_pct * 100.0), clrSilver);
}
```

- [ ] **Step 4: Add alerts with the re-arm band**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//--- A touch is defined at TICK level: the current bid crosses the level
//    since the previous tick. Bar-based detection would miss intrabar tags,
//    which is exactly the case that matters live.
double g_last_bid = 0.0;

struct AlertState { double price; bool fired; bool armed; };
AlertState g_alerts[16];
int        g_alert_count = 0;

void ArmAlert(const double price)
{
   if(g_alert_count >= 16) return;
   g_alerts[g_alert_count].price = price;
   g_alerts[g_alert_count].fired = false;
   g_alerts[g_alert_count].armed = true;
   g_alert_count++;
}

void CheckAlerts(const string context)
{
   if(!InpAlertsOn) return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if(g_last_bid <= 0.0) { g_last_bid = bid; return; }

   for(int i = 0; i < g_alert_count; i++)
   {
      double lvl = g_alerts[i].price;
      bool crossed = (g_last_bid < lvl && bid >= lvl) || (g_last_bid > lvl && bid <= lvl);

      // Re-arm only once price has left by the band, so a level cannot
      // machine-gun while price grinds along it.
      if(g_alerts[i].fired && MathAbs(bid - lvl) >= InpAlertRearmUSD)
         { g_alerts[i].fired = false; g_alerts[i].armed = true; }

      if(crossed && g_alerts[i].armed && !g_alerts[i].fired)
      {
         string msg = StringFormat("%s %s touched %.2f", _Symbol, context, lvl);
         Alert(msg);
         if(InpSendNotifications) SendNotification(msg);
         g_alerts[i].fired = true;
         g_alerts[i].armed = false;
      }
   }
   g_last_bid = bid;
}
```

- [ ] **Step 5: Wire the lifecycle**

Append `OnInit`, `OnTimer`, `OnCalculate` and `OnDeinit`. `OnTimer` fires every `InpRefreshSec` and must, in order: detect D1 rollover (freeze the developing profile into the cache, start a fresh one with a new cursor); call `AccumulateTicksIncremental` and honour the `PENDING`/retry/`M1` state machine; resolve levels, skew and shape; rebuild the composite via the cached histograms; redraw; run `CheckAlerts`. `OnDeinit` must call `ObjectsDeleteAll(0, PFX)`.

- [ ] **Step 6: Compile and attach to a live XAUUSD chart**

Compile in MetaEditor, attach to the suffixed gold chart (the broker symbol is `XAUUSD.p` per `project_broker_symbol` — attach to the suffixed chart, not a bare `XAUUSD` one).

Verify on the chart:
- panel shows `src PENDING` briefly on first attach, then `src TICK`
- panel shows `!! UNCALIBRATED` if `InpSkewThreshold` is still 0
- VPOC/VAH/VAL lines are drawn with named labels
- `ticks` climbs and `rejected` stays small
- no horizontal scrollbar of objects; terminal stays responsive

- [ ] **Step 7: Commit**

```bash
git add mt5_indicators/GoldenChart_VolumeProfile.mq5
git commit -m "feat(indicator): context layer, rendering, panel and alerts

Panel honesty markers are not suppressible: tick-density wording, the
TICK/M1/PENDING source, the UNCALIBRATED warning, and a skew value beside
every shape letter.

Histogram is capped to the most recent profiles with a hard object budget
-- 400 rows x 10 sessions would be 4,000 objects.

Alerts fire on tick-level bid crossings with a re-arm band."
```

---

### Task 11: Parity harness and documentation

**Files:**
- Modify: `mt5_indicators/GoldenChart_VolumeProfile.mq5` (CSV export)
- Create: `scripts/check_volume_profile_parity.py`
- Modify: `mt5_indicators/README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `data/parity/vp_histogram.csv` (columns `session,row,volume`), `data/parity/vp_levels.csv` (columns `session,vpoc,vah,val,low,high,skew,shape,open_type,value_migration,regime`), and a parity checker exiting non-zero on mismatch.

- [ ] **Step 1: Add CSV export to the indicator**

Append to `mt5_indicators/GoldenChart_VolumeProfile.mq5`:

```cpp
//--- Export the indicator's OWN histogram plus the values it derived from it.
//    Python then recomputes every derived value from this exact histogram and
//    requires an exact match. This is what pins the algorithm layer; the raw
//    tick feeds differ between broker and Dukascopy and can never be compared.
void ExportParityCSV(SessionProfile &profiles[], const int count)
{
   int fh = FileOpen("vp_histogram.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) { PrintFormat("%s export failed: %d", PFX, GetLastError()); return; }
   FileWrite(fh, "session", "row", "volume");
   for(int s = 0; s < count; s++)
   {
      if(!profiles[s].valid) continue;
      string tag = TimeToString(profiles[s].start, TIME_DATE);
      for(int i = 0; i < ArraySize(profiles[s].volumes); i++)
         if(profiles[s].volumes[i] > 0.0)
            FileWrite(fh, tag, profiles[s].min_row + i,
                      DoubleToString(profiles[s].volumes[i], 8));
   }
   FileClose(fh);

   fh = FileOpen("vp_levels.csv", FILE_WRITE | FILE_CSV | FILE_ANSI, ',');
   if(fh == INVALID_HANDLE) return;
   FileWrite(fh, "session", "row_size", "value_area_pct", "va_algorithm",
             "skew_threshold", "min_rows_for_shape",
             "vpoc", "vah", "val", "low", "high", "skew", "shape");
   for(int s = 0; s < count; s++)
   {
      if(!profiles[s].valid) continue;
      FileWrite(fh, TimeToString(profiles[s].start, TIME_DATE),
                DoubleToString(InpRowSize, 8), DoubleToString(InpValueAreaPct, 8),
                IntegerToString(InpVAAlgorithm), DoubleToString(InpSkewThreshold, 8),
                IntegerToString(InpMinRowsForShape),
                DoubleToString(profiles[s].vpoc, 8), DoubleToString(profiles[s].vah, 8),
                DoubleToString(profiles[s].val, 8), DoubleToString(profiles[s].low, 8),
                DoubleToString(profiles[s].high, 8),
                profiles[s].has_skew ? DoubleToString(profiles[s].skew, 8) : "",
                ShapeName(profiles[s].shape));
   }
   FileClose(fh);
   PrintFormat("%s parity CSVs written to the Files directory", PFX);
}
```

- [ ] **Step 2: Write the parity checker**

Create `scripts/check_volume_profile_parity.py`:

```python
#!/usr/bin/env python3
"""
Parity harness -- the drift guard for the MQL5 volume-profile port.

The definition lives in two languages and can diverge silently. This script
re-runs the Python definition over the EXACT histogram the indicator computed
(which is why the indicator exports its whole histogram, not just its levels)
and requires an exact match on every derived value.

WHAT THIS CAN AND CANNOT PROVE. Dukascopy ticks are not the broker's ticks, so
absolute row volumes will never agree and a cross-vendor comparison would be a
FAKE test -- passing or failing for reasons unrelated to correctness. So parity
is taken at the algorithm layer: given the indicator's own histogram, Python
must derive the same POC, value area, skew and shape. Every line of logic is
covered; only the raw feed differs, and that difference is disclosed.

Usage:
  python scripts/check_volume_profile_parity.py --dir data/parity
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.microstructure import volume_profile as vp        # noqa: E402
from src.microstructure import profile_context as pc       # noqa: E402

PRICE_TOL = 1e-6
SKEW_TOL = 1e-4
MAX_REPORTED = 25


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/parity")
    args = ap.parse_args()
    d = PROJECT_ROOT / args.dir

    hist_df = pd.read_csv(d / "vp_histogram.csv")
    lvl_df = pd.read_csv(d / "vp_levels.csv")
    if lvl_df.empty:
        print("PARITY FAILED -- no sessions exported")
        return 1

    mismatches: list[str] = []

    def fail(msg: str) -> None:
        if len(mismatches) < MAX_REPORTED:
            mismatches.append(msg)

    for _, row in lvl_df.iterrows():
        tag = row["session"]
        g = hist_df[hist_df.session == tag]
        if g.empty:
            fail(f"{tag}: no histogram rows exported")
            continue

        rows = g["row"].to_numpy(dtype=np.int64)
        vols = g["volume"].to_numpy(dtype=float)
        lo, hi = int(rows.min()), int(rows.max())
        dense = np.zeros(hi - lo + 1)
        dense[rows - lo] = vols

        params = vp.ProfileParams(
            row_size=float(row["row_size"]),
            value_area_pct=float(row["value_area_pct"]),
            va_algorithm="single_row" if int(row["va_algorithm"]) == 0 else "two_row",
        )
        hist = vp.Histogram(lo, dense, params.row_size, int(vols.sum()), 0)
        prof = vp.build_profile(hist, params)
        if prof is None:
            fail(f"{tag}: Python built no profile from the exported histogram")
            continue

        for name, got, want in (("vpoc", prof.vpoc, float(row["vpoc"])),
                                ("vah", prof.vah, float(row["vah"])),
                                ("val", prof.val, float(row["val"])),
                                ("low", prof.low, float(row["low"])),
                                ("high", prof.high, float(row["high"]))):
            if abs(got - want) > PRICE_TOL:
                fail(f"{tag}: {name} python={got:.6f} mql5={want:.6f}")

        cparams = pc.ContextParams(
            skew_threshold=float(row["skew_threshold"]),
            min_rows_for_shape=int(row["min_rows_for_shape"]),
        )
        read = pc.classify_shape(prof, cparams)

        mql_skew = row["skew"]
        if pd.isna(mql_skew) or mql_skew == "":
            if read.skew is not None:
                fail(f"{tag}: python has skew {read.skew:.6f}, mql5 has none")
        elif read.skew is None:
            fail(f"{tag}: mql5 has skew {float(mql_skew):.6f}, python has none")
        elif abs(read.skew - float(mql_skew)) > SKEW_TOL:
            fail(f"{tag}: skew python={read.skew:.6f} mql5={float(mql_skew):.6f}")

        if read.shape != row["shape"]:
            fail(f"{tag}: shape python={read.shape} mql5={row['shape']}")

    ok = not mismatches
    print(f"{'PARITY OK' if ok else 'PARITY FAILED'} -- "
          f"{len(lvl_df)} sessions, {len(hist_df)} histogram rows")
    for m in mismatches:
        print(f"  {m}")
    if not ok:
        print("\nDo NOT relax the tolerances to make this pass. A mismatch means "
              "the two implementations genuinely disagree.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run parity end to end**

Set `InpExportCSV = true` on the chart, let it write, then copy both CSVs from the MT5 Files directory to `data/parity/` and run:

Run: `source venv/bin/activate && python scripts/check_volume_profile_parity.py --dir data/parity`
Expected: `PARITY OK -- N sessions, M histogram rows`

If it fails, fix the **implementation** that is wrong. Do not adjust `PRICE_TOL` or `SKEW_TOL`.

- [ ] **Step 4: Verify the parity harness can actually fail**

A parity check that cannot fail proves nothing (see `project_plumbing_null_trap`). Temporarily change one MQL5 tie branch — in `ValueArea`, flip `takeUp = (dUp <= dDn)` to `(dUp < dDn)` — recompile, re-export, and re-run the checker.

Expected: `PARITY FAILED` with a `vah` or `val` mismatch on at least one session.

Then revert the change, recompile, and confirm `PARITY OK` again.

- [ ] **Step 5: Update the README**

Add a `GoldenChart_VolumeProfile` section to `mt5_indicators/README.md` covering: what it draws; that volume is **tick density, not traded volume**; the `TICK`/`M1`/`PENDING` source field; that the skew threshold is calibrated by `scripts/calibrate_profile_shape.py` and the report is generated, never hand-edited; that the **node thresholds are uncalibrated display heuristics**; that IB is **standard range-based IB and not Fabio's IVB**; that there is deliberately **no delta/CVD/footprint** because a gold CFD cannot support one honestly; and the parity workflow with its "do not relax the tolerances" warning.

- [ ] **Step 6: Run the whole suite**

Run: `pytest tests/unit/ -q`
Expected: PASS, with the pre-existing 880 passing tests unaffected.

- [ ] **Step 7: Commit**

```bash
git add mt5_indicators/GoldenChart_VolumeProfile.mq5 scripts/check_volume_profile_parity.py
git add -f mt5_indicators/README.md
git commit -m "feat(profile): parity harness and README

Parity is taken at the algorithm layer: Python recomputes every derived
value from the indicator's OWN exported histogram. Cross-vendor volume
comparison is impossible and would be a fake test.

Step 4 of the plan verifies the harness can actually fail by inverting a
tie branch -- a parity check that cannot fail proves nothing."
```

---

## Self-Review

**Spec coverage.** §1.1 tick-density honesty → Tasks 1, 10 (panel wording), 11 (README). §2 portability → Task 11 README, and the absence of delta is enforced by there being no such task. §3 architecture and scope boundary → Task 1 Step 5. §4 sessions → Task 9 inputs, Task 10 Step 5. §5 binning → Task 1. §6 accumulation and spread filter → Task 1. §7 live correctness → Task 3 (cursor), Task 9 Step 3 (PENDING/M1), Task 10 Step 5 (rollover), Task 10 Step 3 (integrity readout). §8 POC/VA → Task 2. §9 shape and calibration → Tasks 4 and 8. §10 open type and migration → Task 5. §11 regime → Task 5. §12 IB → Task 7. §13 composite → Task 7. §14 nodes → Task 6. §15 naked POC → Task 6. §16 rendering → Task 10. §17 panel → Task 10. §18 alerts → Task 10. §19 parity → Task 11. §20 tests → distributed across every task. §21 files → all created.

**Two deliberate deviations, both flagged in place:** the Python module is split in two (noted under **Spec**), and §20's skew-invariance test was restated as price-scale and volume-scale invariance, which are the exact properties — the spec was amended to match.

**Placeholder scan.** One intentional stub: Task 10 Step 5 describes the `OnTimer` wiring in prose with an ordered list rather than full code, because it is assembly of functions defined verbatim in Tasks 9 and 10 rather than new logic. Task 8 Step 5 leaves `<value from the report>` — that is a genuine output of Step 3, not an unresolved decision.

**Type consistency.** `Histogram` fields (`min_row`, `volumes`, `row_size`, `accepted`, `rejected`) are used identically in Tasks 1, 2, 4, 6, 7, 8, 11. `Profile` fields (`poc_row`, `val_row`, `vah_row`, `vpoc`, `val`, `vah`, `low`, `high`) match between Task 2's definition and their uses in Tasks 4, 5, 6. `ProfileParams.va_algorithm` is the string `"single_row"`/`"two_row"` in Python and the int `0`/`1` in MQL5; Task 11's parity script converts explicitly. `classify_shape` returns `ShapeRead` in Task 4 and is consumed as such in Task 11.
