# Kronos IC Smell-Test — Design Spec

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Type:** Research-only. ZERO live wiring. No changes to `src/`, configs, or the risk engine.

## 1. Purpose

Evaluate whether [Kronos](https://github.com/shiyu-coder/Kronos) — an open-source, pretrained
foundation model that forecasts future OHLCV candles — carries any tradeable *information* for
**XAUUSD** and **BTCUSD** on the 15m timeframe, before deciding whether it's worth any deeper
integration.

This is a **smell-test in the mould of the squeeze volume-filter (RED) and forward-returns
studies**: measure signal cheaply, apply the project's every-year / strict-fill discipline, emit a
GREEN/RED verdict. It is explicitly NOT a strategy, not wired to `StrategyManager`, and touches
nothing in the live path.

**Success = a defensible GREEN/RED verdict**, not a positive result. RED is a perfectly good
outcome (saves a live footgun), consistent with the GSS ~zero-IC and squeeze-RED precedents.

## 2. Goals / Non-Goals

**Goals**
- Zero-shot IC of Kronos-base forecasts vs realized forward returns, per instrument, **per calendar year**.
- A conditional strict-fill toy sim (only if Stage-1 IC is non-trivial).
- A written verdict report; reusable, cached, re-sliceable analysis.
- Keep the production `venv` and repo untouched by the ML stack.

**Non-Goals**
- No live wiring / no `Signal` emission / no config or `STRATEGY_WEIGHTS` changes.
- No Kronos finetuning (Approach C) — only worth it if zero-shot shows life.
- No claim of edge from a good full-span number alone (see contamination guard, §7).

## 3. Environment & Dependencies

- **Isolated `venv_kronos/`** (user decision) — production `venv` is NOT modified. `requirements.txt`
  is NOT touched. `venv_kronos/` is gitignored.
- Stack: `torch`, `huggingface_hub`, `einops`, `pandas`, `numpy`, `scipy`, `matplotlib` (+ Kronos
  repo source, which is not pip-installable — cloned into `vendor/Kronos/` or scratchpad and imported
  by path). `vendor/Kronos/` gitignored.
- Model: `NeoQuasar/Kronos-base` (102M, context 512) + `NeoQuasar/Kronos-Tokenizer-base`,
  downloaded from HuggingFace (~400 MB). Runtime device: MPS if available, else CPU.

## 4. Data

- Source: existing `data/historical/{XAUUSD,BTCUSD}_5m_real.csv` (single-source 5m, tz-aware UTC,
  columns `timestamp,open,high,low,close,volume`).
- Resample 5m → 15m via the shared `resample` helper (`from research_fx_majors import resample`,
  left/left) — matches project convention (5m is the only stored TF; higher TFs resample-on-load).
- Spans: XAU 2025-01→2026-07 (year buckets **2025, 2026**); BTC 2024-01→2026-06 (**2024, 2025, 2026**).
- Volume note: BTC Dukascopy volume is fractional/often-zero, gold is tick-count. Kronos normalizes
  per-window, so pass OHLCV as-is and synthesize `amount = close × volume` for the model's amount channel.

## 5. Architecture

New directory `scripts/kronos/`. torch is confined to a single file; the analysis is pure numpy/pandas/scipy.

| File | Responsibility | torch |
|---|---|---|
| `scripts/kronos/kronos_forecast.py` | Load Kronos-base + tokenizer; resample; **batched** rolling forecasts; write prediction cache `.npz` | ✅ (only file) |
| `scripts/kronos/kronos_ic.py` | Read cache → Stage-1 IC + Stage-2 strict-fill → GREEN/RED verdict + report | ❌ |
| `tests/unit/test_kronos_ic.py` | Synthetic cache with injected known IC → assert Spearman recovers it + strict-fill PnL signs | ❌ |

**Interface between the two halves is the cache file** — the only contract. This lets analysis be
iterated (thresholds, cost levels, horizons) with no model re-run, and unit-tested with no 1 GB stack.

### 5.1 Prediction cache schema (`.npz`, one per instrument)

Per evaluated window `t` (rows aligned across arrays):
- `timestamp` — window's decision time (last observed bar close).
- `year` — calendar year of `timestamp`.
- `pred_ret_h1..h4` — mean predicted cumulative return at horizon 1..4 bars, averaged over N sampled paths.
- `pred_disp_h4` — dispersion (std across sampled paths) at H=4 = model's predicted uncertainty/vol.
- `real_ret_h1..h4` — realized cumulative return at horizon 1..4 (causal; from bars strictly after `t`).
- `last_close` — for cost/spread scaling in Stage 2.
- Metadata: model id, stride, n_paths, horizon, sampling params (T/top_p/top_k), git commit, created-at.

## 6. Stage 1 — Information Coefficient

For each instrument × horizon (1..4) × {each year, full span}:
- **Spearman rank IC** between `pred_ret_hK` and `real_ret_hK` (robust; the standard IC).
- **Sign hit-rate** of `pred_ret_hK`.
- **Quintile monotonicity**: bucket by predicted return, check realized return increases across buckets.
- Bonus diagnostic: does `pred_disp_h4` correlate with realized |return| (predicted-vol usefulness)?

Report as a table (instrument × horizon × year). This is the "does the forecast carry signal at all" leg.

## 7. Contamination Guard (critical)

Kronos's training cutoff is undisclosed and it was trained on 45 exchanges — recent XAU/BTC bars may
be **in-sample for the model**. Therefore:
- IC is reported **per calendar year**, and the verdict **weights the most-recent year (2026) heaviest**,
  as the slice most likely to be genuinely post-cutoff / OOS.
- A strong full-span IC that **collapses in 2026** is treated as memorization → RED.
- This is the project's every-year-positive discipline applied to a pretrained model.

## 8. Stage 2 — Strict-Fill Toy Sim (conditional)

Runs **only if** Stage-1 IC is non-trivial (right-signed and material; see §9). Otherwise skipped and
reported as "not reached".
- Signal: enter when `|pred_ret_hK|` exceeds a threshold (swept), direction = sign(pred).
- Fills: strict — apply spread + adverse slippage per project strict-fill convention; hold H bars
  (or to a toy SL/TP), exit at realized price.
- Report PF / return / max-DD / win-rate **per year**, at a couple of cost levels (cost-robustness),
  matching the backtest.md smell-test style.

## 9. Verdict Criteria

**GREEN** requires ALL of:
1. Stage-1 Spearman IC right-signed and material (guide: |IC| ≳ 0.03–0.05) at ≥1 horizon, **and**
2. that IC still present in **2026** (not just earlier, possibly-in-sample years), **and**
3. Stage-2 after-cost PF ≳ 1.1 in 2026 and not negative in other years.

**RED** otherwise (IC ~0, IC only in early years, or Stage-2 fails costs). Report states which leg failed.

Verdict is mechanical and stated up front in the report, like the squeeze GREEN/RED.

## 10. Testing

- `tests/unit/test_kronos_ic.py` (no torch): build a synthetic cache where `pred_ret = k·real_ret + noise`
  for known `k`; assert Spearman IC recovers the injected sign/magnitude within tolerance; assert a
  known-profitable synthetic signal yields PF>1 and a known-random one yields PF≈1 in the strict-fill sim.
- `kronos_forecast.py` is validated by a **tiny smoke run** (a few windows) confirming shapes,
  causality (no lookahead — realized returns come strictly after the window), and that the cache loads.
- Reuse the project's pytest setup; unit tests must pass under the production `venv` (no torch import
  in the tested module).

