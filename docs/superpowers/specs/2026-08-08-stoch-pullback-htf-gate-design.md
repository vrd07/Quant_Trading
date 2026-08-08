# stoch_pullback — HTF alignment gate, or retirement

**Status:** design approved 2026-08-08. Research not yet run.

## Why this exists

`stoch_pullback` is enabled in all 8 live configs. Under the project's own deploy
fill model it has never made money.

Full-span XAUUSD 15m, `--slippage strict`, risk-bypassed, 2,209 trades
(2022-01-04 → 2026-08-06):

| year | n | pnl | PF | win% | mean_R |
|---|---:|---:|---:|---:|---:|
| 2022 | 501 | −1334.08 | 0.835 | 30.7 | −0.1161 |
| 2023 | 472 | −1647.26 | 0.788 | 28.4 | −0.2760 |
| 2024 | 485 | −2231.15 | 0.782 | 29.3 | −0.2536 |
| 2025 | 473 | −128.06 | 0.991 | 32.8 | −0.0644 |
| 2026 | 278 | −3013.30 | 0.840 | 31.7 | −0.1661 |
| **all** | **2209** | **−8353.85** | **0.860** | **30.5** | **−0.1757** |

Negative in every year. The mechanism is arithmetic: RR 2.0 breaks even at a
**33.3%** win rate and the strategy delivers **30.5%** — short by ~2.9pp, consistently.

Two findings explain why this was not visible before.

1. **The shipped number used a different fill model.** Identical run, identical 2,209
   trades, default (realistic) slippage: **PF 1.13, +$5,746 (+23.0%)**. The entire
   apparent edge sits inside the fill assumption. `reports/` history records
   "production full-span PF 1.10, 711 trades" — that was realistic fills on a shorter
   dataset.
2. **The original research only ever saw ~1.4 years.** `scripts/research_stoch_pullback.py`
   hard-codes `START, END = "2025-02-01", "2026-06-22"`, split "2025 = OOS, 2026 =
   in-sample". Both the `min_ema_dist_atr` filter and the 07–21 session window were
   fitted inside that window. 2022–2024 (1,458 trades) is data no version of this
   strategy was ever tuned on, developed against, or looked at — and all three years
   are negative.

This is not decay. It is a 1.4-year fit meeting the years outside it.

⚠️ **Consistency note.** `session_vwap_reversion` was implemented, then **rejected and
reverted** at strict PF 0.94, with the recorded lesson that flat-cost research must
clear ~1.3 to survive strict fills. `stoch_pullback` is live at strict PF **0.86**.
The same standard produced opposite outcomes; this spec exists to resolve that.

## Scope

One pre-registered hypothesis, one read-out, one holdout look. Explicitly **not** in
scope: a broad filter-family sweep, re-tuning `min_ema_dist_atr` / session hours /
`arm_window`, or changing the entry logic. Those stay in reserve and are only
justified if the HTF gate shows signal without fully clearing.

## Data and arbiter

- XAUUSD 15m, resampled from `data/historical/XAUUSD_5m_real.csv` (the only stored TF).
- 2022-01-04 → 2026-08-06, 108,281 bars.
- **`--slippage strict`, risk-bypassed.** We are measuring signal quality, not the
  risk engine. Strict is the deploy gate; realistic is what produced the false read.
- **Decision metric is `mean_R` (`pnl / r_dollars`), not dollar PnL.** Sizing is
  equity-proportional, so on a moving equity curve a dollar total partly measures
  *when* a trade happened. Win-rate moves are read against their own standard error.

⚠️ **The research simulator must be validated before it is trusted.** Parameter sweeps
run in the fast sim, but `research_stoch_pullback.py`'s sim and the production engine
have already diverged on this exact strategy (research 1.31/1.19 vs production 1.10).
**Step 1 of implementation is proving the sim reproduces the production engine's
current strict-fill baseline over the full span** (PF 0.860, 2209 trades, the year
table above) within a stated tolerance. If it cannot, the sweep measures a different
system and every downstream number is fiction. Any candidate that clears the sweep is
re-confirmed in the production engine before it counts.

## Windows

| window | years | trades | use |
|---|---|---:|---|
| **TUNE** | 2024, 2025, 2026 | 1,236 | fit freely |
| **HOLDOUT** | 2022, 2023 | 973 | pristine — opened **exactly once**, at the end |

Tune halves for the both-halves test: **H1 = 2024** (485 trades), **H2 = 2025+2026**
(751 trades).

This split is deliberate. 2024 was never seen by the original 1.4-year fit, so a
filter that improves **both** H1 and H2 has to work on both sides of the original
fitting boundary — a robustness test inside the tune window, not merely a larger
sample. The cost is that 2024 is spent as pristine data; that was an explicit choice
over a larger holdout.

**Nothing is fitted, selected, inspected, or plotted on 2022–2023 before the single
final run.** If the holdout is consulted early, it is no longer a holdout and this
whole exercise reduces to the same 1.4-year mistake on a longer span.

## Hypotheses

**H1 — primary.** `stoch_pullback` is the only gold strategy with no higher-timeframe
filter: it reads EMA(50) on its own 15m bars and nothing slower. Adding a slow-EMA
side-alignment gate improves it.

