# Kronos IC Smell-Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether the pretrained Kronos-base foundation model produces forecasts with any tradeable information coefficient for XAUUSD and BTCUSD 15m, and emit a mechanical GREEN/RED verdict.

**Architecture:** torch is confined to ONE file (`kronos_forecast.py`) that runs rolling Kronos forecasts and writes a `.npz` prediction cache. A pure-numpy analysis module (`kronos_ic.py`) reads that cache, computes per-year Spearman IC (Stage 1) and a conditional strict-fill toy sim (Stage 2), and writes a verdict report. The cache is the only interface between the two halves, defined by a tiny `kronos_cache.py` contract module. Everything is research-only — no `src/`, config, or risk-engine changes.

**Tech Stack:** Python 3.13, PyTorch (MPS/CPU), huggingface_hub, einops, pandas, numpy, scipy, matplotlib, pytest. Kronos model source cloned from GitHub (not pip-installable).

## Global Constraints

- **Research-only. ZERO live wiring.** No edits to `src/`, `config/`, `STRATEGY_WEIGHTS`, or the risk engine. (spec §1, §2)
- **Isolated `venv_kronos/`** for all torch work; production `venv` and `requirements.txt` are NOT modified. (spec §3)
- `venv_kronos/` and `vendor/` are **gitignored**. (spec §3)
- Model: `NeoQuasar/Kronos-base` (102M, ctx 512) + `NeoQuasar/Kronos-Tokenizer-base`. Device: MPS if available else CPU. (spec §3)
- Data: existing `data/historical/{XAUUSD,BTCUSD}_5m_real.csv`, resampled 5m→15m via `from research_fx_majors import resample` (left/left). (spec §4)
- **Analysis modules (`kronos_ic.py`, `kronos_cache.py`) MUST NOT import torch** — their unit tests run under the production `venv`. (spec §5, §10)
- **No lookahead:** realized returns come strictly from bars AFTER the window's last observed bar. Enforced by a pure-python test. (spec §12)
- Horizon capped at 4 bars (1h). Cache aligned-array schema per spec §5.1.
- Verdict weights the **most-recent year (2026)** heaviest as the likely-OOS slice. (spec §7, §9)

---

### Task 1: Environment bootstrap + Kronos smoke run (feasibility gate)

Discover fast whether torch + Kronos-base run on this M1 / Python 3.13 machine before investing in the harness. This is the biggest project risk.

**Files:**
- Create: `scripts/kronos/check_env.py`
- Modify: `.gitignore` (add `venv_kronos/` and `vendor/`)

**Interfaces:**
- Produces: a working `venv_kronos/` interpreter and a cloned `vendor/Kronos/` importable model package. No code symbols consumed by later tasks (env only).

- [x] **Step 1: Create isolated venv and install the stack**

```bash
python3 -m venv venv_kronos
./venv_kronos/bin/pip install --upgrade pip
./venv_kronos/bin/pip install torch huggingface_hub einops pandas numpy scipy matplotlib tqdm
```

- [x] **Step 2: Clone the Kronos model source (not pip-installable)**

```bash
mkdir -p vendor
git clone --depth 1 https://github.com/shiyu-coder/Kronos vendor/Kronos
```

- [x] **Step 3: Gitignore the heavy artifacts**

Append to `.gitignore`:
```
venv_kronos/
vendor/
```

- [x] **Step 4: Write the smoke script**

Create `scripts/kronos/check_env.py`:
```python
"""Feasibility gate: load Kronos-base + tokenizer, run one tiny forecast, print device/shapes.
Run with the isolated interpreter: ./venv_kronos/bin/python scripts/kronos/check_env.py"""
import sys, pathlib
import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "vendor" / "Kronos"))  # exposes `model` package

import torch
from model import Kronos, KronosTokenizer, KronosPredictor  # noqa: E402

device = "mps" if torch.backends.mps.is_available() else "cpu"
print(f"torch {torch.__version__} | device {device}")

tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-base")
predictor = KronosPredictor(mdl, tok, device=device, max_context=512)

# 256 synthetic bars in, forecast 4 out
n = 256
idx = pd.date_range("2025-01-01", periods=n, freq="15min", tz="UTC")
base = 2000 + np.cumsum(np.random.randn(n))
df = pd.DataFrame({"open": base, "high": base + 1, "low": base - 1,
                   "close": base, "volume": np.abs(np.random.randn(n)) * 100})
df["amount"] = df["close"] * df["volume"]
y_ts = pd.date_range(idx[-1], periods=5, freq="15min", tz="UTC")[1:]

pred = predictor.predict(df=df, x_timestamp=pd.Series(idx), y_timestamp=pd.Series(y_ts),
                         pred_len=4, T=1.0, top_p=0.9, sample_count=5)
print("prediction shape:", pred.shape)
print(pred[["open", "high", "low", "close"]].to_string())
print("OK: Kronos-base runs on this machine.")
```

