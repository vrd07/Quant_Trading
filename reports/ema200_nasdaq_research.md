# EMA 200 NASDAQ Strategy — Research (new_strategies.md #2)

Generated 2026-07-07 01:04. NAS100 5m (2024-01-01 → 2026-06-22), strict fills, cost 1.0/side, fixed lot 1.0, $1.0/pt/lot (PLACEHOLDER spec — verify vs broker ticker).

Anchor 13:40 UTC (19:10 IST); trigger close ≤ 15:40 UTC; one entry/day; SL = anchor extreme; TP = 2R (spec).

Signals: 529 over 757 days (316 BUY / 213 SELL).

### Variants (raw, risk-bypassed)

| Variant | Trades | WR | PF full | Net | MaxDD | PF 2024 | PF 2025 | PF 2026 |
|---|---|---|---|---|---|---|---|---|
| hold to SL/TP | 524 | 35% | 1.04 | $+623.66 | -31.50% | 0.90 | 1.41 | 0.79 |
| EOD close 21:55 UTC | 524 | 35% | 1.03 | $+514.48 | -33.86% | 0.92 | 1.40 | 0.76 |

### Cost robustness (hold variant)

| Cost/side | PF full | Net | PF 2024 | PF 2025 | PF 2026 |
|---|---|---|---|---|---|
| 1.0 | 1.04 | $+623.66 | 0.90 | 1.41 | 0.79 |
| 2.0 | 0.98 | $-285.55 | 0.87 | 1.26 | 0.78 |
| 3.0 | 0.93 | $-1207.98 | 0.80 | 1.18 | 0.78 |

Stop distance: median 36.4 pts (p25 19.5 / p75 60.3) → median risk ~$36/trade at lot 1.0.

### Full span (hold, raw)

**n=524  WR= 34.5%  PF=1.04  net=$  +623.66  exp=$  +1.19  DD=-31.50%**

| Month | Trades | WR | PF | Net |
|---|---|---|---|---|
| 2024-01 | 11 | 18% | 0.75 | $-38.71 |
| 2024-02 | 20 | 10% | 0.21 | $-274.65 |
| 2024-03 | 14 | 36% | 1.27 | $+89.75 |
| 2024-04 | 12 | 25% | 1.15 | $+47.97 |
| 2024-05 | 19 | 37% | 0.74 | $-123.31 |
| 2024-06 | 17 | 18% | 0.37 | $-348.20 |
| 2024-07 | 18 | 44% | 1.45 | $+243.87 |
| 2024-08 | 17 | 41% | 1.51 | $+322.60 |
| 2024-09 | 16 | 38% | 1.27 | $+106.05 |
| 2024-10 | 20 | 20% | 0.35 | $-581.67 |
| 2024-11 | 17 | 35% | 1.37 | $+96.48 |
| 2024-12 | 18 | 39% | 0.79 | $-51.14 |
| 2025-01 | 20 | 40% | 1.35 | $+122.41 |
| 2025-02 | 17 | 24% | 0.74 | $-61.15 |
| 2025-03 | 14 | 43% | 1.18 | $+108.03 |
| 2025-04 | 18 | 50% | 1.49 | $+452.90 |
| 2025-05 | 18 | 33% | 1.14 | $+86.60 |
| 2025-06 | 16 | 44% | 1.78 | $+264.34 |
| 2025-07 | 16 | 31% | 0.86 | $-60.40 |
| 2025-08 | 18 | 61% | 3.26 | $+922.62 |
| 2025-09 | 18 | 56% | 1.71 | $+252.75 |
| 2025-10 | 18 | 56% | 2.72 | $+535.74 |
| 2025-11 | 19 | 26% | 0.63 | $-145.15 |
| 2025-12 | 21 | 14% | 0.29 | $-299.81 |
| 2026-01 | 20 | 35% | 1.21 | $+54.97 |
| 2026-02 | 18 | 33% | 0.75 | $-107.45 |
| 2026-03 | 22 | 23% | 0.60 | $-419.95 |
| 2026-04 | 19 | 58% | 2.67 | $+738.19 |
| 2026-05 | 20 | 30% | 0.77 | $-295.84 |
| 2026-06 | 13 | 15% | 0.35 | $-1014.17 |

| Split | Trades | WR | PF | Net |
|---|---|---|---|---|
| BUY | 314 | 35% | 1.10 | $+817.04 |
| SELL | 210 | 33% | 0.97 | $-193.38 |
| Asia 0-6h | 0 | 0% | 0.00 | $+0.00 |
| London 7-12h | 0 | 0% | 0.00 | $+0.00 |
| NY 13-20h | 524 | 35% | 1.04 | $+623.66 |
| Late 21-23h | 0 | 0% | 0.00 | $+0.00 |

Exit reasons: {'stop_loss': 343, 'take_profit': 181}; avg bars held 14.1 (~3.5h)

### Last 12 months (hold, raw)

**n=227  WR= 37.0%  PF=1.05  net=$  +353.28  exp=$  +1.56  DD=-31.50%**