## 11. Runtime / CLI

- Batched MPS inference; `--stride`, `--n-paths`, `--horizon`, `--symbol`, `--max-windows`, `--device`
  as flags. Start coarse (~3–5k strided windows/instrument, ~15–20 paths) → minutes, refine if signal shows.
- `kronos_ic.py` flags: `--cache`, `--cost-bps`, `--threshold-grid`, `--report-out`.
- Output report: `reports/kronos_ic_smelltest.md`.

## 12. Risks & Mitigations

- **Lookahead bug** (the Signal.timestamp=now() class of trap from the squeeze test): realized returns
  MUST be computed strictly from bars after the window's last observed bar. Enforced in the smoke test.
- **Runtime blow-up**: batched inference + strided sampling + cache; horizon capped at 4.
- **Python 3.13 / torch wheel availability on M1**: verified at install; fall back to CPU if MPS flaky.
- **Kronos amount channel**: synthesized; documented as an approximation.
- **False-GREEN from drift**: the 2026-weighted per-year verdict is the guard.

## 13. Out of Scope / Follow-ups (only if GREEN)

- Finetuning Kronos on XAU/BTC.
- Wiring a Kronos-derived feature into `ConfluenceGate` as ONE input (never a solo signal).
- Both would be separate spec → plan → implement cycles.