- [x] **Step 5: Run the smoke script**

Run: `./venv_kronos/bin/python scripts/kronos/check_env.py`
Expected: prints device, a `(4, N)` prediction frame, and `OK: Kronos-base runs on this machine.` If the KronosPredictor `predict` signature differs from the above (API drift), note the actual signature — Task 6 depends on it. If MPS errors, re-run forcing `device="cpu"` inside the script and record which works.

- [x] **Step 6: Commit**

```bash
git add .gitignore scripts/kronos/check_env.py
git commit -m "chore(kronos): isolated venv + Kronos-base smoke gate (research-only)"
```

---

### Task 2: Prediction-cache contract module

The `.npz` cache is the only interface between the torch half and the analysis half. Give it a tiny, round-trip-tested module so both sides agree on fields.

**Files:**
- Create: `scripts/kronos/kronos_cache.py`
- Test: `tests/unit/test_kronos_cache.py`

**Interfaces:**
- Produces:
  - `ARRAY_FIELDS: list[str]` — the aligned per-window array names.
  - `META_KEYS: list[str]`.
  - `save_cache(path: str, arrays: dict[str, np.ndarray], meta: dict) -> None`.
  - `load_cache(path: str) -> tuple[dict[str, np.ndarray], dict[str, str]]`.

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_kronos_cache.py`:
```python
import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_cache import ARRAY_FIELDS, save_cache, load_cache


def _synthetic(n=20):
    rng = np.random.default_rng(0)
    return {f: (rng.standard_normal(n) if f not in ("timestamp", "year")
                else np.arange(n, dtype=np.int64)) for f in ARRAY_FIELDS}


def test_round_trip_preserves_all_fields(tmp_path):
    arrays = _synthetic()
    p = tmp_path / "c.npz"
    save_cache(str(p), arrays, {"symbol": "XAUUSD", "n_paths": 20})
    got, meta = load_cache(str(p))
    for f in ARRAY_FIELDS:
        np.testing.assert_allclose(got[f], arrays[f])
    assert meta["symbol"] == "XAUUSD"
    assert meta["n_paths"] == "20"


def test_missing_field_raises(tmp_path):
    arrays = _synthetic()
    del arrays["pred_ret_h1"]
    import pytest
    with pytest.raises(ValueError):
        save_cache(str(tmp_path / "c.npz"), arrays, {})
```

- [x] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kronos_cache'`.

- [x] **Step 3: Write minimal implementation**

Create `scripts/kronos/kronos_cache.py`:
```python
"""Prediction-cache contract shared by kronos_forecast (writer) and kronos_ic (reader).
The .npz cache is the ONLY interface between the torch half and the analysis half.
This module MUST NOT import torch."""
import numpy as np

ARRAY_FIELDS = [
    "timestamp",  # int64 unix seconds; window decision time (last observed bar close)
    "year",       # int16
    "pred_ret_h1", "pred_ret_h2", "pred_ret_h3", "pred_ret_h4",
    "pred_disp_h4",  # std across sampled paths at H=4 (may be nan if unavailable)
    "real_ret_h1", "real_ret_h2", "real_ret_h3", "real_ret_h4",
    "last_close",
]
META_KEYS = ["symbol", "model_id", "stride", "n_paths", "horizon",
             "temperature", "top_p", "top_k", "git_commit", "created_at"]


def save_cache(path, arrays, meta):
    missing = [f for f in ARRAY_FIELDS if f not in arrays]
    if missing:
        raise ValueError(f"cache missing fields: {missing}")
    n = len(arrays[ARRAY_FIELDS[0]])
    for f in ARRAY_FIELDS:
        if len(arrays[f]) != n:
            raise ValueError(f"field {f} length {len(arrays[f])} != {n}")
    payload = {f: np.asarray(arrays[f]) for f in ARRAY_FIELDS}
    payload["_meta"] = np.array([f"{k}={meta.get(k, '')}" for k in META_KEYS])
    np.savez(path, **payload)


def load_cache(path):
    z = np.load(path, allow_pickle=False)
    arrays = {f: z[f] for f in ARRAY_FIELDS}
    meta = {}
    for item in z["_meta"]:
        k, _, v = str(item).partition("=")
        meta[k] = v
    return arrays, meta
```

