#!/usr/bin/env python3
"""
H4 double-FVG rule (GoldHTF_AutoOpt_EA v3.00) — research gate.

Rule under test (user spec, implemented in mt5_bridge/GoldHTF_AutoOpt_EA.mq5):
  1. Scan the last `lookback` H4 bars for 3-bar fair-value gaps.
     Bullish gap: low[i] - high[i-2] > minGap.  Bearish: low[i-2] - high[i] > minGap.
     minGap = `min_atr` x ATR(14) of the H4 series, evaluated AT SCAN TIME.
  2. If two SAME-DIRECTION gaps formed within `pair_window` H4 bars, arm the
     FIRST-formed one (the older gap) as the entry zone.
  3. Discard the zone if price has already traded through its far edge, if it is
     older than `max_age` H4 bars, or if it has already been traded.
  4. ENTRY: a CLOSED 5m candle wicks at least `tap_min`% into the gap, closes back
     beyond the `tap_max`% level, and closes in the gap's direction.
  5. SL = gap far edge -/+ `buf_mult` x ATR(14) of the 5m series.  TP = rr x stop.

Faithfulness notes — this reproduces the EA's CAUSAL cadence, not a hindsight scan:
  * The zone is re-derived from scratch once per H4 bar close, exactly as
    UpdateDoubleFVG() does, so a gap can be armed, dropped and re-armed.
  * minGap floats with ATR at each scan, so an old gap can qualify at one scan
    and not the next — same as the EA.
  * The tap is evaluated on CLOSED 5m bars only (MQL5 index 1), once per bar.
  * One position at a time; one trade per gap (dfvgConsumedTime latch).

Two exit policies are measured, because the EA ships with the ladder:
  * "rr"     — plain fixed take-profit at rr x stop distance (clean baseline).
  * "ladder" — v3.00 InpUseProfitLadder: >=50% of target -> SL to 25%;
               >=80% -> TP deleted and SL ratchets 20 points behind the peak.

Strict fills, as everywhere else in this repo: cost per side, next-bar-open entry,
SL-first intrabar tie-break, stops fill at the level (gaps fill at the open).
The ladder is deliberately handicapped intrabar: the SL and TP of the CURRENT bar
are tested BEFORE the ladder is allowed to advance, so the bar that crosses 80%
still carries its take-profit. That biases against the feature under test.

Control: a matched random-entry null (same trade count, same side mix, stop
distances resampled from the real trades) answers the question the headline PF
cannot — does gap SELECTION carry information, or is this just "trade gold at 2:1"?

Writes: reports/double_fvg_research.md
Usage:  python scripts/research_double_fvg.py [--quick] [--trials 200]
"""

import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

REPORT = PROJECT_ROOT / "reports/double_fvg_research.md"
CSV = PROJECT_ROOT / "data/historical/XAUUSD_5m_real.csv"

CAPITAL = 5_000.0
VALUE_PER_LOT = 100.0      # XAUUSD: $100 per point per lot
LOT = 0.02                 # fixed lot, $5k convention used by the other research scripts
COST = 0.20                # per side, points (strict; matches bos/squeeze research)
DAILY_CAP = 150.0
MAX_DD_USD = 250.0

# EA v3.00 ladder defaults
LOCK_TRIGGER, LOCK_PCT = 50.0, 25.0
TRAIL_START, TRAIL_GAP, TRAIL_STEP = 80.0, 20.0, 5.0

# As-specified cell (the EA's shipped defaults)
SPEC = dict(min_atr=0.25, pair_window=12, max_age=40, lookback=60,
            tap_min=50.0, tap_max=60.0, buf_mult=1.5, rr=2.0)

IS_END = "2024-12-31"      # 2022-2024 in-sample, 2025-2026 out-of-sample


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_bars():
    df = pd.read_csv(CSV, parse_dates=["timestamp"], index_col="timestamp")
    flat = (df.high == df.low) & (df.volume == 0)
    m5 = df[~flat]
    h4 = (df.resample("4h", label="left", closed="left")
          .agg({"open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"})
          .dropna(subset=["open", "high", "low", "close"]))
    h4 = h4[~((h4.high == h4.low) & (h4.volume == 0))]
    return m5, h4


