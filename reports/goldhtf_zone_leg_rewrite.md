# GoldHTF_AutoOpt_EA — legacy zone-leg code review, triage and rewrite

2026-08-09/10. A seven-item code review of `mt5_bridge/GoldHTF_AutoOpt_EA.mq5` was
taken through the repo's usual discipline: verify each claim against measurement
before changing anything, then A/B every change that survived.

Measurement tool is `scripts/simulate_goldhtf_ea.py`, the line-faithful Python port
of `OnTick`. XAUUSD 5m, 2022-03-01 .. 2026-07-31, $1,000, 1% risk, min lot 0.02,
strict $0.20/side cost.

**Every legacy-path A/B is run with `--no-dfvg`, and every double-FVG A/B with
`--dfvg-only`.** With both paths live they compete for the single position slot, so a
change to one moves the trade count of the *other*. An early coupled run of the
tap-band change appeared to lift total PF 1.01 → 1.12; isolated, the same change is
worse. Do not A/B this EA with both paths enabled.

Drawdown below is **peak-relative**. The report's own "max drawdown" divides by the
starting balance, which on a run that compounds 3–4× produces numbers past −100% and
cannot be compared across variants.

---

## Verdict per review item

| # | Claim | Verdict | Action |
|---|---|---|---|
| 1 | `DetectFVG` scans only bars 1–2 | **Confirmed** | Fixed — full `InpFVGLookback` scan |
| 2 | `*10` makes the gap filter too big; legacy path is dead | **Refuted** | Comment corrected, value unchanged |
| 3 | Legacy FVGs never checked for being filled | **Confirmed** | Fixed — `ZoneFilled` |
| 4 | Mitigation tested on one bar only | **Confirmed, and worse than stated** | Fixed — `ZoneTouched` |
| 5 | Entry not anchored to the zone | **Partly confirmed** | Fixed structurally, not with the distance gate |
| 6 | Tap band `held` is weaker than `tapped` | **Refuted** (intended band) | Documented; measured worse if "fixed" |
| 7 | M5 ATR sizes an H1/H4 stop | **Confirmed** | Fixed for the legacy path only |

### 2 — the `*10` multiplier does not disable the legacy path

For 2-digit gold `SYMBOL_POINT` is 0.01, so `dynFVGMinPips * 10 * point` is
`dynFVGMinPips * 0.1` **USD** — a $0.20–$0.65 minimum gap on H1. That is a loose
floor, not a tight one. The full-span funnel confirms it: `legacy_fvg_found` fires on
19,877 of ~123,000 evaluated bars. Removing the `*10`, as the review suggests, would
make an already permissive filter ~10× more permissive. Left at the measured value.

### 4 — the old mitigation gate was vacuous for the freshest gap

Stronger than "checks only one bar". For a bullish gap detected at MQL index 1 the
detector sets `zoneHigh = l[1]`, and the gate then asks `l[1] <= zoneHigh` — true by
construction. `c[1] > zoneLow` follows from the gap definition. So **every** gap that
formed on the last closed bar passed the gate with price never having returned to it.
The gate only did work when the gap was found at index 2.

That is also the real answer to item 5: the entry was not anchored to the zone
because the check that was supposed to anchor it could not fail.

### 6 — the tap band is a band, not a rejection test

`InpDFVG_TapMinPct`/`TapMaxPct` specify "wick at least 50% deep, close no deeper than
60%". `lvMax` sitting below `lvMin` is intentional; `held` rejects candles that closed
straight *through* the gap. This matches `scripts/research_double_fvg.py`, which
sweeps `tap_max = tap_min + 10`. Implementing the review's reading (close back above
the 50% line, i.e. `TapMaxPct = TapMinPct`) is worse:

| DFVG path, isolated | trades | WR | PF | net |
|---|---|---|---|---|
| as shipped (50/60 band) | 119 | 60.5% | **1.06** | +8.2% |
| review's reading | 111 | 59.5% | 1.04 | +5.6% |

---

## Legacy path, isolated (`--no-dfvg`)

