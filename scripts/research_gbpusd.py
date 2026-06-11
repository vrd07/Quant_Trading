#!/usr/bin/env python3
"""
GBPUSD-dedicated edge scan (research only, nothing wired).

Prior art (project_fx_majors_strategy_research, 2026-06-11): on GBPUSD the
generic scan killed london_breakout (PF 0.55-0.88 — it is a FADE pair),
ou_fade_1h, donchian_1h, asia_fade_15m, and the EURUSD spread. Naive
breakout inversion did not survive costs IS. So this pass tests
structure-confirmed fades (better entry than naive inversion, structural
stop) plus session-reversion ideas:

  A sweep_reverse      — London pokes beyond the Asia range, then a 15m
                         close back inside confirms the failed break; fade
                         toward the other side. Stop beyond sweep extreme.
  B prevday_sweep_fade — same pattern against the previous DAY's high/low
                         (bigger level -> wider stop relative to bar range,
                         which is what survives strict fills).
  C london_close_fade  — if the 07-15 UTC London move is overextended vs
                         daily ATR, fade it at 16:00, flat by 21:00.
  D ny_continuation    — control: trade WITH the London move at 16:00
                         (expected dead; continuation is dead everywhere).

All causal: signal on a completed bar, fill next bar open, round-trip cost
charged. Median stop distance is reported because the standing kill-rule is
that tight-stop edges need research PF ~1.5+ to survive the strict-fill gate.

Split: IS = 2024-01-01..2025-09-30, OOS = 2025-10-01..end.
Gate: PF_net >= 1.3 IS AND same-direction OOS with n >= 30; judge parameter
plateaus, never sweep winners.

Usage:
    python scripts/research_gbpusd.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(PROJECT_ROOT))

from research_fx_majors import COSTS, PIP, load, resample, split_is_oos, stats

SYMBOL = "GBPUSD"
COST = COSTS[SYMBOL]
PIPSZ = PIP[SYMBOL]


def _exit_trade(bars: pd.DataFrame, entry: float, side: int,
                stop: float, target: float | None) -> float:
    """Walk bars after entry; stop checked before target on the same bar
    (pessimistic). Returns raw price pnl; falls back to last close."""
    for _, b in bars.iterrows():
        if (side == 1 and b.low <= stop) or (side == -1 and b.high >= stop):
            return (stop - entry) * side
        if target is not None and (
                (side == 1 and b.high >= target) or (side == -1 and b.low <= target)):
            return (target - entry) * side
    return (bars.iloc[-1].close - entry) * side


def _daily_atr(df_5m: pd.DataFrame, n: int = 14) -> pd.Series:
    d = resample(df_5m, "1D")
    pc = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ---------------------------------------------------------------- candidates

def sweep_reverse(m15: pd.DataFrame, buf_frac: float = 0.15,
                  confirm_bars: int = 8, tp_mode: str = "opposite") -> pd.DataFrame:
    """Asia range 00:00-06:59 UTC. During 07:00-11:45 a 15m bar trades beyond
    the range, then a later close back inside confirms the failure within
    confirm_bars; fade at next open. Stop = sweep extreme +/- buf_frac*range.
    TP: 'opposite' = far side of range, 'mid' = range midpoint, 'none'.
    Flat at 16:00 UTC. One trade per day."""
    rows = {}
    for day, g in m15.groupby(m15.index.date):
        asia = g.between_time("00:00", "06:59")
        if len(asia) < 12:
            continue
        hi, lo = asia.high.max(), asia.low.min()
        rng = hi - lo
        if rng <= 0:
            continue
        day_bars = g.between_time("07:00", "15:45")
        if day_bars.empty:
            continue
        times = day_bars.index.time
        detect_end = pd.Timestamp("11:45").time()
        swept, extreme, sweep_i = 0, np.nan, -1
        for i in range(len(day_bars)):
            if times[i] > detect_end:
                break
            bar = day_bars.iloc[i]
            if swept == 0:
                if bar.high > hi:
                    swept, extreme, sweep_i = 1, bar.high, i
                elif bar.low < lo:
                    swept, extreme, sweep_i = -1, bar.low, i
                continue
            if i - sweep_i > confirm_bars:
                break  # breakout held -> no failed-break trade today
            if swept == 1:
                extreme = max(extreme, bar.high)
                confirmed = bar.close < hi
            else:
                extreme = min(extreme, bar.low)
                confirmed = bar.close > lo
            if not confirmed or i + 1 >= len(day_bars):
                continue
            side = -swept
            entry = day_bars.iloc[i + 1].open
            stop = extreme + swept * buf_frac * rng
            target = {"opposite": lo if side == -1 else hi,
                      "mid": (hi + lo) / 2,
                      "none": None}[tp_mode]
            pnl = _exit_trade(day_bars.iloc[i + 1:], entry, side, stop, target)
            rows[day_bars.index[i]] = ((pnl - COST) / PIPSZ,
                                       abs(entry - stop) / PIPSZ)
            break
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def prevday_sweep_fade(m15: pd.DataFrame, atr_d: pd.Series,
                       buf_atr: float = 0.10, confirm_bars: int = 8,
                       tp_mode: str = "mid") -> pd.DataFrame:
    """During 07:00-14:45 UTC price pokes beyond the previous day's high/low,
    then a 15m close back inside confirms; fade at next open. Stop = sweep
    extreme +/- buf_atr*dailyATR. TP 'mid' = previous-day midpoint, '1r',
    'none'. Flat at 20:45 UTC. One trade per day."""
    d = resample(m15, "1D")
    pdh, pdl = d.high.shift(1), d.low.shift(1)
    rows = {}
    for day, g in m15.groupby(m15.index.date):
        ts_day = pd.Timestamp(day, tz="UTC")
        if ts_day not in pdh.index or np.isnan(pdh.get(ts_day, np.nan)):
            continue
        h_lvl, l_lvl = pdh[ts_day], pdl[ts_day]
        atr = atr_d.get(ts_day, np.nan)
        if np.isnan(atr) or atr <= 0:
            continue
        day_bars = g.between_time("07:00", "20:45")
        if day_bars.empty:
            continue
        times = day_bars.index.time
        detect_end = pd.Timestamp("14:45").time()
        swept, extreme, sweep_i = 0, np.nan, -1
        for i in range(len(day_bars)):
            if times[i] > detect_end:
                break
            bar = day_bars.iloc[i]
            if swept == 0:
                if bar.high > h_lvl:
                    swept, extreme, sweep_i = 1, bar.high, i
                elif bar.low < l_lvl:
                    swept, extreme, sweep_i = -1, bar.low, i
                continue
            if i - sweep_i > confirm_bars:
                break
            if swept == 1:
                extreme = max(extreme, bar.high)
                confirmed = bar.close < h_lvl
            else:
                extreme = min(extreme, bar.low)
                confirmed = bar.close > l_lvl
            if not confirmed or i + 1 >= len(day_bars):
                continue
            side = -swept
            entry = day_bars.iloc[i + 1].open
            stop = extreme + swept * buf_atr * atr
            risk = abs(entry - stop)
            target = {"mid": (h_lvl + l_lvl) / 2,
                      "1r": entry + side * risk,
                      "none": None}[tp_mode]
            pnl = _exit_trade(day_bars.iloc[i + 1:], entry, side, stop, target)
            rows[day_bars.index[i]] = ((pnl - COST) / PIPSZ, risk / PIPSZ)
            break
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def london_session_trade(m15: pd.DataFrame, atr_d: pd.Series, k: float = 0.5,
                         direction: int = -1, stop_atr: float = 0.5) -> pd.DataFrame:
    """Measure the London move close(15:00 bar) - open(07:00 bar). If
    |move| >= k*dailyATR, enter at the 16:00 open. direction=-1 fades the
    move (london_close_fade); +1 trades with it (ny_continuation control).
    Stop = stop_atr*ATR, no TP, flat at 20:45 UTC."""
    rows = {}
    for day, g in m15.groupby(m15.index.date):
        ts_day = pd.Timestamp(day, tz="UTC")
        atr = atr_d.get(ts_day, np.nan)
        if np.isnan(atr) or atr <= 0:
            continue
        ldn = g.between_time("07:00", "15:00")
        ny = g.between_time("16:00", "20:45")
        if len(ldn) < 20 or ny.empty:
            continue
        move = ldn.iloc[-1].close - ldn.iloc[0].open
        if abs(move) < k * atr:
            continue
        side = direction * int(np.sign(move))
        entry = ny.iloc[0].open
        stop = entry - side * stop_atr * atr
        pnl = _exit_trade(ny.iloc[1:], entry, side, stop, None)
        rows[ny.index[0]] = ((pnl - COST) / PIPSZ, stop_atr * atr / PIPSZ)
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "stop_p"])


def weekend_gap_fade(m5: pd.DataFrame, atr_d: pd.Series, min_gap_p: float = 3.0,
                     stop_mode: str = "2gap", max_hold_h: int = 24) -> pd.DataFrame:
    """Fade the weekend gap: side = -sign(SundayOpen - FridayClose) when
    |gap| >= min_gap_p pips. Entry = first 5m open after the weekend; the
    round-trip cost is DOUBLED (Sunday-open spreads). TP = Friday close
    (gap fill). stop_mode: '2gap' = 2x|gap| beyond entry (floor 10p),
    '0.5atr', 'none'. Time exit after max_hold_h hours."""
    cost = 2 * COST
    rows = {}
    idx = m5.index
    gaps_ts = idx.to_series().diff()
    weekend_starts = np.where(gaps_ts > pd.Timedelta(hours=24))[0]
    for i in weekend_starts:
        fri_close = m5.close.iloc[i - 1]
        first = m5.iloc[i]
        gap = first.open - fri_close
        if abs(gap) < min_gap_p * PIPSZ:
            continue
        side = -int(np.sign(gap))
        entry = first.open
        atr = atr_d.get(idx[i].normalize(), np.nan)
        if stop_mode == "2gap":
            stop = entry - side * max(2 * abs(gap), 10 * PIPSZ)
        elif stop_mode == "0.5atr":
            if np.isnan(atr):
                continue
            stop = entry - side * 0.5 * atr
        else:
            stop = entry - side * 1e9 * PIPSZ
        target = fri_close
        end = idx[i] + pd.Timedelta(hours=max_hold_h)
        window = m5.iloc[i:][idx[i:] <= end]
        pnl = _exit_trade(window, entry, side, stop, target)
        rows[idx[i]] = ((pnl - cost) / PIPSZ, abs(entry - stop) / PIPSZ,
                        abs(gap) / PIPSZ)
    return pd.DataFrame.from_dict(rows, orient="index",
                                  columns=["pnl", "stop_p", "gap_p"])


# ---------------------------------------------------------------- diagnostics

def sweep_diagnostics(m15: pd.DataFrame) -> None:
    """How often does a London poke beyond the Asia range fail (close back
    inside within 2h), and what happens after? IS only."""
    m15 = m15[m15.index <= "2025-09-30 23:59:59+00:00"]
    n_sweep, n_fail, cont = 0, 0, []
    for day, g in m15.groupby(m15.index.date):
        asia = g.between_time("00:00", "06:59")
        if len(asia) < 12:
            continue
        hi, lo = asia.high.max(), asia.low.min()
        if hi <= lo:
            continue
        window = g.between_time("07:00", "11:45")
        rest = g.between_time("07:00", "15:45")
        for i in range(len(window)):
            bar = window.iloc[i]
            swept = 1 if bar.high > hi else (-1 if bar.low < lo else 0)
            if swept == 0:
                continue
            n_sweep += 1
            lvl = hi if swept == 1 else lo
            after = rest[rest.index > window.index[i]]
            fail_8 = after.iloc[:8]
            failed = ((fail_8.close < lvl).any() if swept == 1
                      else (fail_8.close > lvl).any())
            if failed:
                n_fail += 1
                cont.append((after.iloc[-1].close - lvl) * -swept if len(after) else 0)
            break
    cont = pd.Series(cont) / PIPSZ
    print(f"    IS days with an Asia-range sweep: {n_sweep}; "
          f"failed within 2h: {n_fail} ({100 * n_fail / max(n_sweep, 1):.0f}%)")
    if len(cont):
        print(f"    after a failed sweep, move to 16:00 in fade direction: "
              f"avg {cont.mean():+.1f}p, median {cont.median():+.1f}p")


# ---------------------------------------------------------------- main

def show_df(df: pd.DataFrame, label: str) -> None:
    r = stats(df.pnl if len(df) else pd.Series(dtype=float), label)
    if r.get("n", 0) == 0:
        print(f"    {label:<26} n=0")
        return
    print(f"    {r['label']:<26} n={r['n']:<4} PF={r['pf']:<5.2f} WR={r['wr']:5.1f}%  "
          f"avg={r['mean_pips']:+6.2f}p  t={r['t']:+5.2f}  total={r['sum_pips']:+7.0f}p  "
          f"medstop={df.stop_p.median():.0f}p")


def run(name: str, df: pd.DataFrame) -> None:
    print(f"  {name}:")
    if len(df) == 0:
        print("    n=0")
        return
    is_t = df[df.index <= "2025-09-30 23:59:59+00:00"]
    oos_t = df[df.index > "2025-09-30 23:59:59+00:00"]
    show_df(is_t, "IS  2024-01..2025-09")
    show_df(oos_t, "OOS 2025-10..end")


def main() -> int:
    df = load(SYMBOL)
    m15 = resample(df, "15min")
    atr_d = _daily_atr(df)
    print(f"{SYMBOL}: {df.index[0].date()} → {df.index[-1].date()}  "
          f"({len(m15):,} 15m bars)")
    print("  Diagnostics (IS only):")
    sweep_diagnostics(m15)

    print("\nA sweep_reverse (Asia-range failed break):")
    for buf in (0.10, 0.25):
        for cb in (4, 8, 12):
            for tp in ("opposite", "none"):
                run(f"buf={buf} confirm={cb} tp={tp}",
                    sweep_reverse(m15, buf_frac=buf, confirm_bars=cb, tp_mode=tp))

    print("\nB prevday_sweep_fade (prev-day H/L failed break):")
    for buf in (0.10, 0.20):
        for tp in ("mid", "1r", "none"):
            run(f"buf={buf}atr tp={tp}",
                prevday_sweep_fade(m15, atr_d, buf_atr=buf, tp_mode=tp))

    print("\nC london_close_fade (fade overextended London move at 16:00):")
    for k in (0.4, 0.6, 0.8):
        run(f"k={k} stop=0.5atr",
            london_session_trade(m15, atr_d, k=k, direction=-1))

    print("\nD ny_continuation control (with the London move):")
    run("k=0.5 stop=0.5atr", london_session_trade(m15, atr_d, k=0.5, direction=1))

    print("\nE weekend_gap_fade (2x cost charged for Sunday spread):")
    m5 = df
    for mg in (3.0, 5.0, 8.0):
        for sm in ("2gap", "none"):
            t = weekend_gap_fade(m5, atr_d, min_gap_p=mg, stop_mode=sm)
            run(f"min_gap={mg}p stop={sm}", t)
            if len(t):
                print(f"      (median gap {t.gap_p.median():.0f}p)")

    print("\nGate reminder: PF_net >= 1.3 IS AND same-direction OOS n >= 30; "
          "tight-stop setups (medstop small vs ~10p 15m bars) need ~1.5+.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
