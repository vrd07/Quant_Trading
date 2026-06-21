# Squeeze Breakout — Longer-OOS Gate

**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_longoos.py`
Full span 2025-02-03 → 2026-06-16 (~16 months, 569 signals). SL33/RR2.0, lot0.04, cap$295. Per-quarter view uses the STRICT (0.50) run.

## 1. Standalone PF — full span

| Fills | N | Win% | PF | Net$ | MaxDD% |
|---|---:|---:|---:|---:|---:|
| realistic 0.20 | 345 | 33.0% | 1.15 | +3,911 | -4.8% |
| strict 0.50 | 347 | 33.4% | 1.15 | +4,022 | -4.9% |

## 2. Per-quarter stability (strict 0.50)

| Quarter | N | PF | Net$ |
|---|---:|---:|---:|
| 2025Q1 | 19 | 2.46 | +1,566 |
| 2025Q2 | 64 | 0.97 | -122 |
| 2025Q3 | 33 | 0.39 | -2,036 |
| 2025Q4 | 77 | 1.29 | +1,616 |
| 2026Q1 | 86 | 1.50 | +3,066 |
| 2026Q2 | 68 | 0.99 | -68 |

- Quarters with PF ≥ 1.0: **3/6** (≥10 trades). Drop-best-quarter PF: **1.05** (net +956) — checks the edge isn't carried by one window.

## 3. Correlation persistence — squeeze × kalman (same instrument)

- 2025: **+0.20** · 2026: **+0.13**. Low in BOTH years — the breakout-vs-fade independence is structural, not a 2026 fluke; it stays a real diversifier of kalman.

## Verdict

⚠️ **DOES NOT PASS — edge is real in aggregate but UNSTABLE.** Full-span strict PF 1.15 clears 1.05, but only **3/6 quarters are positive** and one (2025Q3, PF 0.39, −$2,036) is a disaster; drop-best-quarter falls to 1.05. The earlier 2026-only diversifier ✅ was **period-flattered** — 2026 happened to contain its strong quarters. The correlation property DOES hold (+0.20/+0.13), so it remains a genuine *diversifier*, but the standalone edge is too quarter-dependent to promote. **Verdict: research-only.** If ever added, only at tiny weight behind the allocator's decay-floor (which would defund it through quarters like 2025Q3) — never as a standalone or fixed-weight position.

> Even a ✅ is in-sample-on-history (2025-26 only) and depends on the marginal RR2.0 edge; size it small and let the allocator's decay-floor pull it if it fades.