Precedent, same instrument, same timeframe:
- `squeeze_breakout`, `htf_ema_period: 400` → PF 1.21 → 1.44
- `bos_structure`, `htf_ema_period: 600` → 1.16 → 1.28

Mechanism, and why it beats the obvious alternative: the side asymmetry looks
actionable (BUY PF 0.933 vs SELL 0.788; SELL is −$6,353 of the −$8,354) but it
**flips three times** across the span —

| | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|
| BUY | 0.741 | 0.777 | **1.045** | **1.371** | 0.683 |
| SELL | **0.938** | **0.800** | 0.549 | 0.614 | **0.993** |

— so "disable SELL" is fitted to 2024–25 and loses in 2022, 2023 and 2026. The HTF
gate expresses the same intuition *conditionally* ("take the side the higher timeframe
agrees with") instead of as a fixed bet on one direction.

Sweep: `htf_ema_period` ∈ {0 (off), 200, 400, 600, 800}, side-only, computed on the
15m close (no extra feed), mirroring `squeeze_breakout_strategy.py:173-177`:

```python
if self.htf_ema_period > 0:
    htf = float(close.ewm(span=self.htf_ema_period, adjust=False).mean().iloc[-1])
    if (side == OrderSide.BUY and c <= htf) or (side == OrderSide.SELL and c >= htf):
        return None
```

**H0 — read-out, not a shipped knob and not a gate.** RR ∈ {1.5, 2.0, 2.5, 3.0} with
the HTF gate off, to establish where the unfiltered geometry sits relative to
breakeven on 4.6 years. The existing "rr 2.0 is the edge, RR3.0 marginal" finding was
measured on the same 1.4-year window and has never been checked against 2022–2024. A
lone RR sweep is trivially overfittable, so this is context for interpreting H1 and
for the retirement discussion — it is not a candidate to ship on its own, and it does
not stop the run. It is reported on the **tune window only**; RR is not re-derived on
the holdout.

## Selection rule — committed before any result is seen

1. **H0 is descriptive and does NOT gate.** It reports where the unfiltered geometry
   sits relative to breakeven across 4.6 years, which informs how to read H1 and the
   retirement discussion. It does **not** stop the run.

   (An earlier draft made H0 a hard viability gate that aborted before H1 was tested.
   That was wrong: the read-out runs with the gate *off*, and an entry filter changes
   the win rate, which changes the breakeven RR. An unfiltered RR failure therefore
   does not preclude filter-plus-RR working, and gating on it would have killed the
   primary hypothesis untested.)
2. **HTF winner** must satisfy all three:
   - improves `mean_R` in **both** H1 and H2 versus `htf_ema_period: 0`;
   - sits on a **plateau** — at least one neighbouring value also improves both halves.
     An isolated spike between two worse values is noise;
   - retains **≥40%** of baseline trade count on the tune window.
3. If no value satisfies all three, there is no candidate: **stop, report, do not open
   the holdout.**
4. **Exactly one** configuration goes to the holdout. No second look, no "try the next
   best" after a holdout failure — that converts the holdout into a tune window.

## Gate

All legs required, evaluated once:

- **positive-or-flat `mean_R` in 2022 and 2023 individually** (the holdout);
- still positive across the tune years;
- reproduced in the **production engine** (`run_backtest --timeframe 15m
  --slippage strict`), not only in the research sim.

## Consequence

**User decision, 2026-08-08: report first, decide after.** The research is run and
written up; the disable/keep/demote call is made once the numbers are visible.

⚠️ Recorded because it matters to how the result should be read: fixing the
consequence *after* seeing results is weaker than committing beforehand, and is
plausibly how a strategy that fails its own gate stayed live. The alternative offered
was pre-agreeing `enabled: false` on any failed leg. This note is not a request to
revisit the decision — it is here so a future reader knows the gate was evaluated
under a post-hoc consequence and can weight it accordingly.

## Deliverables

- `scripts/research_stoch_htf_gate.py` — sim-vs-production parity check, H0 read-out,
  H1 sweep, single holdout run.
- `reports/stoch_pullback_htf_gate.md` — parity evidence, both sweeps, the selection
  decision, the holdout result, verdict. If the verdict is negative, that is stated in
  the first line.
- `htf_ema_period` added to `stoch_pullback_strategy.py` and to the
  `strategies.stoch_pullback` block in all 8 configs (default `0` = off unless the
  gate ships), plus a unit test for the gate.
- CLAUDE.md strategy-table update and a `project_*` memory recording the verdict —
  including, either way, the strict-vs-realistic finding and the 1.4-year-fit finding,
  so neither is rediscovered from scratch.
- Follow the CLAUDE.md propagation checklist for any config/registry change.

## What would invalidate this work

- The research sim failing to reproduce the production baseline, and the sweep being
  run anyway.
- Any inspection of 2022–2023 before the final run.
- Widening the hypothesis set after seeing tune-window results without re-declaring
  the selection rule.
- Reading dollar PnL instead of `mean_R` where equity is moving.

## Most likely outcome

Failure. Three untouched years at PF 0.78–0.84 is a deep hole, and the gate must move
win rate ~3pp without cutting trade count below 40%. Under even odds. That is still
worth running: it is cheap, it is the method that measurably rescued two sibling
strategies on this instrument, and a clean negative retires the strategy on evidence
rather than on impression.
