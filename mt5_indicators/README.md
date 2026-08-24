# GoldenChart — Investing.com / TradingView chart replica for MT5

Reproduces the exact visual setup from the reference charts in `/volume` (US Tech 100,
BTC/USD, **XAUUSD**, etc.) on any MT5 symbol. Built and tuned for **XAUUSD (Gold)**.

## What's in the package

| File | Window | Replicates |
|------|--------|-----------|
| `GoldenChart_Trend.mq5` | Main chart | **Bollinger Bands (20, 2)** with lavender band fill + **Williams Alligator (21, 13, 8)** (Jaw blue / Teeth red / Lips green, shifted 8/5/3) |
| `GoldenChart_Levels.mq5` | Main chart | **Live trade markers**: reads open positions + pending orders on the symbol and draws **ENTRY (black) / TP (magenta) / SL (red)** dashed lines with price tags, auto-updating as trades open/close |
| `GoldenChart_PlanLevels.mq5` | Main chart | **Planned signal markers**: reads `mt5_chart_signals.csv` (written by the Python bot) and draws planned **ENTRY / TP / SL** as **dotted** lines — shows what the bot intends *before* the order exists |
| `GoldenChart_RSI.mq5` | Sub-window | **RSI (14)** with the pink-shaded 40–60 band + dotted 30/40/60/70 levels |
| `GoldenChart_StochRSI.mq5` | Sub-window | **Stoch RSI (14, 14, 3, 3)** — %K (blue) / %D (orange) + shaded 20–80 band. *(MT5 has no built-in Stoch RSI.)* |
| `GoldenChart_MACD.mq5` | Sub-window | **MACD (12, 26, 9)** — green/red histogram + MACD/signal lines |
| `GoldenChart_Liquidity.mq5` | Main chart | **Liquidity race**: un-swept swing/equal/session levels drawn as rays, ranked by a calibrated `P(hit first in 24h)` from `liquidity_coefficients.mqh`, plus a corner ranking panel. Detection is fixed at M15 regardless of chart TF. Requires `liquidity_coefficients.mqh` beside it. |

## Install

1. In MT5: **File → Open Data Folder** → `MQL5/Indicators/`.
2. Copy all five `GoldenChart_*.mq5` files into that folder (a `GoldenChart/` subfolder is fine).
3. In **MetaEditor** open each file and press **F7** to compile (or **Compile** button). All five must compile with `0 errors`.
4. Back in MT5 they appear under **Navigator → Indicators**.

## Attach (order matters for the sub-window stacking)

Open an **XAUUSD, H4** chart (the reference charts are the `240` = H4 timeframe), then
drag the indicators on in this order:

1. `GoldenChart_Trend`     → main window (BB + Alligator)
2. `GoldenChart_Levels`    → main window (live trade markers)
3. `GoldenChart_PlanLevels`→ main window (planned signal markers, dotted)
4. `GoldenChart_RSI`        → creates sub-window 1
5. `GoldenChart_StochRSI`  → creates sub-window 2
6. `GoldenChart_MACD`       → creates sub-window 3

Then **right-click the chart → Template → Save Template** (e.g. `GoldenChart.tpl`) so you
can one-click apply the whole layout to any chart afterwards.

## Trade markers (`GoldenChart_Levels`)

This indicator does **not** invent S/R — it marks your **actual trades**. It reads every open
position and (optionally) pending order on the chart symbol and draws their levels:

- **ENTRY** — black dashed (open price; labelled `PENDING` for pending orders)
- **TP** — magenta dashed
- **SL** — red dashed

Each line carries a right-scale price tag plus a `TP/ENTRY/SL @price` label. Lines refresh on a
timer and self-clean when a trade closes — open a trade and the lines appear on XAUUSD instantly.

Inputs:
- `InpShowPending` — also mark pending orders (default true).
- `InpShowLabels` — show the text label on each line (default true).
- `InpRefreshSec` — how often to re-scan trades, seconds (default 1).
- `InpLineWidth`, `InpStyle`, and the three colors (`InpEntryColor`/`InpTPColor`/`InpSLColor`).

> Lines for an SL or TP only draw when that level is actually set on the trade (price > 0).

## Planned signal markers (`GoldenChart_PlanLevels`)

This shows the bot's **intended** trades *before* they execute, drawn as **dotted** lines
(distinct from the solid-dashed live markers). The data flow:

```
src/main.py  →  ChartSignalExporter  →  <MT5 Common>\Files\mt5_chart_signals.csv  →  GoldenChart_PlanLevels.mq5
```

- The Python side (`mt5_bridge/chart_signal_export.py`) writes each signal's entry/SL/TP the
  moment it enters `_execute_signal`, with a TTL so stale plans drop off the chart.
- The indicator polls the CSV every `InpRefreshSec` seconds and redraws, skipping rows whose
  `expires_epoch` has passed.

**It's on by default** — no config needed. To tune or disable, add to your `config_live_*.yaml`:

```yaml
chart_signals:
  enabled: true        # set false to stop writing the file
  ttl_minutes: 30      # how long a planned line lingers before expiring
  # data_dir: "..."    # optional override; auto-detects MT5 Common\Files otherwise
```

