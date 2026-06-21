# Kalman BUY-gate — Walk-Forward Validation

**Generated:** 2026-06-21 · **Script:** `scripts/validate_kalman_buygate.py`
Symmetric HTF 1h-EMA(50) BUY gate OFF (live baseline) vs ON. SELL gate stays on in both. Params: SL33/RR1/lot0.04/cost0.20/cap$295 on $50k. 2025 is OOS.

| Year | Gate | N | PF | Net$ | MaxDD% | BUY PF (n) | SELL PF (n) |
|---|---|---:|---:|---:|---:|---:|---:|
| 2025 (OOS) | OFF (live) | 563 | 1.19 | +6,613 | -4.5% | 1.40 (361) | 0.91 (202) |
| 2025 (OOS) | **ON** | 532 | 1.15 | +4,904 | -5.1% | 1.54 (286) | 0.82 (246) |
| 2026 (in-sample) | OFF (live) | 610 | 1.08 | +3,243 | -6.7% | 0.95 (369) | 1.32 (241) |
| 2026 (in-sample) | **ON** | 556 | 1.16 | +5,510 | -6.2% | 1.05 (259) | 1.27 (297) |

## Verdict

⚠️ **In-sample only.** 2026 PF +0.08 but 2025 (OOS) PF -0.05 — the gate is fit to 2026's regime and does NOT generalize. Do NOT enable live.


### Why it fails OOS (the instructive part)

The gate does **exactly what it was designed to** — it lifts BUY-side PF in BOTH years (2026 0.95→1.05, 2025 1.40→1.54): it really does remove weak counter-trend longs. It still loses OOS because:
1. **2025 was an up year — BUYs were the WINNING side (PF 1.40).** Gating BUYs down in an uptrend removes profitable dip-buys. The situation-map's in-sample win was 2026's down/round-trip regime flattering trend-alignment; flip the regime and it inverts. This is the same beta-not-alpha lesson from the demean test (`project_kalman_beta_vs_alpha`).
2. **Slot interaction:** fewer BUY entries free up `max_positions` slots and the no-hedge directional lock, letting more losing SELLs through (2025 SELL count 202→246, PF 0.91→0.82). This is real live behaviour, not a sim artifact.

**Conclusion:** keep `htf_buy_filter_enabled` shipped but **default OFF**. A trend-alignment gate is a regime bet, not a durable edge — wiring it as a config flag lets it be A/B'd later without another code change. It does not revive the OOS-dead entry (`project_kalman_v2_retune_no_edge`).