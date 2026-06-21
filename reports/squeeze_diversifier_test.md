# Squeeze Breakout — Diversifier Correlation Test (2026)

**Generated:** 2026-06-21 · **Script:** `scripts/validate_squeeze_diversifier.py`
Daily R-streams; squeeze = SL33/RR2.0 all-hours. Equal-risk blends. In-sample 2026.

> Final gate: a marginal stream earns a slot only if it is **uncorrelated** to the roster AND **improves the blend**. ⚠️ squeeze trades XAUUSD — same instrument as kalman — so the kalman pair is the one to watch.

## Correlation of squeeze vs roster

| Pair | Correlation |
|---|---:|
| squeeze × kalman_regime | +0.13  ⚠️ same instrument |
| squeeze × london_breakout | +0.03 |
| squeeze × monday_drift | +0.10 |
| **avg \|corr\|** | **0.09** |

## Full correlation matrix

| | kalman_regime | london_breakout | monday_drift | squeeze_breakout |
|---|---|---|---|---|
| **kalman_regime** | +1.00 | -0.04 | -0.01 | +0.13 |
| **london_breakout** | -0.04 | +1.00 | -0.07 | +0.03 |
| **monday_drift** | -0.01 | -0.07 | +1.00 | +0.10 |
| **squeeze_breakout** | +0.13 | +0.03 | +0.10 | +1.00 |

## Does adding it help the blend? (equal-risk, R-units)

| Blend | Sharpe | Max DD (R) | Total R |
|---|---:|---:|---:|
| 3-way (roster) | 3.00 | -6.3 | +25.8 |
| **4-way (+squeeze)** | 3.32 | -3.3 | +25.2 |
| squeeze standalone | 1.84 | -11.8 | +23.6 |

## Verdict

- Squeeze avg |corr| to roster **0.09**; kalman pair **+0.13** (same instrument).
- Blend Sharpe 3.00 → 3.32; maxDD -6.3R → -3.3R.

✅ **Earns a small diversifier slot (in-sample).** Squeeze is genuinely uncorrelated to the roster — even being on XAUUSD it does not track kalman (different logic: it rides breaks where kalman fades) — and adding it improves the blend. Next and final: a longer-OOS holding ≥1.05, then add at SMALL weight via the allocator (`allocator_prototype.py`), never standalone. Build it as a real strategy only at that point (CLAUDE.md propagation checklist).

> In-sample 2026 only. Even a ✅ here is provisional until a longer OOS confirms both the standalone PF (≥1.05) and the low correlation hold.