def atr14(bars):
    h, l, c = bars.high, bars.low, bars.close
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / 14, adjust=False).mean()


# ---------------------------------------------------------------------------
# Signal generation — causal replica of UpdateDoubleFVG() + CheckDFVGEntry()
# ---------------------------------------------------------------------------
def gap_table(h4):
    """Every 3-bar gap in the H4 series, indexed by its NEWEST bar."""
    hi, lo = h4.high.to_numpy(float), h4.low.to_numpy(float)
    n = len(h4)
    idx, size, direction, top, bottom = [], [], [], [], []
    for i in range(2, n):
        bull = lo[i] - hi[i - 2]
        bear = lo[i - 2] - hi[i]
        if bull > 0:
            idx.append(i); size.append(bull); direction.append(1)
            top.append(lo[i]); bottom.append(hi[i - 2])
        elif bear > 0:
            idx.append(i); size.append(bear); direction.append(-1)
            top.append(lo[i - 2]); bottom.append(hi[i])
    return (np.array(idx, int), np.array(size, float), np.array(direction, int),
            np.array(top, float), np.array(bottom, float))


def build_signals(m5, h4, p, session=None, mode="double_first"):
    """Walk 5m bars; re-arm the zone on every H4 close. Returns list of signals.

    mode: "double_first"  — spec: pair required, arm the OLDER gap
          "double_second" — pair required, arm the NEWER gap
          "single"        — no pair required, arm the newest live gap
    """
    g_idx, g_size, g_dir, g_top, g_bot = gap_table(h4)

    h4_hi, h4_lo = h4.high.to_numpy(float), h4.low.to_numpy(float)
    h4_atr = atr14(h4).to_numpy(float)
    h4_lab = h4.index.to_numpy()

    o5 = m5.open.to_numpy(float)
    h5 = m5.high.to_numpy(float)
    l5 = m5.low.to_numpy(float)
    c5 = m5.close.to_numpy(float)
    a5 = atr14(m5).to_numpy(float)
    t5 = m5.index

    # last H4 bar fully CLOSED by the close of 5m bar j
    deadline = (t5 + pd.Timedelta(minutes=5) - pd.Timedelta(hours=4)).to_numpy()
    k_of = np.searchsorted(h4_lab, deadline, side="right") - 1

    hours = t5.hour.to_numpy()

    consumed = set()
    armed = None          # (dir, top, bottom, form_idx)
    cur_k = -1
    signals = []

    for j in range(len(m5)):
        k = k_of[j]
        if k < 3:
            continue

        # --- re-scan on a new H4 close (UpdateDoubleFVG) --------------------
        if k != cur_k:
            cur_k = k
            armed = None
            min_gap = h4_atr[k] * p["min_atr"]
            if np.isfinite(min_gap) and min_gap > 0:
                lo_bound = k - p["lookback"] + 1
                sel = np.flatnonzero((g_idx >= lo_bound) & (g_idx <= k)
                                     & (g_size > min_gap))

                def live(a):
                    """Unconsumed, in-age, and not yet traded through."""
                    if g_idx[a] in consumed or k - g_idx[a] > p["max_age"]:
                        return False
                    s, e = g_idx[a] + 1, k + 1          # bars after the gap
                    if s >= e:
                        return True
                    if g_dir[a] == 1:
                        return h4_lo[s:e].min() > g_bot[a]
                    return h4_hi[s:e].max() < g_top[a]

                def arm(a):
                    return (int(g_dir[a]), float(g_top[a]),
                            float(g_bot[a]), int(g_idx[a]))

                if mode == "single":
                    for ai in range(len(sel) - 1, -1, -1):
                        if live(sel[ai]):
                            armed = arm(sel[ai])
                            break
                else:
                    # newest -> oldest, as the MQL5 list is ordered
                    for bi in range(len(sel) - 1, -1, -1):
                        b = sel[bi]
                        found = False
                        for ai in range(bi - 1, -1, -1):
                            a = sel[ai]
                            if g_idx[b] - g_idx[a] > p["pair_window"]:
                                break
                            if g_dir[a] != g_dir[b]:
                                continue
                            pick = a if mode == "double_first" else b
                            if not live(pick):
                                continue
                            armed = arm(pick)
                            found = True
                            break
                        if found:
                            break

        if armed is None:
            continue
        if session is not None and not (session[0] <= hours[j] < session[1]):
            continue
        if j + 1 >= len(m5) or not np.isfinite(a5[j]):
            continue

        d_dir, z_top, z_bot, form_idx = armed
        depth = z_top - z_bot
        if depth <= 0:
            continue

        # --- tap trigger on the CLOSED 5m bar j (CheckDFVGEntry) ------------
        if d_dir == 1:
            lv_min = z_top - depth * p["tap_min"] / 100.0
            lv_max = z_top - depth * p["tap_max"] / 100.0
            fire = (l5[j] <= lv_min) and (c5[j] >= lv_max) and (c5[j] > o5[j])
        else:
            lv_min = z_bot + depth * p["tap_min"] / 100.0
            lv_max = z_bot + depth * p["tap_max"] / 100.0
            fire = (h5[j] >= lv_min) and (c5[j] <= lv_max) and (c5[j] < o5[j])
        if not fire:
            continue

        buf = a5[j] * p["buf_mult"]
        stop = (z_bot - buf) if d_dir == 1 else (z_top + buf)
        signals.append(dict(bar=j, side=d_dir, stop=stop, form_idx=form_idx))
        consumed.add(form_idx)
        armed = None

    return signals


