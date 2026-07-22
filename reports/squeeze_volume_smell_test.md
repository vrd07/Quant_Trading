# Squeeze-breakout volume-filter smell-test — XAUUSD

Range 2026-05-08..2026-07-15 · 41 labeled squeeze breakouts (26 SELL) · native 33/66pt geometry · cost 0.5pt/side · GC=F hourly volume.

## Break RVOL split (all trades)

**break_rvol** (median 0.77)

| bkt | n | win% | mean_R |
|-----|---|------|--------|
| high  |  21 |   48% |  +0.40 |
| low   |  20 |   50% |  +0.47 |

## Coil RVOL split (all trades)

**coil_rvol** (median 0.43)

| bkt | n | win% | mean_R |
|-----|---|------|--------|
| high  |  21 |   57% |  +0.68 |
| low   |  20 |   40% |  +0.17 |

## Break RVOL split (SELL only — the bleed)

**break_rvol · SELL** (median 0.60)

| bkt | n | win% | mean_R |
|-----|---|------|--------|
| high  |  13 |   69% |  +1.05 |
| low   |  13 |   54% |  +0.59 |

## Verdict

**RED** on break_rvol.

⚠️ This sample is 41 trades over ~2.5 months — far too few for significance. A split can occur by chance, so GREEN justifies BUYING multi-year GC data for a proper every-year test — it does NOT justify any live change. RED = drop the hypothesis.

Secondary observations (not gated — noted for the paid-data test, not acted on): the break-surge half is flat, but the coil half shows a +0.68R (high-coil) vs +0.17R (low-coil) split. Note this runs OPPOSITE to the dry-up hypothesis — HIGHER coil volume (less contraction), not more dry-up, associates with better outcomes, which reads more like an overall-activity/trend proxy than the textbook coil. The SELL-only break_rvol cut (+1.05R high vs +0.59R low) is the one cell pointing the hypothesized way on the strategy's known bleed. Both are n<30 curiosities, not findings.

Caveats: GC is COMEX futures (not spot XAUUSD; ~23h session, maintenance break) — volume used only as a relative percentile. yfinance GC daily volume is broken; hourly only. 1h volume is coarser than the 15m break; break_rvol uses the last COMPLETED hour (causal, lagged ≤1h).