| variant | trades | WR | PF | net | peak DD | R | losing yrs |
|---|---|---|---|---|---|---|---|
| v1 zone leg (baseline) | 852 | 57.2% | 1.13 | +171% | −82% | +0.01 | 2 |
| **v2 zone leg** (items 1+3+4) | 630 | 59.7% | 1.18 | +274% | **−40%** | +0.06 | 1 |
| v2, lookback 60 | 507 | 61.5% | 1.15 | +222% | −46% | +0.07 | 2 |
| v1 + zone-TF ATR (item 7 alone) | 630 | 58.1% | 1.15 | +228% | −102% | +0.03 | 3 |
| **v2 + zone-TF ATR** (shipped) | 475 | 60.2% | **1.28** | +397% | −44% | +0.10 | 1 |
| v2 + `MaxZoneDistATR 0.5` | 150 | 59.3% | 1.24 | +42% | −22% | +0.06 | 2 |

Two things worth keeping:

* **Item 7 is not additive on its own.** Applied to the old zone leg the H1 ATR buffer
  gives three losing years and a −102% drawdown; applied on top of the rewritten leg
  it is the single biggest gain. A wider buffer only pays once the level it pads is
  one price has actually respected. It was also measured on the double-FVG path
  (H4 ATR) and is worse there — PF 1.06 → 1.04, DD −34% → −89% — so it is scoped to
  the legacy path via the `legacyZone` argument to `CalculateSLTP`.
* **A longer FVG memory is not free.** Lookback 60 is worse than 20 on PF and on the
  year map. Older gaps are weaker even after the fill check.

### `InpMaxZoneDistATR` — the earlier "harmful" verdict was setting-specific

The in-file note said the gate was tested and harmful. That was measured at 2.0, where
it barely binds. At 0.5 it binds hard and improves PF (1.18 → 1.24) and halves
drawdown (−40% → −22%) — but discards three quarters of the trades and most of the
net, and adds a losing year. Left OFF; the zone-touch requirement is now the anchor.

---

## Shipping configuration (both paths live, as the EA runs)

| | trades | WR | PF | net | peak DD | R | losing yrs |
|---|---|---|---|---|---|---|---|
| before | 950 | 56.8% | 1.11 | +148% | −90% | 0.00 | 2 |
| after | 556 | 60.8% | **1.30** | +439% | **−42%** | +0.11 | 1 |

Per-year PF after: 2022 1.04 / 2023 1.47 / 2024 0.92 / 2025 1.70 / 2026 1.28.

## ⚠️ The fixes do not create an edge

Matched random-entry control — same trade count, same side mix, stop distances and
RRs resampled from the real trades, entry bars drawn uniformly from the same window,
executed by the same engine, 200 trials:

| config | real PF | null median | null p95 | real beats |
|---|---|---|---|---|
| before | 1.11 | 1.03 | 1.26 | **68%** of draws |
| after | 1.30 | 1.14 | 1.63 | **73%** of draws |

Against a 95% bar, 68% → 73% is not a change in kind. The null's median PF also rises
1.03 → 1.14, because the wider H1-ATR stops mechanically raise PF at a fixed R:R — the
control absorbs exactly the part of the improvement that is geometry rather than
timing. **The entry timing remains statistically indistinguishable from random**, which
is the same verdict `reports/double_fvg_research.md` reached for the H4 path.

What the rewrite genuinely buys: the EA no longer arms levels price has already broken,
no longer passes a gate that cannot fail, and no longer places a swing stop behind a
level that was never visited. Those were real defects and the risk profile improves
accordingly (drawdown roughly halves on both paths). That is a correctness and
risk-profile result, not an alpha result.

## Not done

* No walk-forward split and no out-of-sample slice — every number here is in-sample
  over the full span. The per-year column is the only stability evidence.
* 2024 is still a losing year (PF 0.92), so this does not pass the "positive or flat
  every calendar year" leg of `backtest.md`.
* Harness fidelity limits still apply: it evaluates once per M5 close where the EA
  evaluates every tick, so live takes **more** legacy trades at worse average prices
  than measured here.