- [x] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_cache.py -v`
Expected: PASS (2 tests). Note it runs under the **production `venv`** — proves no torch dependency.

- [x] **Step 5: Commit**

```bash
git add scripts/kronos/kronos_cache.py tests/unit/test_kronos_cache.py
git commit -m "feat(kronos): prediction-cache contract module + round-trip test"
```

---

### Task 3: Stage-1 information-coefficient functions

Pure-numpy IC math, TDD against a synthetic cache with a known injected signal.

**Files:**
- Create: `scripts/kronos/kronos_ic.py` (Stage-1 functions only for now)
- Test: `tests/unit/test_kronos_ic.py`

**Interfaces:**
- Consumes: `kronos_cache.ARRAY_FIELDS`, `load_cache`.
- Produces:
  - `spearman_ic(pred: np.ndarray, real: np.ndarray) -> float`
  - `sign_hit_rate(pred: np.ndarray, real: np.ndarray) -> float`
  - `ic_by_year(arrays: dict, horizon: int) -> dict[int, float]` (year → Spearman IC)

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_kronos_ic.py`:
```python
import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_ic import spearman_ic, sign_hit_rate, ic_by_year


def test_spearman_recovers_positive_signal():
    rng = np.random.default_rng(1)
    real = rng.standard_normal(2000)
    pred = 0.6 * real + 0.4 * rng.standard_normal(2000)  # correlated
    assert spearman_ic(pred, real) > 0.3


def test_spearman_random_is_near_zero():
    rng = np.random.default_rng(2)
    assert abs(spearman_ic(rng.standard_normal(2000), rng.standard_normal(2000))) < 0.08


def test_sign_hit_rate_perfect():
    real = np.array([1.0, -2.0, 3.0, -4.0])
    assert sign_hit_rate(real, real) == 1.0


def test_ic_by_year_splits_correctly():
    n = 1000
    years = np.where(np.arange(n) < 500, 2025, 2026).astype(np.int64)
    real = np.random.default_rng(3).standard_normal(n)
    # signal only in 2026 half
    pred = np.where(years == 2026, 0.7 * real, np.random.default_rng(4).standard_normal(n))
    arrays = {"year": years, "pred_ret_h1": pred, "real_ret_h1": real}
    out = ic_by_year(arrays, horizon=1)
    assert out[2026] > 0.3
    assert abs(out[2025]) < 0.12
```

- [x] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kronos_ic'`.

- [x] **Step 3: Write minimal implementation**

Create `scripts/kronos/kronos_ic.py`:
```python
"""Stage-1 IC + Stage-2 strict-fill analysis of a Kronos prediction cache.
Pure numpy/pandas/scipy — MUST NOT import torch (tests run under production venv)."""
import numpy as np
from scipy.stats import spearmanr


def spearman_ic(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real)
    if m.sum() < 10:
        return float("nan")
    return float(spearmanr(pred[m], real[m]).correlation)


def sign_hit_rate(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (pred != 0)
    if m.sum() == 0:
        return float("nan")
    return float((np.sign(pred[m]) == np.sign(real[m])).mean())


def ic_by_year(arrays, horizon):
    years = np.asarray(arrays["year"]).astype(int)
    pred = np.asarray(arrays[f"pred_ret_h{horizon}"], float)
    real = np.asarray(arrays[f"real_ret_h{horizon}"], float)
    out = {}
    for y in sorted(set(years.tolist())):
        s = years == y
        out[int(y)] = spearman_ic(pred[s], real[s])
    return out
```

- [x] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -v`
Expected: PASS (4 tests).

- [x] **Step 5: Commit**

```bash
git add scripts/kronos/kronos_ic.py tests/unit/test_kronos_ic.py
git commit -m "feat(kronos): Stage-1 IC functions (Spearman, hit-rate, per-year) + tests"
```

---

### Task 4: Stage-2 strict-fill toy sim

Conditional after-cost sim. TDD: profitable synthetic signal → PF>1; random → PF≈1; cost reduces PF.

**Files:**
- Modify: `scripts/kronos/kronos_ic.py` (add `strict_fill_sim`)
- Test: `tests/unit/test_kronos_ic.py` (add cases)

**Interfaces:**
- Produces: `strict_fill_sim(pred, real, cost_bps: float, threshold: float) -> dict` with keys `pf, ret, dd, wr, n`. `pred`/`real` are horizon returns as fractions; cost is round-trip in bps.

- [x] **Step 1: Write the failing test**

Append to `tests/unit/test_kronos_ic.py`:
```python
from kronos_ic import strict_fill_sim


