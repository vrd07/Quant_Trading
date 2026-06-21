# Kalman A/B — RANGE-on vs RANGE-off

**Generated:** 2026-06-21 · **Script:** `scripts/validate_kalman_range_drop.py`
Live geometry SL33/RR1/lot0.04/cap$295/$50,000. RANGE-off = drop OU mean-reversion signals; re-simulate the same tape. Both years.

| Year | Variant | N | PF | Net$ | MaxDD% |
|---|---|---:|---:|---:|---:|
| 2025 (OOS) | RANGE-on (baseline) | 563 | 1.19 | +6,613 | -4.5% |
| 2025 (OOS) | **RANGE-off** | 529 | 1.09 | +3,071 | -4.8% |
| 2026 (in-sample) | RANGE-on (baseline) | 610 | 1.08 | +3,243 | -6.7% |
| 2026 (in-sample) | **RANGE-off** | 549 | 1.16 | +5,444 | -5.2% |

### The RANGE subset being removed

| Year | RANGE N | RANGE PF | RANGE Net$ |
|---|---:|---:|---:|
| 2025 (OOS) | 80 | 1.04 | +233 |
| 2026 (in-sample) | 126 | 0.83 | -1,565 |

## Verdict

- **2026:** PF 1.08→1.16 (+0.08), net +3,243→+5,444, DD -6.7%→-5.2%.
- **2025 OOS:** PF 1.19→1.09 (-0.10), net +6,613→+3,071, DD -4.5%→-4.8%.

❌ **DO NOT drop RANGE statically — it is REGIME-DEPENDENT.** It helps 2026 (PF +0.08, net +2,201) but **hurts 2025 OOS MORE** (PF -0.10, net -3,541). Across the two years the drop is net-negative.

- **The situation-map's 'clean win' was an artifact.** That test applied a size *multiplier* to the tape post-hoc; this A/B actually *removes* the signals, which re-allocates `max_positions` slots to other trend trades (note 2025 net fell -3,541 though the removed RANGE subset was only +$233). Real removal ≠ post-hoc down-sizing.
- **RANGE is not dead — it's regime-conditional:** PF 0.83 in 2026's down/chop year, 1.04 in 2025's calmer tape. A static drop bets on one regime.
- **Correct handling = the DECAY-FLOOR ALLOCATOR**, not a config drop: let the sub-mode (or the whole strategy) be defunded *dynamically* when its trailing edge goes negative, and restored when it recovers. That captures the 2026 benefit without paying the 2025 cost.

> This makes kalman *bleed less in one regime*, it does not create a durable edge (entry still OOS-dead: `project_kalman_v2_retune_no_edge`). Net lesson: even 'drop the dead sub-mode' is regime-dependent — dynamic defunding beats static cuts.