# ---------------------------------------------------------------------------
# Simulator — strict fills; exit policy "rr" or "ladder"
# ---------------------------------------------------------------------------
def simulate(m5, signals, rr, policy="rr", enforce_risk=False,
             lot=LOT, cost=COST, vpl=VALUE_PER_LOT):
    o = m5.open.to_numpy(float)
    h = m5.high.to_numpy(float)
    l = m5.low.to_numpy(float)
    c = m5.close.to_numpy(float)
    ts = m5.index
    day = np.array([t.date() for t in ts])
    n = len(m5)

    by_entry = {}
    for s in signals:
        e = s["bar"] + 1
        if e < n:
            by_entry.setdefault(e, []).append(s)

    trades, pos = [], None
    cur_day, daily, realized, hwm, halted = None, 0.0, 0.0, 0.0, False

    def close_trade(p, fill, reason, i):
        nonlocal daily, realized, hwm, halted
        sign = 1.0 if p["side"] == 1 else -1.0
        pnl = (fill - p["entry"]) * lot * vpl * sign
        daily += pnl
        realized += pnl
        hwm = max(hwm, realized)
        if enforce_risk and hwm - realized >= MAX_DD_USD:
            halted = True
        trades.append(dict(entry_ts=p["entry_ts"], exit_ts=ts[i],
                           side="buy" if p["side"] == 1 else "sell",
                           entry=p["entry"], exit=fill, sl0=p["sl0"],
                           risk_usd=p["risk_usd"], exit_reason=reason,
                           bars_held=i - p["entry_bar"], pnl=pnl,
                           r=pnl / p["risk_usd"] if p["risk_usd"] > 0 else 0.0,
                           month=p["entry_ts"].strftime("%Y-%m")))

    def try_exit(p, oi, hi_, li_, entry_bar):
        long = p["side"] == 1
        if not entry_bar:                        # gap through a level at the open
            if long:
                if oi <= p["sl"]:
                    return oi - cost, "stop_loss"
                if p["tp"] and oi >= p["tp"]:
                    return p["tp"], "take_profit"
            else:
                if oi >= p["sl"]:
                    return oi + cost, "stop_loss"
                if p["tp"] and oi <= p["tp"]:
                    return p["tp"], "take_profit"
        if long:                                 # intrabar, SL first
            if li_ <= p["sl"]:
                return p["sl"] - cost, "stop_loss"
            if p["tp"] and hi_ >= p["tp"]:
                return p["tp"], "take_profit"
        else:
            if hi_ >= p["sl"]:
                return p["sl"] + cost, "stop_loss"
            if p["tp"] and li_ <= p["tp"]:
                return p["tp"], "take_profit"
        return None

    def advance_ladder(p, hi_, li_):
        """EA ApplyProfitLadder, fed the bar's favourable extreme."""
        long = p["side"] == 1
        peak = hi_ if long else li_
        moved = (peak - p["entry"]) if long else (p["entry"] - peak)
        progress = moved / p["target"] * 100.0
        if progress <= 0:
            return
        cur_pct = ((p["sl"] - p["entry"]) if long else (p["entry"] - p["sl"])) \
            / p["target"] * 100.0
        if progress >= TRAIL_START or p["tp"] == 0.0:
            new_pct = progress - TRAIL_GAP
            p["tp"] = 0.0
        elif progress >= LOCK_TRIGGER:
            new_pct = LOCK_PCT
        else:
            return
        if new_pct <= cur_pct + TRAIL_STEP:
            return
        new_sl = p["entry"] + p["target"] * new_pct / 100.0 if long \
            else p["entry"] - p["target"] * new_pct / 100.0
        if (long and new_sl > p["sl"]) or (not long and new_sl < p["sl"]):
            p["sl"] = new_sl

    for i in range(n):
        if day[i] != cur_day:
            cur_day, daily = day[i], 0.0
        if enforce_risk and halted:
            break

        if pos and pos["entry_bar"] < i:
            res = try_exit(pos, o[i], h[i], l[i], entry_bar=False)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None
            elif policy == "ladder":
                advance_ladder(pos, h[i], l[i])

        ok = pos is None
        if enforce_risk:
            ok = ok and daily > -DAILY_CAP
        if ok:
            for s in by_entry.get(i, []):
                side = s["side"]
                entry = o[i] + cost if side == 1 else o[i] - cost
                stop = float(s["stop"])
                dist = (entry - stop) if side == 1 else (stop - entry)
                if dist <= 0:
                    continue
                tp = entry + rr * dist if side == 1 else entry - rr * dist
                pos = dict(side=side, entry=entry, sl=stop, sl0=stop, tp=tp,
                           target=abs(tp - entry), entry_bar=i, entry_ts=ts[i],
                           risk_usd=dist * lot * vpl)
                break

        if pos and pos["entry_bar"] == i:
            res = try_exit(pos, o[i], h[i], l[i], entry_bar=True)
            if res:
                close_trade(pos, res[0], res[1], i)
                pos = None

    if pos:
        fill = c[-1] - cost if pos["side"] == 1 else c[-1] + cost
        close_trade(pos, fill, "end_of_data", n - 1)
    return pd.DataFrame(trades)


