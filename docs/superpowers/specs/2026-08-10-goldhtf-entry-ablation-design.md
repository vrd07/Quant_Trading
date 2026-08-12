# GoldHTF EA — entry-chain ablation design

Date: 2026-08-10
Status: approved, not yet implemented
Target: `mt5_bridge/GoldHTF_AutoOpt_EA.mq5`, legacy multi-TF entry chain

## Problem

After the 2026-08-09/10 zone-leg rewrite (`reports/goldhtf_zone_leg_rewrite.md`) the
EA measures PF 1.30 / +439% / peak DD −42% over 2022-03..2026-07. But against a
matched random-entry control it beats only **73% of 200 draws** on profit factor,
against a 95% bar — barely moved from the 68% it scored before the rewrite. The
rewrite fixed real defects and roughly halved drawdown; it did not make the entry
distinguishable from random.

The legacy chain carries 498 of 556 trades and essentially all the P&L, and it has
four sequential legs. **We do not know which of them, if any, carries information.**
This spec measures that.

## Decision this work supports

Which legs of the legacy entry chain earn their place, and which are decoration to be
deleted. Pre-committed: if nothing clears the bar, the finding is reported and the
search stops. No wider grid, no variant hunt — that is the `project_rsi_reversal_m1`
failure mode.

## Method

Primary: leave-one-out ablation, each cell scored against **its own** matched
random-entry control. Cross-check: a signal-level forward-return test, run only on
legs the primary calls decoration.

The two fail differently, which is the point of running both. The primary can be
fooled by exit and geometry effects; the cross-check cannot see costs or stops.

### Harness and isolation

`scripts/simulate_goldhtf_ea.py`, the line-faithful Python port of `OnTick`.
XAUUSD 5m, 2022-03-01 .. 2026-07-31, $1,000, 1% risk, min lot 0.02, strict
$0.20/side, current shipped config (zone-v2 + H1-ATR stop buffer).

**Every cell runs `--no-dfvg`.** The two entry paths compete for the single position
slot, so with both live a change to one moves the other's trade count. This already
produced one false reading: a coupled run made the tap-band change look like PF
1.01 → 1.12 when isolated it is worse.

### Cells

Direction in this chain comes from `GetHTFTrend()`, and `CheckEntryConfirmation`
requires the H1 zone to agree with it before any pattern is tested. So:

| cell | removed | mechanism |
|---|---|---|
| A0 | — | baseline |
| A1 | H4 trend | direction taken from the zone; no trend test |
| A2 | H1 zone | direction from trend; stop replaced by an ATR stop (multiple computed once from A0 so A2's median stop width matches A0's, then frozen) |
| A3 | M15 structure | `CheckMTFStructure` skipped |
| A4 | M5 candlestick pattern | pattern skipped, directional close-confirm kept |
| A5 | M5 pattern *and* confirm | enter on the first bar every other leg is true |
| A6 | *(adds)* RANGING gate | block entries when regime == RANGING |

A2 cannot be a clean deletion: the zone supplies the stop level, so removing it
changes geometry as well as entry timing. Matching the substitute ATR stop to the
baseline's median structural width is what keeps it a test of entry information.

A6 is an addition rather than a removal. It is included because `RANGING` entries
measure flat in the shipped config — 158 of 498 legacy trades at PF 1.01 / +0.018R,
against STRONG 1.55 and WEAK 1.26 — and regime is known at entry, so it is
legitimately actionable. (The comparable-looking hold-time split is **not**: a trade
that held >24h did so *because* it did not stop out. That is the
`project_conditioned_stat_trap` error and it is deliberately excluded here.)

### Metric

Per cell: 500 control trials. Same fixed RNG seed across cells, recorded in the
report, for reproducibility — **not** for pairing. Cells differ in trade count, stop
distances, and RRs, so the random draw streams diverge starting from the first draw
even with the seed held fixed. Seed 11 buys a reproducible number, not a paired
comparison.

Two numbers:

- `percentile` — share of null draws whose PF the real PF beats
- `z = (real_PF − mean(null_PF)) / sd(null_PF)` — **primary**

z is primary because it uses the whole null distribution rather than a rank, so it has
more power at the same trial count. At 200 trials the standard error on a percentile
near 70% is ~3pp, which is why the previously reported 68% → 73% move is inside noise
and why this spec uses 500.

