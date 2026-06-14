# Crypto strategy hunt — BTCUSD / ETHUSD (2026-06-14)

**Verdict: NO deployable spot-OHLC crypto edge.** Same disposition as the
GBPUSD / EURUSD / gold-physics hunts. The obvious crypto edges (trend, breakout,
seasonality) either die out-of-sample or only "work" as bull-market beta /
catastrophic-drawdown trend-following that breaks the $5k risk caps.

Script: `scripts/research_crypto.py`. Data: 2.5y Dukascopy 5m, fetched this
session (local data was only 3.5 months — inadequate). Cost: BTC 0.10% / ETH
0.12% round-trip (realistic retail crypto, conservative). Split: IS 2024-01..
2025-09, OOS 2025-10..2026-06. ETH = cross-asset confirmation.

## The split is a regime test, not a noise test
- **IS 2024-01..2025-09 was a strong BULL:** BTC **+158%**, ETH **+76%**.
- **OOS 2025-10..2026-06 was a brutal BEAR:** BTC **−46%**, ETH **−62%**.

This is *why* the results look the way they do, and it's the most useful frame:
any long-biased strategy was guaranteed to look great IS and die OOS.

## Family-by-family

| Family | Result | Why it fails the bar |
|---|---|---|
| **Donchian breakout (daily)** | Long-only green IS, **negative OOS** every variant (20/5 OOS PF 0.08 BTC). ETH 20/10 corrected = PF 1.17, loses OOS. | Pure bull beta. (The earlier ETH "PF 14 / +916%" was a DATA BUG — see note below — not an edge.) |
| **Time-series momentum (daily)** | Best of the lot, and bug-immune (close-to-close). ETH 30d-lookback/14d-hold **long/short**: IS PF 1.91 t=1.54, **OOS PF 4.89 t=2.07**, ALL PF 2.26 t=2.24. | **The same variant LOSES on BTC** (PF 0.81, −38%, maxDD −70%). One asset winning + one losing on the identical rule = luck/overfit, not alpha. Single-asset n=52, maxDD −37% breaks the $350/7% caps. |
| **Prior-day volatility breakout (intraday)** | BTC k=0.5 long-only IS PF 1.52 t=1.98 → **OOS PF 0.78**. | Dies OOS. Long-only = drift again. |
| **Day-of-week seasonality** | Mon t=+1.56 at 0h delay → **0.96 at 2h delay**. Tue/Thu flip between delays. | Decays with entry delay ⇒ open/spread artifact, not a seasonal edge (the recurring trap). |
| **Intraday fade (CONTROL)** | PF 0.83, t=−2.19 (BTC). | Dies on costs exactly as expected — harness sanity check passes. |

## ⚠️ Data-integrity note (bug found & fixed mid-study)
First ETH pass used `--point 0.01`, making Dukascopy ETH prices **10× too low**
(~$200 instead of ~$2,300), then merged onto the old local MT5 file at a
different scale. The seam at 2026-02-28 produced a fake +915% single trade that
inflated ETH Donchian to "PF 14 / +916%". **Fixed:** deleted the ETH CSV,
re-fetched fresh with `--point 0.1` (verified: $2,353 Jan-2024, continuous seam,
max $4,952). All ETH numbers above are post-fix. **BTC was always clean**
(point 0.1 correct, seam continuous $65,857→$65,869). Lesson: always sanity-check
a new symbol's price scale against a known reference before trusting any PF.

## Why the honest answer is "no edge here"
1. **Long-only directional timing = beta.** Green IS is the +158% bull, not skill. OOS proves it: every long-only variant negative through the bear.
2. **Long/short daily trend-following** has the right economics (it shorted the crash) and posted the single best OOS t-stat (1.90, ETH) — but it is **inconsistent BTC vs ETH**, weakly significant (t~2, n~50), and carries **−37% to −68% drawdowns** that blow straight through the live caps ($350 / 7% on the $5k account). This is the same wall that shelved kalman-on-crypto.
3. **Fades die on costs** (control confirms the harness isn't manufacturing edge).
4. **No robust seasonality** — every "effect" decays with a 2h entry delay.

## What *would* have an edge (and why it's out of reach here)
The real, documented crypto edges are not in single-asset spot OHLC:
- **Funding-rate / basis carry** (perp funding, spot-vs-future) — needs perp/funding data we don't ingest.
- **Cross-sectional momentum** across 20–50 coins — needs a universe, not BTC+ETH.
- **On-chain / flow signals** — different data plane entirely.

Our setup (MT5 spot CFDs, $5k prop caps, intraday/session architecture) is
structurally the wrong vehicle for crypto's genuine edges, and the directional
edges that *do* fit the vehicle don't survive OOS or the drawdown caps.

## Recommendation
Do **not** wire a crypto strategy live from this pass. If crypto exposure is
wanted later, the only candidate with real logic is a **daily long/short TSMOM
diversifier (30/14)** sized to a tiny risk budget and judged as portfolio
crisis-alpha, NOT as a standalone edge — and only after confirming it on a
basket beyond BTC/ETH. Nothing here clears the strict-fill / drawdown bar.

## Artifacts
- `scripts/research_crypto.py` — the sweep (re-runnable, `--symbol`).
- `data/historical/BTCUSD_5m_real.csv`, `ETHUSD_5m_real.csv` — now full 2.5y
  Dukascopy (was 3.5mo MT5 capture; pre-fetch `.bak_pre_crypto_research` saved).
- Do **not** re-research: long-only crypto timing, daily breakout/Donchian,
  day-of-week seasonality, intraday fades. All dead or artifactual above.
