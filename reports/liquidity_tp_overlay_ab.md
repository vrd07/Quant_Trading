# Liquidity TP overlay — A/B results

**Verdict: DOES NOT SHIP for any strategy. `enabled: false` in all 8 configs.**

The overlay works exactly as designed — it raises win rate and converts stop-outs into
take-profits — and still loses money on all three strategies in both years. The targets
it moves shrink by a median 9–14%, and the extra wins do not pay for the reward given up.

Task B4 of `docs/superpowers/plans/2026-08-07-liquidity-entry-and-exit.md`.
Spec: `docs/superpowers/specs/2026-08-07-liquidity-tp-overlay-design.md`.

## Method

XAUUSD 15m, full span, `--slippage strict`, risk-bypassed. Both arms run on
`config/config_live_25000.yaml`, toggling **only** `risk.liquidity_tp_overlay.enabled`.

> Deviation from the plan's literal commands, and it matters: the plan's baseline
> omitted `--config`, which resolves to `config_live_50000.yaml` via `ACTIVE_CONFIG`.
> That would have compared a $50k baseline against a $25k overlay arm — different
> sizing, different PF, the toggle confounded with account size. Both arms use the
> $25k config here.

Trades are matched on `(timestamp, side)`, not row position, so a diverging trade count
could not silently misalign the comparison. All three strategies produced identical
entry sets in both arms (843 / 2209 / 418), so this is a clean paired comparison: the
same trades, differing only in where the target sat.

## Step 2 — the overlay actually fired

| strategy | trades | targets moved | share |
|---|---:|---:|---:|
| squeeze_breakout | 843 | 81 | 9.6% |
| stoch_pullback | 2209 | 966 | 43.7% |
| bos_structure | 418 | 176 | 42.1% |

⚠️ **The first run of this A/B reproduced the baseline byte-for-byte on all three.**
That was two plumbing defects, not a result — see "Why the first run was a false null"
below. Nothing in this report was read until targets actually moved.

## Step 3 — year split

Ship criterion: PF improves in **both** 2025 and 2026.

| strategy | year | OFF n | OFF pnl | OFF PF | ON pnl | ON PF | |
|---|---|---:|---:|---:|---:|---:|---|
| squeeze_breakout | 2025 | 202 | 4518.53 | **1.408** | 4487.06 | 1.405 | worse |
| squeeze_breakout | 2026 | 134 | 3354.34 | **1.425** | 3081.42 | 1.390 | worse |
| stoch_pullback | 2025 | 473 | −128.06 | **0.991** | −373.96 | 0.975 | worse |
| stoch_pullback | 2026 | 278 | −3013.30 | **0.840** | −3664.86 | 0.805 | worse |
| bos_structure | 2025 | 87 | 680.90 | **1.243** | 621.82 | 1.224 | worse |
| bos_structure | 2026 | 53 | 799.35 | **1.239** | 713.33 | 1.223 | worse |

**6 of 6 year-cells worse. No strategy improves in any year.** Full span agrees:
squeeze 1.167 → 1.161, stoch 0.860 → 0.848, bos 1.040 → 1.025.

## Why it loses — the mechanism

Restricting to the trades whose target actually moved (same entries, both arms):

| strategy | moved | OFF pnl | ON pnl | delta | OFF win rate | ON win rate | median target shrink |
|---|---:|---:|---:|---:|---:|---:|---:|
| squeeze_breakout | 81 | 385.97 | 81.58 | **−304.39** | 0.370 | **0.383** | 14.0% |
| stoch_pullback | 966 | −305.99 | −976.25 | **−670.26** | 0.307 | **0.342** | 9.4% |
| bos_structure | 176 | 692.42 | 521.58 | **−170.84** | 0.347 | **0.364** | 12.5% |

Exit reasons on the moved trades shift the way the hypothesis predicted:

| strategy | stop_loss | take_profit |
|---|---|---|
| squeeze_breakout | 51 → 50 | 30 → 31 |
| stoch_pullback | 674 → 644 | 292 → **322** |
| bos_structure | 115 → 112 | 61 → **64** |

**The hypothesis is not refuted — the trade it implies is.** Pulling the target short of
an un-swept pool does make it easier to reach: win rate rises for all three, and 30 stop-outs
become take-profits on stoch_pullback alone. But the target shrinks ~9–14% on every trade
it touches, and that reward is surrendered on the winners the strategy would have banked
anyway. The extra wins do not cover it. This is the fixed-RR trade-off in its plainest
form, and it is consistent with what is already known about these strategies: for
squeeze_breakout the CLAUDE.md note is explicit that "the FIXED RR2.0 geometry is the
entire edge". Shortening the target attacks the edge itself.

A shallower buffer would not rescue it: the loss scales with how much reward is
surrendered, and the win-rate gain is already priced in at this buffer. Chasing a
`buffer_atr` that happens to break even on this span would be fitting the knob to the
answer, which the plan forbids.

## Why the first run was a false null

Recorded because both defects produce a clean, believable "no effect", and both were
invisible in the unit suite.

1. **The overlay was live-only.** It was hooked at the end of `RiskProcessor.calculate_stops`
   specifically so backtests would see it. But both backtest engines call `calculate_stops`
   only when a signal arrives *without* a stop — `if signal.entry_price and not signal.stop_loss`
   — and all three targeted strategies emit their own structural stop. `execution_engine`
   calls it unconditionally. So the overlay would have applied **in live trading while being
   invisible to every backtest**: the exact asymmetry the hook placement was chosen to prevent.
   Fixed with `RiskProcessor.apply_tp_overlay()`, called unconditionally in both engines and
   idempotent so the live path cannot double-snap.
2. **Two strategies never published their ATR.** `stoch_pullback` and `bos_structure` did not
   set `metadata['atr']`, so the overlay read `atr=0` and declined every signal via the
   `no_bars` path. `squeeze_breakout` already published it — which is why it was the only one
   that moved after fix 1, at 9.6%. After fix 2 the other two move at ~43%.

Both fixes are committed and stand on their own regardless of this verdict: without them
the overlay was a live-only, unvalidated behaviour sitting behind a config toggle.

## Disposition

- `risk.liquidity_tp_overlay.enabled: false` in all 8 configs. The code stays — it is
  tested, inert by default, and the A/B is reproducible.
- Do not re-run this with a different `buffer_atr` or `band_pct` on this data. The result
  is not marginal (6/6 cells, mechanism understood); a knob that flips it would be fitted.
- The pools-attract-price premise is *supported* here (win rate and TP-exit share both
  rise). What fails is paying for it with reward on fixed-RR strategies. If this is
  revisited, the honest version is a different question: whether a pool-aware *entry* or
  position-size decision can use that attraction without surrendering target distance.
  Note that the Phase A entry gate on kalman also failed — see
  `reports/kalman_liquidity_gate.md`.