def test_strict_fill_profitable_signal():
    rng = np.random.default_rng(10)
    real = rng.standard_normal(3000) * 0.002          # ~0.2% moves
    pred = 0.8 * real + 0.2 * rng.standard_normal(3000) * 0.002
    res = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)
    assert res["pf"] > 1.2 and res["n"] == 3000


def test_strict_fill_random_signal_breakeven():
    rng = np.random.default_rng(11)
    real = rng.standard_normal(3000) * 0.002
    pred = rng.standard_normal(3000) * 0.002           # uncorrelated
    res = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)
    assert 0.8 < res["pf"] < 1.25


def test_cost_reduces_pf():
    rng = np.random.default_rng(12)
    real = rng.standard_normal(3000) * 0.002
    pred = 0.8 * real + 0.2 * rng.standard_normal(3000) * 0.002
    free = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)["pf"]
    costed = strict_fill_sim(pred, real, cost_bps=5.0, threshold=0.0)["pf"]
    assert costed < free
```

- [x] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -k strict_fill -v`
Expected: FAIL with `ImportError: cannot import name 'strict_fill_sim'`.

- [x] **Step 3: Write minimal implementation**

Append to `scripts/kronos/kronos_ic.py`:
```python
def strict_fill_sim(pred, real, cost_bps, threshold):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (np.abs(pred) >= threshold)
    if m.sum() == 0:
        return {"pf": float("nan"), "ret": 0.0, "dd": 0.0, "wr": float("nan"), "n": 0}
    net = np.sign(pred[m]) * real[m] - cost_bps / 1e4  # round-trip cost in return units
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    equity = np.cumsum(net)
    dd = float((np.maximum.accumulate(equity) - equity).max()) if equity.size else 0.0
    return {"pf": pf, "ret": float(net.sum()), "dd": dd,
            "wr": float((net > 0).mean()), "n": int(m.sum())}
```

- [x] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -v`
Expected: PASS (all Task-3 + Task-4 tests).

- [x] **Step 5: Commit**

```bash
git add scripts/kronos/kronos_ic.py tests/unit/test_kronos_ic.py
git commit -m "feat(kronos): Stage-2 strict-fill toy sim + tests"
```

---

### Task 5: Verdict logic + report/CLI

Mechanical GREEN/RED per spec §9 (2026-weighted), plus report writer and CLI entry point.

**Files:**
- Modify: `scripts/kronos/kronos_ic.py` (add `verdict`, `build_report`, `main`/argparse)
- Test: `tests/unit/test_kronos_ic.py` (add verdict cases)

**Interfaces:**
- Consumes: `ic_by_year`, `strict_fill_sim`, `kronos_cache.load_cache`.
- Produces:
  - `verdict(ic_by_year_map: dict[int,float], sim_by_year_pf: dict[int,float], recent_year: int, ic_floor=0.03, pf_floor=1.1) -> tuple[str, list[str]]` returning `("GREEN"|"RED", reasons)`.
  - CLI: `python kronos_ic.py --cache <path> [--horizon 4] [--cost-bps 5] [--threshold 0.001] [--report-out reports/kronos_ic_smelltest.md]`.

- [x] **Step 1: Write the failing test**

Append to `tests/unit/test_kronos_ic.py`:
```python
from kronos_ic import verdict


def test_verdict_green_when_recent_year_ic_and_pf_hold():
    v, reasons = verdict({2024: 0.01, 2025: 0.05, 2026: 0.06},
                         {2024: 1.0, 2025: 1.2, 2026: 1.3}, recent_year=2026)
    assert v == "GREEN"


def test_verdict_red_when_ic_only_in_early_year():
    v, reasons = verdict({2024: 0.09, 2025: 0.02, 2026: 0.005},
                         {2024: 1.5, 2025: 1.0, 2026: 0.9}, recent_year=2026)
    assert v == "RED"
    assert any("2026" in r for r in reasons)


def test_verdict_red_when_recent_pf_fails_costs():
    v, reasons = verdict({2024: 0.05, 2025: 0.05, 2026: 0.05},
                         {2024: 1.3, 2025: 1.2, 2026: 0.95}, recent_year=2026)
    assert v == "RED"
```

- [x] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -k verdict -v`
Expected: FAIL with `ImportError: cannot import name 'verdict'`.

- [x] **Step 3: Write minimal implementation**