Indicator inputs: `InpFile` (default `mt5_chart_signals.csv`), the three colors, `InpAllSymbols`
(draw plans for every symbol vs. only the chart's), `InpShowLabels`, `InpRefreshSec`.

> Quick test without trading: run `python mt5_bridge/chart_signal_export.py` — it writes one demo
> XAUUSD signal and prints the file path. Attach `GoldenChart_PlanLevels` to an XAUUSD chart and the
> dotted lines appear within `InpRefreshSec`. (The demo line expires after 30 min.)

## Notes on fidelity

- BB and Alligator use MT5's native `iBands` / `iAlligator` engines, so the math is identical
  to the platform — only the *styling* (band fill, colors, alligator shifts) is re-skinned to
  match TradingView. The Alligator forward-shift (8/5/3) is applied as a plot shift exactly
  like the classic indicator.
- Stoch RSI is computed from scratch (RSI → stochastic → %K SMA → %D SMA), `(14,14,3,3)`.
- The dashed S/R lines are MT5 `OBJ_HLINE` objects, so they show the colored price label on the
  right scale just like the reference screenshots, and self-update on each new bar.

## Liquidity race — regenerating the coefficients

`liquidity_coefficients.mqh` is **generated**. Never hand-edit it — hand-tuning turns a
calibrated model into a guess. To change the weights:

```bash
python scripts/research_liquidity_race.py
```

That rewrites the `.mqh` and `reports/liquidity_race_calibration.md` together, then
recompile the indicator (F7). After any change to detection logic on either side, re-run
the parity check:

```bash
python scripts/check_liquidity_parity.py --dir data/parity
```

## GoldenChart_VolumeProfile

Draws the session volume profile for XAUUSD — VPOC, VAH and VAL per session, a
histogram for the most recent sessions, a multi-day composite, the initial
balance, HVN/LVN nodes, naked POCs, and a panel carrying the Auction Market
Theory read (P/b/D shape, open type, value migration, balance regime).

**It is tick density, not traded volume.** Gold trades as a broker CFD: there
are no trade prints, and MT5's `volume`/`volume_real` are zero or synthetic for
XAUUSD. Every "volume" here counts quote updates. The panel says so, and no
label anywhere may imply otherwise. This is also why there is deliberately **no
delta, CVD or footprint** — a gold CFD cannot support one honestly, and a
fabricated one would be worse than none.

**Source field.** The panel shows `TICK`, `M1` or `PENDING`. `PENDING` means
tick history is still synchronising; it is not the same as "no ticks", and the
indicator will not silently drop to M1 fidelity because of a slow sync. It
falls back to `M1` only after `InpTickRetryLimit` *consecutive* failures, and
says so in the log when it does.

**The skew threshold is calibrated, not chosen.** `InpSkewThreshold` decides
where a profile stops being a D and becomes a P or a b. It ships at `0.0`,
which is the UNCALIBRATED sentinel — the panel warns in red until you set it.
The calibrated value comes from:

```bash
python scripts/calibrate_profile_shape.py
```

which writes `reports/volume_profile_shape_calibration.md`. That report is
**generated — never hand-edit it**, and re-run the calibration if `InpRowSize`
or the session definition changes, since both alter the histogram the threshold
is computed from. Read the report's plateau width, not whether it hit its
target. The panel always prints the skew value beside the shape letter, so a
marginal session is visible as a number rather than hidden inside the label.

**The node thresholds are NOT calibrated.** `InpHVNProminencePct`,
`InpLVNRatio` and `InpNodeMinSepRows` are display heuristics — nothing was
fitted to produce them. They are a reasonable default for reading a chart and
nothing more. Do not build a trading rule on them without taking it through the
full `backtest.md` gate.

**IB is standard range-based Initial Balance**, the range of the first
`InpIBMinutes` of the session. It is **not** Fabio Valentini's IVB, whose rule
incorporates volume and is not recoverable from the course material. Do not
relabel it as IVB.

### Parity — non-optional

The definition lives in two languages: `src/microstructure/volume_profile.py`
and `src/microstructure/profile_context.py` are authoritative, and the `.mq5`
is a port of them. They can drift silently. After **any** change to either
side, set `InpExportCSV = true`, let the indicator write `vp_histogram.csv`,
`vp_levels.csv` and `vp_nodes.csv` to the MT5 `Files` directory, copy all three
into `data/parity/`, and run:

```bash
python scripts/check_volume_profile_parity.py --dir data/parity
```

Parity is taken at the **algorithm layer**: the indicator exports its own
histogram, and Python re-derives POC, value area, skew, shape, regime, open
type, value migration and the node sets from that exact histogram. Cross-vendor
volume comparison is impossible — Dukascopy ticks are not the broker's ticks —
so comparing raw row counts would be a fake test that passes or fails for
reasons unrelated to correctness.

**Do not relax `PRICE_TOL` or `SKEW_TOL` to make it pass.** A mismatch means
the two implementations genuinely disagree; fix whichever one is wrong.