| Month | Trades | WR | PF | Net |
|---|---|---|---|---|
| 2025-06 | 5 | 60% | 4.56 | $+191.78 |
| 2025-07 | 16 | 31% | 0.86 | $-60.40 |
| 2025-08 | 18 | 61% | 3.26 | $+922.62 |
| 2025-09 | 18 | 56% | 1.71 | $+252.75 |
| 2025-10 | 18 | 56% | 2.72 | $+535.74 |
| 2025-11 | 19 | 26% | 0.63 | $-145.15 |
| 2025-12 | 21 | 14% | 0.29 | $-299.81 |
| 2026-01 | 20 | 35% | 1.21 | $+54.97 |
| 2026-02 | 18 | 33% | 0.75 | $-107.45 |
| 2026-03 | 22 | 23% | 0.60 | $-419.95 |
| 2026-04 | 19 | 58% | 2.67 | $+738.19 |
| 2026-05 | 20 | 30% | 0.77 | $-295.84 |
| 2026-06 | 13 | 15% | 0.35 | $-1014.17 |

| Split | Trades | WR | PF | Net |
|---|---|---|---|---|
| BUY | 139 | 40% | 1.21 | $+871.42 |
| SELL | 88 | 33% | 0.85 | $-518.14 |
| Asia 0-6h | 0 | 0% | 0.00 | $+0.00 |
| London 7-12h | 0 | 0% | 0.00 | $+0.00 |
| NY 13-20h | 227 | 37% | 1.05 | $+353.28 |
| Late 21-23h | 0 | 0% | 0.00 | $+0.00 |

Exit reasons: {'stop_loss': 143, 'take_profit': 84}; avg bars held 17.2 (~4.3h)

### 2026 YTD deep dive (hold, raw)

**n=112  WR= 33.0%  PF=0.79  net=$ -1044.24  exp=$  -9.32  DD=-31.24%**

| Month | Trades | WR | PF | Net |
|---|---|---|---|---|
| 2026-01 | 20 | 35% | 1.21 | $+54.97 |
| 2026-02 | 18 | 33% | 0.75 | $-107.45 |
| 2026-03 | 22 | 23% | 0.60 | $-419.95 |
| 2026-04 | 19 | 58% | 2.67 | $+738.19 |
| 2026-05 | 20 | 30% | 0.77 | $-295.84 |
| 2026-06 | 13 | 15% | 0.35 | $-1014.17 |

| Split | Trades | WR | PF | Net |
|---|---|---|---|---|
| BUY | 69 | 38% | 0.95 | $-129.74 |
| SELL | 43 | 26% | 0.60 | $-914.50 |
| Asia 0-6h | 0 | 0% | 0.00 | $+0.00 |
| London 7-12h | 0 | 0% | 0.00 | $+0.00 |
| NY 13-20h | 112 | 33% | 0.79 | $-1044.24 |
| Late 21-23h | 0 | 0% | 0.00 | $+0.00 |

Exit reasons: {'stop_loss': 75, 'take_profit': 37}; avg bars held 17.1 (~4.3h)

### ENFORCED ($150 daily / $250 trailing halt, fixed lot 1.0)

**n=18   WR= 11.1%  PF=0.41  net=$  -167.97  exp=$  -9.33  DD= -5.05%**

| Month | Trades | WR | PF | Net |
|---|---|---|---|---|
| 2024-01 | 11 | 18% | 0.75 | $-38.71 |
| 2024-02 | 7 | 0% | 0.00 | $-129.26 |

| Split | Trades | WR | PF | Net |
|---|---|---|---|---|
| BUY | 11 | 9% | 0.08 | $-155.38 |
| SELL | 7 | 14% | 0.89 | $-12.59 |
| Asia 0-6h | 0 | 0% | 0.00 | $+0.00 |
| London 7-12h | 0 | 0% | 0.00 | $+0.00 |
| NY 13-20h | 18 | 11% | 0.41 | $-167.97 |
| Late 21-23h | 0 | 0% | 0.00 | $+0.00 |

Exit reasons: {'stop_loss': 16, 'take_profit': 2}; avg bars held 4.1 (~1.0h)

Enforced run HALTED by trailing-DD kill switch: 18 of 524 raw trades taken.

## Verdict

**FAIL — do not ship.** Full-span PF 1.04 is noise around breakeven: 2024 PF 0.90,
2025 PF 1.41, 2026 PF 0.79 — a single good year bracketed by two losers, raw max
DD -31.5% at a fixed 1.0 lot, and the EOD-close variant is the same shape. Cost
robustness is moot at this PF. ENFORCED ($150 daily / $250 trailing / fixed lot)
the run halts after 18 trades at PF 0.41. The repo gate requires clearing PF ~1.10+
on BOTH walk-forward years; this clears neither. Note 2025's PF 1.41 coincides
with the strongest one-way NASDAQ leg (16.8k->30k full-span) — the "edge" is
regime beta, not the EMA200 anchor rule.

## Production-engine backtest (2026-07-07, run_backtest --timeframe 5m --slippage strict, $5k config)

- **Raw:** PF 0.58, −$2,143.95 (−42.88%), 527 trades, WR 28.7%, MaxDD −42.93%.
- **--enforce-risk:** PF 0.19, −$254.05 (−5.08%), 23 trades — trailing-DD kill switch halts the run.
- Note the strict fill tables carry NO NAS100 spread/slippage entries (zero cost charged),
  so these production numbers are OPTIMISTIC. Confirms the research FAIL verdict; the
  strategy is implemented and wired live BY USER DECISION (2026-07-07, "tune later").

### Production engine, 2026 YTD only (--start 2026-01-01, strict, $5k)
- Raw: PF 0.52, −$698 (−13.96%), 111 trades, WR 27.0%, MaxDD −14.4%.
- --enforce-risk: PF 0.43, −$242 (−4.84%), 28 trades — kill switch halt.
- Matches the research year map (2026 PF 0.79 raw): 2026 is a losing year for
  this rule in every harness.