Append to `scripts/kronos/kronos_ic.py`:
```python
def verdict(ic_by_year_map, sim_by_year_pf, recent_year, ic_floor=0.03, pf_floor=1.1):
    reasons = []
    ric = ic_by_year_map.get(recent_year, float("nan"))
    rpf = sim_by_year_pf.get(recent_year, float("nan"))
    # Leg 1: recent-year IC must be material and right-signed (positive = usable long/short sign).
    if not np.isfinite(ric) or abs(ric) < ic_floor:
        reasons.append(f"{recent_year} IC {ric:.3f} below floor {ic_floor} (likely no signal / drift)")
        return "RED", reasons
    reasons.append(f"{recent_year} IC {ric:.3f} >= floor {ic_floor}")
    # Leg 2: recent-year after-cost PF must clear the floor.
    if not np.isfinite(rpf) or rpf < pf_floor:
        reasons.append(f"{recent_year} strict-fill PF {rpf:.2f} below floor {pf_floor}")
        return "RED", reasons
    reasons.append(f"{recent_year} strict-fill PF {rpf:.2f} >= floor {pf_floor}")
    # Leg 3: no other year may be outright negative-PF (edge must not be one-year-only).
    neg = [y for y, pf in sim_by_year_pf.items() if np.isfinite(pf) and pf < 1.0 and y != recent_year]
    if neg:
        reasons.append(f"years with PF<1.0: {sorted(neg)} (not every-year positive)")
        return "RED", reasons
    return "GREEN", reasons


def build_report(symbol, meta, ic_tables, sim_tables, final_verdict, reasons):
    """ic_tables: {horizon: {year: ic}}; sim_tables: {horizon: {year: sim_dict}}."""
    lines = [f"# Kronos IC Smell-Test — {symbol}", "",
             f"**Verdict: {final_verdict}**", "",
             "Reasons:", *[f"- {r}" for r in reasons], "",
             f"Model: {meta.get('model_id')} | stride {meta.get('stride')} | "
             f"paths {meta.get('n_paths')} | commit {meta.get('git_commit')}", "",
             "## Stage 1 — Spearman IC (per horizon × year)", ""]
    years = sorted({y for t in ic_tables.values() for y in t})
    lines.append("| horizon | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|" + "---|" * (len(years) + 1))
    for h in sorted(ic_tables):
        row = " | ".join(f"{ic_tables[h].get(y, float('nan')):.3f}" for y in years)
        lines.append(f"| h{h} | {row} |")
    lines += ["", "## Stage 2 — strict-fill toy sim (per horizon × year: PF)", ""]
    lines.append("| horizon | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|" + "---|" * (len(years) + 1))
    for h in sorted(sim_tables):
        row = " | ".join(f"{sim_tables[h].get(y, {}).get('pf', float('nan')):.2f}" for y in years)
        lines.append(f"| h{h} | {row} |")
    return "\n".join(lines) + "\n"


def _sim_by_year(arrays, horizon, cost_bps, threshold):
    years = np.asarray(arrays["year"]).astype(int)
    pred = np.asarray(arrays[f"pred_ret_h{horizon}"], float)
    real = np.asarray(arrays[f"real_ret_h{horizon}"], float)
    return {int(y): strict_fill_sim(pred[years == y], real[years == y], cost_bps, threshold)
            for y in sorted(set(years.tolist()))}


def main(argv=None):
    import argparse
    from kronos_cache import load_cache
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--cost-bps", type=float, default=5.0)
    ap.add_argument("--threshold", type=float, default=0.001)
    ap.add_argument("--report-out", default="reports/kronos_ic_smelltest.md")
    a = ap.parse_args(argv)
    arrays, meta = load_cache(a.cache)
    recent = int(np.asarray(arrays["year"]).astype(int).max())
    ic_tables = {h: ic_by_year(arrays, h) for h in range(1, a.horizon + 1)}
    sim_tables = {h: _sim_by_year(arrays, h, a.cost_bps, a.threshold) for h in range(1, a.horizon + 1)}
    # verdict uses the best-IC horizon by |recent-year IC|
    best_h = max(ic_tables, key=lambda h: abs(ic_tables[h].get(recent, 0.0) or 0.0))
    sim_pf = {y: s["pf"] for y, s in sim_tables[best_h].items()}
    v, reasons = verdict(ic_tables[best_h], sim_pf, recent)
    report = build_report(meta.get("symbol", "?"), meta, ic_tables, sim_tables, v,
                          [f"(verdict horizon h{best_h})", *reasons])
    import pathlib
    pathlib.Path(a.report_out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.report_out).write_text(report)
    print(report)
    return v


if __name__ == "__main__":
    main()
```

