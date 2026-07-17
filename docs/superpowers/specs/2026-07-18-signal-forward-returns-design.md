# Order-Flow Signal Forward-Return Analyzer — Design

**Date:** 2026-07-18
**Status:** Approved by user (all sections)
**Builds on:** Stage 1 (`features.py` detectors, `load_ticks`/`ensure_ticks`) and Stage 1.5 (live `{day}_signals.jsonl` feed). Research-only; no live-trading wiring.

## Problem

The order-flow marking tool (Stages 1 / 1.5) produces marks but has **zero
evidence** any mark predicts price. The live signal log is one day old (~66
events) — far too few to conclude anything. This tool measures, on a
statistically meaningful sample, whether each mark type would have made money
as a trade — the front half of the pre-agreed Stage-2 gate. Its success is a
**trustworthy verdict**, not a found edge; a clean sweep of "dead" is a
valuable, money-saving result.

## Decisions (user)

1. **Sample source = reconstruct from tick history.** Replay the SAME five
   `features.py` detectors over the Dukascopy tick history (`data/ticks/`,
   the immutable store) across a date range → hundreds-to-thousands of
   events. The live `{day}_signals.jsonl` files are analyzed as a SEPARATE
   cohort (ongoing out-of-sample), reported side-by-side, clearly marked
   "live OOS, thin".
2. **Label = triple-barrier, cost-aware.** Per signal: ATR-scaled stop /
   target + time limit, walked over real ticks, net of spread+slippage,
   result in R units. Mirrors live SL/TP and the strict-fill gate.
3. **Slicing = mark × direction, IS/OOS + significance.** Test the 8
   directional mark cells only; report n, expectancy, PF, win-rate, 70/30
   time-split IS/OOS, t-stat, and a plain multiple-testing note. Context
   slicing (regime/session/hour) only on whatever survives — deferred.

## Architecture

Pure core + thin orchestration, following the repo's `research_*.py` pattern.

### 1. `src/microstructure/forward_returns.py` (pure, no I/O, no ML)

- `event_direction(kind: str) -> str | None` — maps a `FlowEvent.kind` to
  `"long"` / `"short"` / `None`. Long: `bullish_divergence`, `sweep_low`,
  `absorption_of_selling`, `imbalance_buy`. Short: their mirrors
  (`bearish_divergence`, `sweep_high`, `absorption_of_buying`,
  `imbalance_sell`). `liquidity_withdrawal` → `None` (directionless;
  context-only, excluded from directional labeling).
- `label_event(ticks, entry_ts, entry_price, direction, atr, cfg) -> dict`
  — triple-barrier over the tick path (see Section 2). Returns
  `{kind?, direction, ts, R_net, outcome, bars_held, mae, mfe}`.
  `outcome ∈ {"target","stop","time"}`. `cfg` carries
  `sl_atr, tp_atr, max_hold, cost_pts`.
- `summarize(labeled_events, split_frac=0.7) -> dict` — per-cell stats +
  IS/OOS split + verdict (see Section 3).

### 2. `scripts/analyze_signal_forward_returns.py` (orchestration + report)

- CLI: `--symbol XAUUSD --start … --end … --timeframe 15m`, barrier flags
  `--sl-atr 1.0 --tp-atr 2.0 --max-hold 16 --cost-pts 0.4`,
  `--split-frac 0.7`, `--live-dir` (default the Stage-1.5 live dir).
- Reconstruct: `ensure_ticks`/`load_ticks` for the range → `resample_bars`
  at the TF → run all five detectors from `features.py` → flat event table.
  ATR(14) on the TF bars, looked up at each signal's bar.
- Label every event via `label_event` (skip `None`-direction kinds).
- Live cohort: load each `{day}_signals.jsonl`, re-price/re-label through the
  identical path, report separately.
- Render ranked console table + write `reports/signal_forward_returns.md`.

## Section 2 — Triple-barrier labeling (cost-aware)

Per signal, direction from `event_direction`:
- **Entry** at the signal price on its side; charge `cost_pts` immediately
  (no mid fills).
- **Barriers**: stop at `entry ∓ sl_atr×ATR`, target at `entry ± tp_atr×ATR`,
  time limit `max_hold` bars. ATR = TF ATR(14) at signal time (barriers scale
  with volatility like the live strategies). Defaults sl_atr 1.0 / tp_atr 2.0
  (RR 1:2 house default) / max_hold 16 (4h on 15m).
- **Walk subsequent ticks** (real path, not bars): first barrier touched
  wins; an intrabar stop-then-target counts as the STOP. Time-exit records
  the actual signed R at the close tick.
- **R units, net of costs** applied at entry and exit (`cost_pts`/side,
  default ~0.4 pt gold strict-fill assumption).
- Free byproducts: `mae`/`mfe` (max adverse/favorable excursion) — later
  distinguishes "stop mis-placed" from "signal dead".

## Section 3 — Aggregation, IS/OOS split, significance, report

Per cell (kind × direction, 8 directional cells; `liquidity_withdrawal`
context-only):
- `n` reported first; `n < 30` in either half → `⚠ thin`, no verdict.
- Expectancy (mean R_net), win rate, profit factor, total R, median bars
  held, mean MAE/MFE.
- **IS/OOS at 70/30 by time**; same stats each half. Candidate requires
  positive expectancy in BOTH halves. One-sided → flagged (regime luck).
- **t-stat** on R_net vs zero + a plain multiple-testing note ("8 cells
  tested; ~0.4 false positives expected at p<0.05 — treat a lone significant
  cell with suspicion"). No Bonferroni theater.
- **Verdict**: `CANDIDATE` iff n≥30 per half AND expectancy positive in both
  IS and OOS AND full-sample t-stat > 2; else `thin` / `one-sided` / `dead`.
- Output: ranked console table + `reports/signal_forward_returns.md` with the
  historical table, the live-cohort table side-by-side, and a one-paragraph
  honest bottom line. A clean sweep of `dead` is a valid, valuable result.

## Section 4 — Testing

- `tests/unit/test_forward_returns.py` (synthetic ticks, no network):
  - `label_event`: target-before-stop → `+tp_R` net of costs; gap-to-stop →
    `−1R`; neither before `max_hold` → time-exit signed R; intrabar
    stop-then-target → counted as STOP (proves tick-walk not bar-walk);
    higher `cost_pts` strictly lowers R_net.
  - `summarize`: hand-built events → correct expectancy/PF/win-rate; IS/OOS
    boundary places events correctly; verdict logic fires
    (`CANDIDATE`/`thin`/`one-sided`/`dead`) on constructed inputs.
- Script smoke: run over the real 2026-07-07..09 ticks already in
  `data/ticks/` → produces the ranked table + writes the report, no error.

## Out of scope

- Context slicing (regime/session/hour) — only on cells that survive the
  directional pass; separate follow-up.
- Any change to `features.py`, live trading, configs, the bridge, or the
  Stage-2 ML classifier itself (this is Stage 2's front half — labeling &
  edge screening — not the classifier).
- Optimizing barrier params for a result — the defaults mirror live geometry;
  flags exist for stress-testing, not for mining a hot cell.