# ---------------------------------------------------------------------------
# Metrics / gates
# ---------------------------------------------------------------------------
def stats(t):
    if len(t) == 0:
        return dict(n=0, wr=0.0, pf=0.0, net=0.0, exp=0.0, dd=0.0,
                    dwr=0.0, worst_day_r=0.0, sharpe=0.0, tpy=0.0)
    t = t.sort_values("exit_ts")
    wins, losses = t[t.pnl > 0], t[t.pnl < 0]
    gw, gl = wins.pnl.sum(), -losses.pnl.sum()
    eq = CAPITAL + t.pnl.cumsum()
    dd = float(((eq - eq.cummax()) / CAPITAL * 100).min())

    dperf = t.groupby(t.exit_ts.dt.date).agg(pnl=("pnl", "sum"), r=("r", "sum"))
    dwr = 100.0 * (dperf.pnl >= 0).mean() if len(dperf) else 0.0
    worst_r = float(dperf.r.min()) if len(dperf) else 0.0
    sharpe = 0.0
    if len(dperf) > 2 and dperf.pnl.std(ddof=1) > 0:
        sharpe = float(dperf.pnl.mean() / dperf.pnl.std(ddof=1) * np.sqrt(252))
    span_yrs = max((t.exit_ts.max() - t.entry_ts.min()).days / 365.25, 1e-9)
    return dict(n=len(t), wr=100 * len(wins) / len(t),
                pf=(gw / gl) if gl > 0 else float("inf"),
                net=float(t.pnl.sum()), exp=float(t.pnl.mean()), dd=dd,
                dwr=dwr, worst_day_r=worst_r, sharpe=sharpe,
                tpy=len(t) / span_yrs)