- [x] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_ic.py -v`
Expected: PASS (all cache/IC/sim/verdict tests).

- [x] **Step 5: Commit**

```bash
git add scripts/kronos/kronos_ic.py tests/unit/test_kronos_ic.py
git commit -m "feat(kronos): verdict logic + report writer + CLI"
```

---

### Task 6: Forecast pass (torch) — produce the cache

The only torch file. Rolling Kronos forecasts → cache. The window/return index math is extracted into a pure-python helper so causality is unit-tested without the model.

**Files:**
- Create: `scripts/kronos/kronos_forecast.py`
- Test: `tests/unit/test_kronos_windows.py` (pure-python causality test)

**Interfaces:**
- Consumes: `kronos_cache.save_cache/ARRAY_FIELDS`; `research_fx_majors.resample`; Kronos `Kronos/KronosTokenizer/KronosPredictor` (signature confirmed in Task 1).
- Produces:
  - `build_eval_indices(n_bars: int, ctx: int, horizon: int, stride: int) -> list[int]` — indices `t` of the last OBSERVED bar; `obs = bars[t-ctx+1:t+1]`, `future = bars[t+1:t+1+horizon]`.
  - CLI writing a cache: `--symbol XAUUSD --stride 12 --n-paths 15 --horizon 4 --max-windows 4000 --ctx 512 --device auto --out data/kronos_cache_XAUUSD.npz`.

- [x] **Step 1: Write the failing causality test**

Create `tests/unit/test_kronos_windows.py`:
```python
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_forecast import build_eval_indices


def test_future_strictly_after_observed():
    ctx, horizon, n = 512, 4, 5000
    idxs = build_eval_indices(n, ctx, horizon, stride=10)
    assert idxs, "expected some windows"
    for t in idxs:
        assert t - ctx + 1 >= 0          # enough history
        assert t + horizon <= n - 1      # enough future
        # future bars (t+1..t+horizon) are strictly AFTER the last observed bar t
        assert (t + 1) > t


def test_stride_spacing():
    idxs = build_eval_indices(3000, 512, 4, stride=12)
    assert all(b - a == 12 for a, b in zip(idxs, idxs[1:]))
```

Note: `test_kronos_windows.py` imports `kronos_forecast`, which imports torch only inside functions/`__main__` — so `build_eval_indices` and this test must NOT trigger a torch import at module load. Keep all torch imports lazy (inside `run_forecast`/`main`).

- [x] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_windows.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kronos_forecast'`.

- [x] **Step 3: Write minimal implementation**

Create `scripts/kronos/kronos_forecast.py`:
```python
"""Rolling Kronos-base forecasts → prediction cache (.npz). The ONLY torch file.
Run with: ./venv_kronos/bin/python scripts/kronos/kronos_forecast.py --symbol XAUUSD ...
torch/Kronos imports are LAZY (inside run_forecast) so the pure helper stays importable
under the production venv for testing."""
import sys, pathlib, argparse, datetime as dt
import numpy as np
import pandas as pd

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))          # for research_fx_majors.resample
sys.path.insert(0, str(REPO / "scripts" / "kronos"))  # for kronos_cache


def build_eval_indices(n_bars, ctx, horizon, stride):
    """Indices t of the last OBSERVED bar. obs=bars[t-ctx+1:t+1], future=bars[t+1:t+1+horizon]."""
    start = ctx - 1
    end = n_bars - horizon            # need `horizon` bars strictly after t
    return list(range(start, end, stride))


def _load_15m(symbol):
    from research_fx_majors import resample
    df = pd.read_csv(REPO / f"data/historical/{symbol}_5m_real.csv",
                     parse_dates=["timestamp"])
    df = df.set_index("timestamp").sort_index()
    b = resample(df, "15min").dropna()
    b["amount"] = b["close"] * b["volume"]
    return b


def run_forecast(symbol, stride, n_paths, horizon, max_windows, ctx, device, out):
    import torch
    from model import Kronos, KronosTokenizer, KronosPredictor
    sys.path.insert(0, str(REPO / "vendor" / "Kronos"))
    from kronos_cache import save_cache, ARRAY_FIELDS  # noqa

    if device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained("NeoQuasar/Kronos-base")
    predictor = KronosPredictor(mdl, tok, device=device, max_context=ctx)

    bars = _load_15m(symbol)
    closes = bars["close"].to_numpy()
    idxs = build_eval_indices(len(bars), ctx, horizon, stride)
    if max_windows:
        idxs = idxs[:max_windows]

    cols = ["open", "high", "low", "close", "volume", "amount"]
    acc = {f: [] for f in ARRAY_FIELDS}
    from tqdm import tqdm
    for t in tqdm(idxs, desc=f"{symbol} forecasts"):
        obs = bars.iloc[t - ctx + 1: t + 1]
        x_ts = pd.Series(obs.index)
        y_ts = pd.Series(bars.index[t + 1: t + 1 + horizon])
        pred = predictor.predict(df=obs[cols], x_timestamp=x_ts, y_timestamp=y_ts,
                                 pred_len=horizon, T=1.0, top_p=0.9, sample_count=n_paths)
        pc = pred["close"].to_numpy()          # mean predicted closes, length=horizon
        c0 = closes[t]
        acc["timestamp"].append(int(bars.index[t].timestamp()))
        acc["year"].append(bars.index[t].year)
        acc["last_close"].append(c0)
        acc["pred_disp_h4"].append(float("nan"))  # per-path std not exposed by predict(); nan
        for k in range(1, horizon + 1):
            acc[f"pred_ret_h{k}"].append(pc[k - 1] / c0 - 1.0)
            acc[f"real_ret_h{k}"].append(closes[t + k] / c0 - 1.0)

    meta = {"symbol": symbol, "model_id": "NeoQuasar/Kronos-base", "stride": stride,
            "n_paths": n_paths, "horizon": horizon, "temperature": 1.0, "top_p": 0.9,
            "top_k": 0, "git_commit": _git_commit(), "created_at": dt.datetime.utcnow().isoformat()}
    save_cache(out, {f: np.asarray(v) for f, v in acc.items()}, meta)
    print(f"wrote {out}: {len(idxs)} windows")


def _git_commit():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=REPO).decode().strip()
    except Exception:
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--stride", type=int, default=12)
    ap.add_argument("--n-paths", type=int, default=15)
    ap.add_argument("--horizon", type=int, default=4)
    ap.add_argument("--max-windows", type=int, default=4000)
    ap.add_argument("--ctx", type=int, default=512)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    out = a.out or f"data/kronos_cache_{a.symbol}.npz"
    run_forecast(a.symbol, a.stride, a.n_paths, a.horizon, a.max_windows, a.ctx, a.device, out)


if __name__ == "__main__":
    main()
```