### Pre-committed thresholds

Fixed before any run. Every "drops" and "raises" below is measured **against A0**, not
against any other cell:

- **Load-bearing** — removing the leg drops z by **≥ 0.5** *and* drops percentile by
  **≥ 10pp**. Both conditions, so a leg cannot be saved by metric-shopping.
- **Decoration** — removing it moves z by **< 0.5** in either direction.
- **Harmful** — removing it *raises* z by ≥ 0.5 and percentile by ≥ 10pp.
- **A6 inverts**: the regime gate earns its place only if adding it raises z by ≥ 0.5.

### Pre-committed guards

1. **If A0's own z < 1.0, leg-level results are not read at all.** If the full chain is
   not distinguishable from its own null, differences between its legs are noise being
   ranked. That case is reported as "decoration end to end" and nothing is cut on
   leg-level evidence.
2. **Any cell whose trade count differs from baseline by more than 3× is flagged
   qualitative.** Removing the pattern leg may go from ~500 trades to several
   thousand; past that it is a different strategy, not an ablation of this one.

### Cross-check — forward returns, no trading

Runs only on legs the primary calls decoration. For leg L: take every bar where all
*other* legs are true and the chain sits at its entry decision, split on L true/false,
compare forward returns signed by the intended trade direction.

Three requirements, each of which has produced a fake result in this repo before:

- **ATR-normalized returns, not points.** Gold ran ~1800 → ~4100 across this span, so
  raw points weight 2026 several times 2022.
- **Drift control.** Every cell is read net of the unconditional forward return over
  the same window, per side. A long-biased subset shows positive returns from gold's
  trend alone — the trap in `project_forward_returns_validation`, where it made 3 of 4
  cells fake.
- **Day-blocked bootstrap.** Forward returns on consecutive M5 bars overlap almost
  completely, so a naive t-stat is badly inflated.

Horizons: 2h and 24h.

Reading: the cross-check **revives** a leg only if the true-vs-false difference
excludes zero at 95% after drift adjustment. It never overrides the primary. A leg
that is decoration in the primary but alive here yields the finding "the information
exists and the exits are destroying it" — a separate result, and a different repair.

## Deliverables

- `scripts/simulate_goldhtf_ea.py` — six ablation flags
  (`--no-trend-leg`, `--no-zone-leg`, `--no-mtf-leg`, `--no-pattern-leg`,
  `--no-confirm`, `--skip-ranging`), plus z and percentile in the control block.
  Additive: with no flags, behaviour is unchanged.
- `scripts/research_goldhtf_entry_ablation.py` — drives the seven cells, runs the
  cross-check, writes the report.
- `reports/goldhtf_entry_ablation.md` — cell table, verdicts against the thresholds
  above, cross-check results.

## Out of scope

- **The EA is not modified by this work.** Cutting legs from the `.mq5` is a follow-up
  gated on the result, and if guard 1 trips there is no cut to make.
- The H4 double-FVG path. Already ablated in `reports/double_fvg_research.md`, where
  neither the "double" nor the "first gap" condition was load-bearing.
- Exit and ladder behaviour. Held fixed at shipped values across every cell.
- The three entry-code defects named during review but not measured here — the
  repainting H4 MA/RSI read on the forming bar, the `(structure) || (momentum)`
  disjunction resolving long on conflict, and `ZoneTouched` proving the zone was
  tested at some point rather than that price is at it now. These are candidate
  follow-ups; if A1 says the trend leg is decoration, the first two stop mattering.

## Known limitations

- **In-sample over the full span, no walk-forward.** Defensible for "does this leg
  contribute" but not for "will this make money". If a leg survives, cutting the others
  is a change that needs its own out-of-sample check before it ships.
- Seven cells is seven tests on one dataset. The ≥ 0.5 z threshold is set with that in
  mind; guard 1 is the main protection against reading noise as structure.
- Harness fidelity: it evaluates once per M5 close where the EA evaluates every tick,
  so live takes more legacy trades at worse average prices than measured.
- Runtime: the control replay loops the full bar range per trial, ~10 minutes per cell
  at 500 trials, ~70 minutes total, more if A5 produces thousands of trades.