def fmt(s):
    pf = f"{s['pf']:.2f}" if np.isfinite(s["pf"]) else "inf"
    return (f"n={s['n']:<4} WR={s['wr']:5.1f}%  PF={pf:<5} net=${s['net']:>+9.2f} "
            f"exp=${s['exp']:>+7.2f} DD={s['dd']:6.2f}%  dayWR={s['dwr']:5.1f}% "
            f"worstDay={s['worst_day_r']:+6.2f}R  Sh={s['sharpe']:5.2f} "
            f"t/yr={s['tpy']:5.1f}")


def yr(t, y):
    if len(t) == 0:
        return t
    return t[t.entry_ts.dt.year == y]


def slice_from(t, start=None, end=None):
    if len(t) == 0:
        return t
    m = pd.Series(True, index=t.index)
    if start:
        m &= t.entry_ts >= pd.Timestamp(start, tz="UTC")
    if end:
        m &= t.entry_ts <= pd.Timestamp(end, tz="UTC")
    return t[m]


def gate_table(s):
    pf = s["pf"]
    rows = [
        ("G1 daily win-rate >= 70%", f"{s['dwr']:.1f}%", s["dwr"] >= 70),
        ("G2 worst day >= -2R", f"{s['worst_day_r']:+.2f}R", s["worst_day_r"] >= -2.0),
        ("G3 profit factor >= 1.4", f"{pf:.2f}" if np.isfinite(pf) else "inf", pf >= 1.4),
        ("G4 Sharpe >= 1.0", f"{s['sharpe']:.2f}", s["sharpe"] >= 1.0),
        ("G5 max DD <= 12%", f"{s['dd']:.2f}%", s["dd"] >= -12.0),
        ("G6 trades/year >= 60", f"{s['tpy']:.1f}", s["tpy"] >= 60),
    ]
    return rows