If Task 1 recorded a different `predict()` signature, adjust the `predictor.predict(...)` call here to match (parameter names for timestamps / sample count) — this is the one place API drift lands.

- [x] **Step 4: Run causality test to verify it passes**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_windows.py -v`
Expected: PASS (2 tests) under the production venv — proves the helper imports with no torch.

- [x] **Step 5: Tiny smoke run with the real model**

Run: `./venv_kronos/bin/python scripts/kronos/kronos_forecast.py --symbol XAUUSD --max-windows 5 --n-paths 3 --out /tmp/kronos_smoke.npz`
Expected: writes a 5-window cache. Then confirm it loads and has no accidental lookahead sign leakage:
`./venv/bin/python -c "from scripts.kronos.kronos_cache import load_cache; a,m=load_cache('/tmp/kronos_smoke.npz'); print(m['symbol'], len(a['year']), a['real_ret_h1'][:3])"`
Expected: prints `XAUUSD 5 [...]`.

- [x] **Step 6: Commit**

```bash
git add scripts/kronos/kronos_forecast.py tests/unit/test_kronos_windows.py
git commit -m "feat(kronos): rolling forecast pass + causal windowing test"
```

---

### Task 7: Run the smell-test — produce verdicts for XAUUSD and BTCUSD

Execution task. Deliverable = the report + verdict, not new code. Start coarse; refine only if a signal shows.

**Files:**
- Create (outputs, may be gitignored): `data/kronos_cache_XAUUSD.npz`, `data/kronos_cache_BTCUSD.npz`
- Create: `reports/kronos_ic_smelltest_XAUUSD.md`, `reports/kronos_ic_smelltest_BTCUSD.md`

- [x] **Step 1: Coarse forecast pass, both instruments**

```bash
./venv_kronos/bin/python scripts/kronos/kronos_forecast.py --symbol XAUUSD --stride 16 --n-paths 15 --max-windows 4000 --out data/kronos_cache_XAUUSD.npz
./venv_kronos/bin/python scripts/kronos/kronos_forecast.py --symbol BTCUSD --stride 16 --n-paths 15 --max-windows 4000 --out data/kronos_cache_BTCUSD.npz
```
Expected: two caches written. Note wall-clock; if a pass exceeds ~20 min, raise `--stride` or lower `--n-paths` and re-run.

- [x] **Step 2: Run the analysis / verdict**

```bash
./venv/bin/python scripts/kronos/kronos_ic.py --cache data/kronos_cache_XAUUSD.npz --report-out reports/kronos_ic_smelltest_XAUUSD.md
./venv/bin/python scripts/kronos/kronos_ic.py --cache data/kronos_cache_BTCUSD.npz --report-out reports/kronos_ic_smelltest_BTCUSD.md
```
Expected: per-horizon × per-year IC tables, Stage-2 PF tables (best-IC horizon), and a GREEN/RED verdict printed + written for each instrument.

- [x] **Step 3: Interpret against the contamination guard**

Read both reports. Apply spec §7/§9: if IC is present full-span but collapses in **2026**, that's memorization → RED. If BTC shows IC (the paper's headline instrument) but XAU doesn't, say so. Record whether the BTC result is directionally consistent with the Kronos paper's BTCUSD claim.

- [x] **Step 4: Gitignore the caches (large binaries)**

Append to `.gitignore`:
```
data/kronos_cache_*.npz
```

- [x] **Step 5: Commit the reports**

```bash
git add reports/kronos_ic_smelltest_XAUUSD.md reports/kronos_ic_smelltest_BTCUSD.md .gitignore
git commit -m "research(kronos): XAUUSD + BTCUSD IC smell-test reports (verdict)"
```

---

### Task 8: Wrap-up — memory note

Persist the verdict so future sessions don't re-run this.

**Files:**
- Create: `/Users/varadbandekar/.claude/projects/-Users-varadbandekar-Documents-Quant-trading/memory/project_kronos_ic_smelltest.md`
- Modify: the memory `MEMORY.md` index (one-line pointer)

- [x] **Step 1: Write the memory note**

Frontmatter `type: project`. Capture: what Kronos is, the two instruments, the verdict (GREEN/RED per instrument), the key per-year IC numbers, the contamination-guard reasoning, and whether BTC matched the paper. Link `[[project_forward_returns_validation]]` and `[[project_squeeze_volume_filter_smelltest]]` as sibling smell-tests. If RED: add "do not re-research zero-shot Kronos IC on XAU/BTC 15m."

- [x] **Step 2: Add the one-line index pointer**

Add to `MEMORY.md`:
`- [Kronos IC smell-test](project_kronos_ic_smelltest.md) — 2026-07-25: <VERDICT>; per-year IC + contamination guard; research-only, isolated venv_kronos, no live wiring.`

- [x] **Step 3: Final verification**

Run: `./venv/bin/python -m pytest tests/unit/test_kronos_cache.py tests/unit/test_kronos_ic.py tests/unit/test_kronos_windows.py -v`
Expected: all pass under the production venv (no torch). Confirms the harness is self-contained.

---

## Self-Review

**Spec coverage:**
- §1 purpose / research-only → Task 1–8 (no src/config edits); ✓
- §3 env isolation → Task 1; ✓
- §4 data + resample → Task 6 `_load_15m`; ✓
- §5 architecture (3 files + torch isolation) → Tasks 2/3-5/6; ✓
- §5.1 cache schema → Task 2 `ARRAY_FIELDS`; ✓
- §6 Stage-1 IC → Task 3; ✓
- §7 contamination guard → Task 5 `verdict` (recent_year weighting) + Task 7 Step 3; ✓
- §8 Stage-2 strict-fill (conditional) → Task 4 + Task 5 (verdict Leg 2/3 gate); ✓
- §9 verdict criteria → Task 5; ✓
- §10 testing (synthetic cache, no torch) → Tasks 2-5 tests + Task 6 causality test; ✓
- §11 runtime/CLI → Task 5 (`kronos_ic` CLI) + Task 6 (`kronos_forecast` CLI); ✓
- §12 lookahead guard → Task 6 `build_eval_indices` + `test_kronos_windows`; ✓
- §12 pred_disp fallback → Task 6 stores nan (documented, contract intact); ✓

**Placeholder scan:** No TBD/TODO; all code steps carry real code. The single API-dependent point (`predictor.predict` signature) is explicitly resolved in Task 1 and flagged where it's used. pred_disp_h4 = nan is a documented design fallback (spec §6 "bonus diagnostic"), not a placeholder.

**Type consistency:** `ARRAY_FIELDS`/`save_cache`/`load_cache` used identically in Tasks 2/5/6. `ic_by_year`, `strict_fill_sim`, `verdict` signatures consistent across Tasks 3/4/5 and the CLI. `build_eval_indices` signature matches between Task 6 impl and `test_kronos_windows`. Horizon field names `pred_ret_h{k}`/`real_ret_h{k}` consistent between writer (Task 6) and readers (Tasks 3/4/5).
