# GoldenChart — Investing.com / TradingView chart replica for MT5

Reproduces the exact visual setup from the reference charts in `/volume` (US Tech 100,
BTC/USD, **XAUUSD**, etc.) on any MT5 symbol. Built and tuned for **XAUUSD (Gold)**.

## What's in the package

| File | Window | Replicates |
|------|--------|-----------|
| `GoldenChart_Trend.mq5` | Main chart | **Bollinger Bands (20, 2)** with lavender band fill + **Williams Alligator (21, 13, 8)** (Jaw blue / Teeth red / Lips green, shifted 8/5/3) |
| `GoldenChart_Levels.mq5` | Main chart | **Live trade markers**: reads open positions + pending orders on the symbol and draws **ENTRY (black) / TP (magenta) / SL (red)** dashed lines with price tags, auto-updating as trades open/close |
| `GoldenChart_RSI.mq5` | Sub-window | **RSI (14)** with the pink-shaded 40–60 band + dotted 30/40/60/70 levels |
| `GoldenChart_StochRSI.mq5` | Sub-window | **Stoch RSI (14, 14, 3, 3)** — %K (blue) / %D (orange) + shaded 20–80 band. *(MT5 has no built-in Stoch RSI.)* |
| `GoldenChart_MACD.mq5` | Sub-window | **MACD (12, 26, 9)** — green/red histogram + MACD/signal lines |

## Install

1. In MT5: **File → Open Data Folder** → `MQL5/Indicators/`.
2. Copy all five `GoldenChart_*.mq5` files into that folder (a `GoldenChart/` subfolder is fine).
3. In **MetaEditor** open each file and press **F7** to compile (or **Compile** button). All five must compile with `0 errors`.
4. Back in MT5 they appear under **Navigator → Indicators**.

## Attach (order matters for the sub-window stacking)

Open an **XAUUSD, H4** chart (the reference charts are the `240` = H4 timeframe), then
drag the indicators on in this order:

1. `GoldenChart_Trend`  → main window (BB + Alligator)
2. `GoldenChart_Levels` → main window (S/R lines + price tags)
3. `GoldenChart_RSI`     → creates sub-window 1
4. `GoldenChart_StochRSI`→ creates sub-window 2
5. `GoldenChart_MACD`    → creates sub-window 3

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

## Notes on fidelity

- BB and Alligator use MT5's native `iBands` / `iAlligator` engines, so the math is identical
  to the platform — only the *styling* (band fill, colors, alligator shifts) is re-skinned to
  match TradingView. The Alligator forward-shift (8/5/3) is applied as a plot shift exactly
  like the classic indicator.
- Stoch RSI is computed from scratch (RSI → stochastic → %K SMA → %D SMA), `(14,14,3,3)`.
- The dashed S/R lines are MT5 `OBJ_HLINE` objects, so they show the colored price label on the
  right scale just like the reference screenshots, and self-update on each new bar.