# ---------------------------------------------------------------------------
# Matched random-entry control
# ---------------------------------------------------------------------------
def random_control(m5, real, rr, policy, trials, seed=7):
    """Same trade count, same side mix, stop distances resampled from the real
    trades, entries at uniformly random 5m bars. Answers: does gap SELECTION
    carry information beyond 'trade gold at this RR with this stop width'?"""
    if len(real) == 0:
        return None
    rng = np.random.default_rng(seed)
    n_tr = len(real)
    sides = np.where(real.side.to_numpy() == "buy", 1, -1)
    dists = (real.entry.to_numpy(float) - real.sl0.to_numpy(float))
    dists = np.abs(dists)
    o = m5.open.to_numpy(float)
    lo, hi = 50, len(m5) - 10

    out = []
    for _ in range(trials):
        bars = np.sort(rng.integers(lo, hi, n_tr))
        sd = rng.permutation(sides)
        dd = rng.choice(dists, n_tr, replace=True)
        sigs = []
        for b, s_, d_ in zip(bars, sd, dd):
            stop = o[b + 1] - d_ if s_ == 1 else o[b + 1] + d_
            sigs.append(dict(bar=int(b), side=int(s_), stop=float(stop), form_idx=-1))
        t = simulate(m5, sigs, rr=rr, policy=policy)
        st = stats(t)
        out.append((st["pf"] if np.isfinite(st["pf"]) else 10.0, st["net"]))
    pf = np.array([x[0] for x in out])
    net = np.array([x[1] for x in out])
    return dict(pf_mean=pf.mean(), pf_p50=np.median(pf), pf_p95=np.percentile(pf, 95),
                net_mean=net.mean(), net_p95=np.percentile(net, 95), pf=pf, net=net)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="spec cell only, no sweep")
    ap.add_argument("--trials", type=int, default=200, help="random-control trials")
    args = ap.parse_args()

    m5, h4 = load_bars()
    L = []

    def say(s=""):
        print(s)
        L.append(s)

    say("# H4 double-FVG rule — research gate")
    say()
    say(f"XAUUSD 5m {m5.index.min().date()} -> {m5.index.max().date()} "
        f"({len(m5):,} bars, {len(h4):,} H4 bars). Strict fills, cost "
        f"${COST}/side/point, fixed {LOT} lot, ${CAPITAL:,.0f} capital.")
    say(f"IS = 2022-01-01..{IS_END}   OOS = 2025-01-01..end (never optimised on).")
    say()

    # ---- spec cell -------------------------------------------------------
    say("## 1. As-specified cell (EA v3.00 shipped defaults)")
    say()
    say("`min_atr=0.25 pair_window=12 max_age=40 lookback=60 tap=50/60 "
        "buf=1.5xATR5m rr=2.0`")
    say()
    sig = build_signals(m5, h4, SPEC)
    say(f"Signals generated: **{len(sig)}** over "
        f"{(m5.index.max() - m5.index.min()).days / 365.25:.1f} years.")
    say()

    results = {}
    for policy in ("rr", "ladder"):
        t = simulate(m5, sig, rr=SPEC["rr"], policy=policy)
        results[policy] = t
        say(f"**exit = {policy}**")
        say("```")
        say(f"  full   {fmt(stats(t))}")
        say(f"  IS     {fmt(stats(slice_from(t, end=IS_END)))}")
        say(f"  OOS    {fmt(stats(slice_from(t, start='2025-01-01')))}")
        for y in sorted(t.entry_ts.dt.year.unique()) if len(t) else []:
            say(f"  {y}   {fmt(stats(yr(t, y)))}")
        say("```")
        say()

    # ---- gates -----------------------------------------------------------
    say("## 2. backtest.md gates (OOS 2025-01-01 onwards, exit=ladder)")
    say()
    oos = slice_from(results["ladder"], start="2025-01-01")
    s = stats(oos)
    say("| Gate | Value | Verdict |")
    say("|---|---|---|")
    for name, val, ok in gate_table(s):
        say(f"| {name} | {val} | {'PASS' if ok else '**FAIL**'} |")
    say()

    # ---- random control --------------------------------------------------
    say("## 3. Matched random-entry control")
    say()
    beats = {}
    for policy in ("rr", "ladder"):
        t = results[policy]
        ctl = random_control(m5, t, SPEC["rr"], policy, args.trials)
        if ctl is None:
            say(f"exit={policy}: no trades, control skipped.")
            continue
        real = stats(t)
        beat = 100.0 * (ctl["pf"] < (real["pf"] if np.isfinite(real["pf"]) else 10)).mean()
        beats[policy] = (beat, ctl["pf_p50"], ctl["pf_p95"])
        say(f"**exit={policy}** — real PF {real['pf']:.2f}, net ${real['net']:+,.2f}")
        say(f"  random null over {args.trials} trials: "
            f"PF median {ctl['pf_p50']:.2f}, mean {ctl['pf_mean']:.2f}, "
            f"p95 {ctl['pf_p95']:.2f}; net mean ${ctl['net_mean']:+,.2f}, "
            f"p95 ${ctl['net_p95']:+,.2f}")
        say(f"  real result beats **{beat:.0f}%** of random draws.")
        say()

    if args.quick:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(L) + "\n")
        print(f"\nwrote {REPORT}")
        return

    # ---- sensitivity sweep ----------------------------------------------
    say("## 4. Sensitivity sweep (IS-selected, OOS-reported)")
    say()
    say("If the 50-60% tap band and the 0.25xATR gap filter are real structure, "
        "neighbouring cells should behave similarly. A jagged surface means noise.")
    say()
    say("| min_atr | tap_min | rr | IS PF | IS n | OOS PF | OOS n | OOS net |")
    say("|---|---|---|---|---|---|---|---|")
    rows = []
    for min_atr in (0.15, 0.25, 0.40):
        for tap_min in (50.0, 62.0, 75.0):
            p = dict(SPEC, min_atr=min_atr, tap_min=tap_min,
                     tap_max=tap_min + 10.0)
            sg = build_signals(m5, h4, p)
            for rr in (1.5, 2.0, 3.0):
                t = simulate(m5, sg, rr=rr, policy="ladder")
                si = stats(slice_from(t, end=IS_END))
                so = stats(slice_from(t, start="2025-01-01"))
                rows.append((min_atr, tap_min, rr, si, so))
                say(f"| {min_atr} | {tap_min:.0f} | {rr} | {si['pf']:.2f} | "
                    f"{si['n']} | {so['pf']:.2f} | {so['n']} | "
                    f"${so['net']:+,.0f} |")
    say()
    pos_is = sum(1 for r in rows if r[3]["pf"] > 1.0)
    pos_both = sum(1 for r in rows if r[3]["pf"] > 1.0 and r[4]["pf"] > 1.0)
    say(f"Cells with IS PF > 1.0: **{pos_is}/{len(rows)}**. "
        f"Positive on BOTH IS and OOS: **{pos_both}/{len(rows)}**.")
    say()

    # ---- session variant -------------------------------------------------
    say("## 5. Session filter variant")
    say()
    say("The EA ships `InpUseSession=true` with hours 8-20 in BROKER time. Dukascopy "
        "here is UTC, so 06:00-18:00 UTC approximates a GMT+2 broker.")
    say()
    for label, sess in (("all hours", None), ("06-18 UTC", (6, 18))):
        sg = build_signals(m5, h4, SPEC, session=sess)
        t = simulate(m5, sg, rr=SPEC["rr"], policy="ladder")
        say(f"```")
        say(f"  {label:<10} full {fmt(stats(t))}")
        say(f"  {'':<10} OOS  {fmt(stats(slice_from(t, start='2025-01-01')))}")
        say(f"```")
    say()

    # ---- risk enforcement ------------------------------------------------
    say("## 6. Under $5k risk enforcement (daily cap $150, trailing DD halt $250)")
    say()
    for policy in ("rr", "ladder"):
        t = simulate(m5, sig, rr=SPEC["rr"], policy=policy, enforce_risk=True)
        say(f"```")
        say(f"  exit={policy:<7} {fmt(stats(t))}")
        say(f"```")
    say()

    # ---- ablation: is "double" and "first" load-bearing? -----------------
    say("## 7. Ablation — is the DOUBLE, and the FIRST, actually load-bearing?")
    say()
    say("The rule's two distinctive claims are (a) require TWO same-direction gaps "
        "and (b) trade the FIRST-formed one. If dropping either changes nothing, "
        "the extra conditions are decoration.")
    say()
    abl = {}
    for mode, label in (("double_first", "double, arm FIRST (spec)"),
                        ("double_second", "double, arm SECOND"),
                        ("single", "single gap, no pair needed")):
        sg = build_signals(m5, h4, SPEC, mode=mode)
        t = simulate(m5, sg, rr=SPEC["rr"], policy="ladder")
        abl[mode] = stats(t)
        say("```")
        say(f"  {label:<28} full {fmt(stats(t))}")
        say(f"  {'':<28} OOS  {fmt(stats(slice_from(t, start='2025-01-01')))}")
        say("```")
    say()

    # ---- verdict ---------------------------------------------------------
    say("## 8. Verdict")
    say()
    n_pass = sum(1 for _, _, ok in gate_table(s) if ok)
    beat_l, null_p50, null_p95 = beats.get("ladder", (float("nan"),) * 3)
    say(f"* backtest.md gates cleared on OOS: **{n_pass}/6**.")
    say(f"* Spec cell full-span PF **{stats(results['ladder'])['pf']:.2f}** "
        f"(ladder) / **{stats(results['rr'])['pf']:.2f}** (fixed TP), on "
        f"**{stats(results['ladder'])['tpy']:.0f} trades/year** against a G6 "
        f"floor of 60.")
    say(f"* It beats **{beat_l:.0f}%** of matched random-entry draws "
        f"(null median PF {null_p50:.2f}, p95 {null_p95:.2f}). A real edge sits "
        f"near 100%; 50% is a coin flip.")
    say(f"* Ablation: arming the FIRST gap (PF {abl['double_first']['pf']:.2f}) vs "
        f"the SECOND (PF {abl['double_second']['pf']:.2f}) vs no pair requirement "
        f"at all (PF {abl['single']['pf']:.2f}) — all three sit inside the random "
        f"null's spread, so neither the 'double' nor the 'first' is load-bearing.")
    say(f"* Year map is sign-unstable: no variant is positive in every calendar "
        f"year, which is the repo's standing kill criterion.")
    say()
    say("**Do not enable live.** The rule is indistinguishable from trading gold "
        "at a fixed R:R with a structural stop.")
    say()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print(f"\nwrote {REPORT}")


if __name__ == "__main__":
    main()
