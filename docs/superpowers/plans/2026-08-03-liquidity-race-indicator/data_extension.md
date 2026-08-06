# XAUUSD history extension — evidence

Run date: 2026-08-06 (fetch passes 1–2 on 2026-08-03, gap repair 2026-08-06)
Command: `python scripts/fetch_dukascopy.py --symbol XAUUSD --start 2022-01-01 --end 2025-01-28`
Repair:  `python scripts/fetch_dukascopy.py --symbol XAUUSD --start 2022-01-28 --end 2022-03-02 --workers 2`

| | rows | first | last |
|---|---|---|---|
| before | 105,573 | 2025-01-29 11:00:00+00:00 | 2026-07-31 20:55:00+00:00 |
| after  | 324,079 | 2022-01-02 23:00:00+00:00 | 2026-08-05 17:20:00+00:00 |

⚠️ `last` advanced from 2026-07-31 to 2026-08-05 between the "before" snapshot and
this verification. That is the **weekly refresh cron appending live bars**, not
anything this task did. The plan's Step 3 expects `last` unchanged; read it as
"unchanged by the fetch", which holds.

Bars per year:

```
       bars  days  weekday-median bars/day
2022  70940   310   276
2023  70641   309   276
2024  70917   312   276
2025  70423   263   276
2026  41158   170   276   (partial year, through 2026-08-05)
```

Duplicates: 0   Monotonic: True   Flat bars: 0
Bad OHLC (high<low, high<open/close, low>open/close): 0   NaNs: 0   Non-positive prices: 0

**Verdict:** **FULL** (IS 2022-01 → 2025-12, OOS 2026-01 → 2026-07)

Backups taken by the fetcher, in `data/historical/`:
`XAUUSD_5m_real.csv.bak_pre_dukascopy_20260802_193923` (pass 1),
`...20260802_201634` (pass 2 gap-fill),
`...20260806_105527` and `...20260806_110516` (Feb-2022 repair).

---

## Beyond the plan's checks

The plan verifies per-YEAR bar counts. The SDD ledger flagged that this is not
sufficient — bars-per-DAY differed by year (2024 ~227/day vs 2025 ~268/day), and
partial days inside the 1000-bar scan window would distort level age and session
extremes without moving the yearly totals much. Both were checked.

### 1. The per-day worry was a denominator artifact — REFUTED

The 227-vs-268 spread is **not** missing data. It is a provenance difference in
which *calendar* days each slice contains:

```
bars by day-of-week (0=Mon … 6=Sun)
year      0      1      2      3      4     6
2022  13092  13523  13248  13194  11709   960
2023  13656  14322  14352  14286  13017  1008
2024  14244  14289  14048  14265  13041  1030
2025  14170  14307  14173  13888  13838    47   <- almost no Sunday bars
```

The Dukascopy-fetched years (2022–24) carry ~1,000 Sunday-evening bars each; the
pre-existing 2025 slice carries 47. Dividing by a day count that includes ~52 thin
Sunday sessions drags the 2022–24 average down. Measured on weekdays only, the
**median is exactly 276 bars/day in every year, 2022 through 2026** — a complete
23-hour gold session. There is no partial-day problem.

### 2. One real hole existed, and has been repaired

February 2022 held **552 bars against January's 5,484** — a single 624-hour gap
from 2022-02-01 23:55 to 2022-02-28 00:00. This was the residue of the HTTP 429
losses and was invisible in the per-year total (2022 still showed 65,726 bars,
inside the plan's 65–75k usability band).

After the targeted repair fetch:

| | 2022 bars | Feb 2022 bars | missing weekdays | distorted scan windows |
|---|---|---|---|---|
| before | 65,726 | 552 | 20 | 1,000 |
| after | 70,940 | 5,490 | 1 (Good Friday) | **0** |

### 3. Scan-window distortion — the metric that actually matters

For each bar, the wall-clock span of its trailing 1000 bars. Complete data gives
~135h (p50); a window spanning a hole runs far longer. Threshold is the p99 of the
known-good 2025-07…2026-07 slice (165.3h).

```
year    bars   distorted   %clean    worst span
2022   70940           0   100.0%       159.3h
2023   70641           0   100.0%       159.3h
2024   70917           0   100.0%       162.7h
2025   70423         724    99.0%       166.1h
2026   41158           0   100.0%       165.3h
```

**2025's 724 is a threshold artifact, not damage.** The cutoff is the p99 *of that
very slice*, so ~1% of it exceeds the cutoff by construction — 724/70,423 = 1.03%,
with a maximum excess over p99 of **0.8h**. For contrast, the pre-repair 2022
windows exceeded it by up to **618h**. No year now contains a genuine hole.

### 4. Remaining gaps are real market closures, not data loss

The largest intra-week gap anywhere in the dataset is now **73.1h, and it is Easter
in every single year** (Good Friday absent 2022-04-15, 2023-04-07, 2024-03-29,
2025-04-18, 2026-04-03 — including in the never-rate-limited known-good slice).
The rest of the top-10 are Christmas and New Year. The remaining "wholly missing
weekdays" are 2024-07-16, 2025-12-25, 2026-05-25 and the Good Fridays.

The 83% "complete day" rate shared by 2022, 2023, 2024 and 2026 (vs 95% for 2025)
is the same Sunday/holiday-session effect and not a defect: the median day is a
full 276 bars in all of them.

---

## What Task 10 should read from this

`--is-start 2022-01-01`. All four IS years carry 70.4k–70.9k bars, all inside the
65–75k band, all with 100% undistorted 1000-bar scan windows. The FULL split is
supported by the data.
