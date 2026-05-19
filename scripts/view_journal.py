#!/usr/bin/env python3
"""
Trade Analytics CLI — Run alongside or after the trading system.

Usage:
    python scripts/view_journal.py                     # one-shot report (30d)
    python scripts/view_journal.py --live              # auto-refresh every 30s
    python scripts/view_journal.py --days 7            # look back 7 days
    python scripts/view_journal.py --symbol XAUUSDm    # filter by symbol
    python scripts/view_journal.py --strategy breakout # filter by strategy

    python scripts/view_journal.py --weekly            # Saturday week review (Mon–Sat)
    python scripts/view_journal.py --weekly --week-offset 1  # previous week

Daily sections:
  1. Executive summary (win rate, PF, expectancy, max DD, streaks, R)
  2. Strategy scorecard
  3. Exit-reason + Regime mix
  4. R-multiple distribution
  5. Daily P&L with cumulative equity

Weekly sections (--weekly):
  1. Week header + Week-over-week comparison
  2. Executive summary (this week)
  3. Day-of-week breakdown (Mon–Sat)
  4. Strategy scorecard
  5. Exit reason + regime mix
  6. R-multiple distribution
  7. Best / worst 3 trades
  8. Auto takeaways + improvement suggestions
"""

import sys
import os
import signal
import time
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
import argparse

# ── setup paths ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_DIR = PROJECT_ROOT / "data" / "logs"
JOURNAL_GLOB = "trade_journal*.csv"  # legacy + namespaced (per-config) journals

# ── graceful shutdown ────────────────────────────────────────────────
_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── colors ───────────────────────────────────────────────────────────

G = "\033[92m"      # green
R = "\033[91m"      # red
Y = "\033[93m"      # yellow
B = "\033[94m"      # blue
C = "\033[96m"      # cyan
M = "\033[95m"      # magenta
DIM = "\033[2m"     # dim
BOLD = "\033[1m"    # bold
RST = "\033[0m"     # reset


# ── helpers ──────────────────────────────────────────────────────────

def color_pnl(val: float, fmt: str = "+.2f") -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return f"{DIM}{'n/a':>{fmt.split('.')[0].lstrip('+')}}{RST}" if fmt else "n/a"
    c = G if val >= 0 else R
    return f"{c}{val:{fmt}}{RST}"


def fmt_pf(pf: float) -> str:
    if pf is None or math.isinf(pf):
        return f"{G}   ∞ {RST}"
    if math.isnan(pf):
        return f"{DIM}  n/a{RST}"
    c = G if pf >= 1.0 else R
    return f"{c}{pf:>5.2f}{RST}"


def fmt_duration(seconds: float) -> str:
    if seconds is None or math.isnan(seconds) or seconds <= 0:
        return "  —  "
    m = seconds / 60
    if m < 60:
        return f"{m:>4.1f}m"
    h = m / 60
    if h < 24:
        return f"{h:>4.1f}h"
    return f"{h/24:>4.1f}d"


def fmt_delta(curr: float, prev: float, fmt: str = "+.2f", as_pct: bool = False) -> str:
    """Format a week-over-week delta with color."""
    if curr is None or prev is None:
        return f"{DIM}n/a{RST}"
    if isinstance(curr, float) and math.isnan(curr):
        return f"{DIM}n/a{RST}"
    if isinstance(prev, float) and math.isnan(prev):
        return f"{DIM}n/a{RST}"
    delta = curr - prev
    c = G if delta >= 0 else R
    if as_pct and prev != 0:
        pct = delta / abs(prev) * 100
        return f"{c}({delta:{fmt}}, {pct:+.0f}%){RST}"
    return f"{c}({delta:{fmt}}){RST}"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


# ── data loading ─────────────────────────────────────────────────────

def _discover_journals(journal_filter: str = None):
    """Return all trade_journal*.csv files, optionally filtered by substring."""
    if not JOURNAL_DIR.exists():
        return []
    files = sorted(JOURNAL_DIR.glob(JOURNAL_GLOB))
    if journal_filter:
        files = [f for f in files if journal_filter.lower() in f.stem.lower()]
    return files


def _read_journal(journal_filter: str = None):
    import pandas as pd

    files = _discover_journals(journal_filter)
    if not files:
        return pd.DataFrame()

    frames = []
    for f in files:
        try:
            sub = pd.read_csv(f)
            if not sub.empty:
                sub["_source_file"] = f.name
                frames.append(sub)
        except Exception as e:
            print(f"  {Y}Warn: failed to read {f.name}: {e}{RST}")

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Dedupe by trade_id (legacy file may overlap with namespaced ones)
    if "trade_id" in df.columns:
        df = df.drop_duplicates(subset=["trade_id"], keep="last")

    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")

    if df["exit_time"].dt.tz is None:
        df["exit_time"] = df["exit_time"].dt.tz_localize("UTC")
    if df["entry_time"].dt.tz is None:
        df["entry_time"] = df["entry_time"].dt.tz_localize("UTC")

    return df


def _apply_filters(df, symbol=None, strategy=None):
    if symbol:
        df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if strategy:
        df = df[df["strategy"].astype(str).str.lower() == strategy.lower()]
    df = df.sort_values("exit_time", ascending=False)
    return df


def load_trades(days: int = 30, symbol: str = None, strategy: str = None,
                journal: str = None):
    """Load trade journal, filter by recency + optional symbol/strategy."""
    df = _read_journal(journal)
    if df.empty:
        return df
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df[df["exit_time"] >= cutoff].copy()
    return _apply_filters(df, symbol, strategy)


def week_bounds(offset: int = 0):
    """Return (start_utc, end_utc) for Mon 00:00 → Sat 23:59:59 of the given week.

    offset=0 → current week, offset=1 → previous week, etc.
    """
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())  # Mon=0..Sun=6
    monday = monday - timedelta(weeks=offset)
    start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=5, hours=23, minutes=59, seconds=59)
    return start, end


def load_trades_window(start, end, symbol: str = None, strategy: str = None,
                       journal: str = None):
    """Load trades whose exit_time falls in [start, end]."""
    df = _read_journal(journal)
    if df.empty:
        return df
    df = df[(df["exit_time"] >= start) & (df["exit_time"] <= end)].copy()
    return _apply_filters(df, symbol, strategy)


# ── metrics ──────────────────────────────────────────────────────────

def compute_metrics(df):
    """Return dict of portfolio-level metrics."""
    n = len(df)
    if n == 0:
        return {}

    pnl = df["realized_pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    avg_win = wins.mean() if len(wins) else 0.0
    avg_loss = losses.mean() if len(losses) else 0.0
    win_rate = len(wins) / n * 100
    expectancy = pnl.mean()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown from chronological equity curve
    eq = pnl.sort_index(ascending=False).iloc[::-1].cumsum()
    running_max = eq.cummax()
    drawdown = eq - running_max
    max_dd = drawdown.min() if len(drawdown) else 0.0

    # Streaks (chronological)
    chrono = df.sort_values("exit_time")["realized_pnl"].astype(float).values
    max_win_streak = max_loss_streak = cur_w = cur_l = 0
    for v in chrono:
        if v > 0:
            cur_w += 1
            cur_l = 0
            max_win_streak = max(max_win_streak, cur_w)
        elif v < 0:
            cur_l += 1
            cur_w = 0
            max_loss_streak = max(max_loss_streak, cur_l)
        else:
            cur_w = cur_l = 0

    # R-multiples
    df_r = df.copy()
    df_r["initial_risk"] = df_r["initial_risk"].astype(float)
    df_r = df_r[df_r["initial_risk"] > 0]
    r_multiples = (df_r["realized_pnl"].astype(float) / df_r["initial_risk"]) if len(df_r) else None
    avg_r = r_multiples.mean() if r_multiples is not None and len(r_multiples) else float("nan")
    sum_r = r_multiples.sum() if r_multiples is not None and len(r_multiples) else float("nan")

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "net_pnl": pnl.sum(),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "largest_win": wins.max() if len(wins) else 0.0,
        "largest_loss": losses.min() if len(losses) else 0.0,
        "expectancy": expectancy,
        "pf": pf,
        "max_dd": max_dd,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_duration_sec": df["duration_seconds"].astype(float).mean(),
        "avg_r": avg_r,
        "sum_r": sum_r,
        "r_count": len(r_multiples) if r_multiples is not None else 0,
    }


def _print_journal_sources(journal_filter: str = None):
    """Show which journal files were merged so the source is never ambiguous."""
    files = _discover_journals(journal_filter)
    if not files:
        return
    names = ", ".join(f.name for f in files)
    tag = f"journal={journal_filter}" if journal_filter else "all journals"
    print(f"  {DIM}Sources ({tag}): {names}{RST}")
    print()


# ── section printers (shared) ────────────────────────────────────────

def print_header(days: int, total: int, filters: dict):
    bar = "═" * 64
    print()
    print(f"{BOLD}╔{bar}╗{RST}")
    print(f"{BOLD}║{'  TRADE ANALYTICS REPORT':<{len(bar)}}║{RST}")
    print(f"{BOLD}╚{bar}╝{RST}")
    tag_parts = [f"last {days}d", f"{total} trades"]
    if filters.get("symbol"):
        tag_parts.append(f"symbol={filters['symbol']}")
    if filters.get("strategy"):
        tag_parts.append(f"strategy={filters['strategy']}")
    print(f"  {DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RST}  │  " +
          f"  │  ".join(tag_parts))
    print()


def print_exec_summary(m: dict):
    """Headline KPIs in three rows."""
    if not m:
        print(f"  {Y}No closed trades found.{RST}\n")
        return

    print(f"  {BOLD}EXECUTIVE SUMMARY{RST}")
    print("  " + "─" * 88)
    print(
        f"  Net P&L   {color_pnl(m['net_pnl'], '+10.2f'):>10}  │  "
        f"Win Rate {m['win_rate']:>5.1f}% ({m['wins']}W / {m['losses']}L)  │  "
        f"Profit Factor {fmt_pf(m['pf'])}  │  "
        f"Expectancy {color_pnl(m['expectancy'], '+7.2f')}"
    )
    print(
        f"  Max DD    {color_pnl(m['max_dd'], '+10.2f'):>10}  │  "
        f"Streaks   W:{G}{m['max_win_streak']:>2}{RST} / L:{R}{m['max_loss_streak']:>2}{RST}             │  "
        f"Avg Hold      {fmt_duration(m['avg_duration_sec']):>6}  │  "
        f"Trades {m['trades']:>4}"
    )
    r_str = f"{m['avg_r']:+.2f}R" if not math.isnan(m['avg_r']) else "n/a"
    sum_r_str = f"{m['sum_r']:+.2f}R" if not math.isnan(m['sum_r']) else "n/a"
    print(
        f"  Avg Win   {color_pnl(m['avg_win'], '+10.2f'):>10}  │  "
        f"Avg Loss {color_pnl(m['avg_loss'], '+8.2f')}             │  "
        f"Best/Worst {color_pnl(m['largest_win'], '+7.2f')}/{color_pnl(m['largest_loss'], '+7.2f')}  │  "
        f"Avg R {r_str}  Σ {sum_r_str} ({m['r_count']})"
    )
    print("  " + "─" * 88)
    print()


def print_strategy_scorecard(df):
    """Per-strategy performance."""
    if df.empty:
        return

    total = len(df)
    print(f"  {BOLD}STRATEGY SCORECARD{RST}")
    print("  " + "─" * 92)
    print(f"  {'Strategy':<16} {'Trd':>4} {'Use%':>5} {'Win%':>5} "
          f"{'Net':>9} {'Avg':>8} {'Exp':>8} {'PF':>6} {'AvgHold':>8}")
    print("  " + "─" * 92)

    rows = []
    for strat, grp in df.groupby("strategy"):
        cnt = len(grp)
        pnl = grp["realized_pnl"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_win = wins.sum()
        gross_loss = abs(losses.sum())
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        avg_hold = grp["duration_seconds"].astype(float).mean()

        rows.append({
            "strategy": strat,
            "count": cnt,
            "usage": cnt / total * 100,
            "win_rate": (pnl > 0).sum() / cnt * 100,
            "net": pnl.sum(),
            "avg": pnl.mean(),
            "expectancy": pnl.mean(),
            "pf": pf,
            "hold": avg_hold,
        })

    rows.sort(key=lambda r: r["net"], reverse=True)

    for r in rows:
        print(
            f"  {str(r['strategy'])[:15]:<16} {r['count']:>4} {r['usage']:>4.1f}% "
            f"{r['win_rate']:>4.1f}% "
            f"{color_pnl(r['net'], '+9.2f')} "
            f"{color_pnl(r['avg'], '+8.2f')} "
            f"{color_pnl(r['expectancy'], '+8.2f')} "
            f"{fmt_pf(r['pf'])} "
            f"{fmt_duration(r['hold']):>8}"
        )
    print("  " + "─" * 92)
    print()


def print_exit_regime(df):
    """Exit reason + regime distribution, side-by-side."""
    if df.empty:
        return

    exit_rows = []
    for reason, grp in df.groupby("exit_reason"):
        pnl = grp["realized_pnl"].astype(float)
        exit_rows.append((str(reason)[:18], len(grp), pnl.sum(), pnl.mean(),
                          (pnl > 0).sum() / len(grp) * 100))
    exit_rows.sort(key=lambda r: r[2], reverse=True)

    regime_rows = []
    if "regime" in df.columns:
        for regime, grp in df.groupby("regime"):
            pnl = grp["realized_pnl"].astype(float)
            regime_rows.append((str(regime)[:12], len(grp), pnl.sum(), pnl.mean(),
                                (pnl > 0).sum() / len(grp) * 100))
        regime_rows.sort(key=lambda r: r[2], reverse=True)

    print(f"  {BOLD}EXIT REASONS{' ' * 30}REGIMES{RST}")
    print("  " + "─" * 40 + "  " + "─" * 40)
    header_l = f"  {'Reason':<18} {'Trd':>4} {'Net':>9} {'W%':>5}"
    header_r = f"  {'Regime':<12} {'Trd':>4} {'Net':>9} {'Avg':>8}"
    print(f"{header_l:<42}{header_r}")
    print("  " + "─" * 40 + "  " + "─" * 40)

    rows = max(len(exit_rows), len(regime_rows))
    for i in range(rows):
        left = "  " + " " * 38
        right = "  " + " " * 38
        if i < len(exit_rows):
            n, cnt, net, avg, wpct = exit_rows[i]
            left = (f"  {n:<18} {cnt:>4} "
                    f"{color_pnl(net, '+9.2f')} {wpct:>4.1f}%")
        if i < len(regime_rows):
            n, cnt, net, avg, wpct = regime_rows[i]
            right = (f"  {n:<12} {cnt:>4} "
                     f"{color_pnl(net, '+9.2f')} {color_pnl(avg, '+8.2f')}")
        print(f"{left:<50}{right}")
    print("  " + "─" * 40 + "  " + "─" * 40)
    print()


def print_r_analysis(df):
    """R-multiple distribution (risk-adjusted outcomes)."""
    df = df.copy()
    df["initial_risk"] = df["initial_risk"].astype(float)
    df = df[df["initial_risk"] > 0]
    if df.empty:
        return

    df["R"] = df["realized_pnl"].astype(float) / df["initial_risk"]
    r = df["R"]

    buckets = [
        ("≤ -2R", (r <= -2).sum()),
        ("-2R to -1R", ((r > -2) & (r <= -1)).sum()),
        ("-1R to 0", ((r > -1) & (r < 0)).sum()),
        ("0 to +1R", ((r >= 0) & (r < 1)).sum()),
        ("+1R to +2R", ((r >= 1) & (r < 2)).sum()),
        ("+2R to +3R", ((r >= 2) & (r < 3)).sum()),
        ("≥ +3R", (r >= 3).sum()),
    ]
    total = len(r)
    max_cnt = max((b[1] for b in buckets), default=1) or 1

    print(f"  {BOLD}R-MULTIPLE DISTRIBUTION{RST}  "
          f"{DIM}({total} trades with initial_risk set){RST}")
    print("  " + "─" * 60)
    for label, cnt in buckets:
        pct = cnt / total * 100
        bar_len = int(cnt / max_cnt * 30)
        color = R if "≤" in label or "-" in label else G
        bar = f"{color}{'█' * bar_len}{RST}"
        print(f"  {label:<14} {cnt:>4} ({pct:>4.1f}%)  {bar}")
    print("  " + "─" * 60)
    print(f"  {DIM}Avg R = {r.mean():+.2f}   Median R = {r.median():+.2f}   "
          f"Best = {r.max():+.2f}   Worst = {r.min():+.2f}{RST}")
    print()


def print_daily_summary(df):
    """Daily P&L breakdown with cumulative equity."""
    if df.empty:
        return

    df = df.copy()
    df["date"] = df["exit_time"].dt.date
    print(f"  {BOLD}DAILY P&L{RST}")
    print("  " + "─" * 68)
    print(f"  {'Date':<12} {'Trd':>4} {'Win%':>5} {'Net':>10} "
          f"{'Cum':>10} {'Best':>9} {'Worst':>9}")
    print("  " + "─" * 68)

    daily = df.groupby("date").agg(
        trades=("realized_pnl", "count"),
        wins=("realized_pnl", lambda x: (x > 0).sum()),
        net=("realized_pnl", "sum"),
        best=("realized_pnl", "max"),
        worst=("realized_pnl", "min"),
    ).sort_index()

    cum = 0
    for d, row in daily.iterrows():
        cum += row["net"]
        wpct = row["wins"] / row["trades"] * 100 if row["trades"] else 0
        print(f"  {str(d):<12} {int(row['trades']):>4} {wpct:>4.1f}% "
              f"{color_pnl(row['net'], '+10.2f')} "
              f"{color_pnl(cum, '+10.2f')} "
              f"{color_pnl(row['best'], '+9.2f')} "
              f"{color_pnl(row['worst'], '+9.2f')}")

    print("  " + "─" * 68)
    print()


# ── weekly-only printers ─────────────────────────────────────────────

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def print_week_header(start, end, total, filters: dict, offset: int):
    bar = "═" * 72
    label = "THIS WEEK" if offset == 0 else f"{offset} WEEK(S) AGO"
    title = f"  WEEKLY REVIEW — {label}"
    print()
    print(f"{BOLD}╔{bar}╗{RST}")
    print(f"{BOLD}║{title:<{len(bar)}}║{RST}")
    print(f"{BOLD}╚{bar}╝{RST}")

    tag_parts = [
        f"{start.strftime('%a %b %d')} → {end.strftime('%a %b %d, %Y')}",
        f"{total} trades",
    ]
    if filters.get("symbol"):
        tag_parts.append(f"symbol={filters['symbol']}")
    if filters.get("strategy"):
        tag_parts.append(f"strategy={filters['strategy']}")
    print(f"  {DIM}{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RST}  │  " +
          f"  │  ".join(tag_parts))
    print()


def print_wow_compare(curr: dict, prev: dict):
    """Side-by-side week-over-week comparison."""
    if not curr:
        return

    print(f"  {BOLD}WEEK-OVER-WEEK{RST}")
    print("  " + "─" * 76)
    print(f"  {'Metric':<18} {'This Week':>14} {'Prev Week':>14} {'Δ':>20}")
    print("  " + "─" * 76)

    def row(name, curr_val, prev_val, fmt="+10.2f", suffix=""):
        c_str = f"{curr_val:{fmt}}{suffix}" if curr_val is not None else "n/a"
        p_str = f"{prev_val:{fmt}}{suffix}" if prev_val is not None and not (isinstance(prev_val, float) and math.isnan(prev_val)) else "n/a"
        if prev_val is None or (isinstance(prev_val, float) and math.isnan(prev_val)):
            delta_str = f"{DIM}new{RST}"
        else:
            delta_str = fmt_delta(curr_val, prev_val, fmt="+.2f", as_pct=True)
        c_colored = color_pnl(curr_val, fmt) + suffix if isinstance(curr_val, (int, float)) and "+" in fmt else c_str
        print(f"  {name:<18} {c_str:>14} {p_str:>14} {delta_str:>30}")

    prev = prev or {}
    print(f"  {'Net P&L':<18} "
          f"{color_pnl(curr.get('net_pnl', 0), '+12.2f'):>22} "
          f"{color_pnl(prev.get('net_pnl', 0), '+12.2f'):>22} "
          f"{fmt_delta(curr.get('net_pnl'), prev.get('net_pnl'), '+.2f', as_pct=True):>20}")
    print(f"  {'Trades':<18} "
          f"{curr.get('trades', 0):>14} "
          f"{prev.get('trades', 0):>14} "
          f"{fmt_delta(curr.get('trades'), prev.get('trades'), '+.0f'):>30}")
    print(f"  {'Win Rate':<18} "
          f"{curr.get('win_rate', 0):>13.1f}% "
          f"{prev.get('win_rate', 0):>13.1f}% "
          f"{fmt_delta(curr.get('win_rate'), prev.get('win_rate'), '+.1f'):>30}")

    # PF
    c_pf = curr.get('pf', float('nan'))
    p_pf = prev.get('pf', float('nan'))
    c_pf_s = "∞" if math.isinf(c_pf) else (f"{c_pf:.2f}" if not math.isnan(c_pf) else "n/a")
    p_pf_s = "∞" if math.isinf(p_pf) else (f"{p_pf:.2f}" if not math.isnan(p_pf) else "n/a")
    print(f"  {'Profit Factor':<18} {c_pf_s:>14} {p_pf_s:>14} "
          f"{fmt_delta(c_pf if not math.isinf(c_pf) else None, p_pf if not math.isinf(p_pf) else None, '+.2f'):>30}")

    # Avg R
    c_r = curr.get('avg_r', float('nan'))
    p_r = prev.get('avg_r', float('nan'))
    c_r_s = f"{c_r:+.2f}R" if not math.isnan(c_r) else "n/a"
    p_r_s = f"{p_r:+.2f}R" if not math.isnan(p_r) else "n/a"
    print(f"  {'Avg R':<18} {c_r_s:>14} {p_r_s:>14} "
          f"{fmt_delta(c_r, p_r, '+.2f'):>30}")

    # Sum R
    c_sr = curr.get('sum_r', float('nan'))
    p_sr = prev.get('sum_r', float('nan'))
    c_sr_s = f"{c_sr:+.2f}R" if not math.isnan(c_sr) else "n/a"
    p_sr_s = f"{p_sr:+.2f}R" if not math.isnan(p_sr) else "n/a"
    print(f"  {'Σ R':<18} {c_sr_s:>14} {p_sr_s:>14} "
          f"{fmt_delta(c_sr, p_sr, '+.2f'):>30}")

    print(f"  {'Max DD':<18} "
          f"{color_pnl(curr.get('max_dd', 0), '+12.2f'):>22} "
          f"{color_pnl(prev.get('max_dd', 0), '+12.2f'):>22} "
          f"{fmt_delta(curr.get('max_dd'), prev.get('max_dd'), '+.2f'):>20}")
    print("  " + "─" * 76)
    print()


def print_day_of_week(df, week_start):
    """Day-of-week breakdown Mon–Sat with calendar dates."""
    if df.empty:
        return

    df = df.copy()
    df["date"] = df["exit_time"].dt.date

    print(f"  {BOLD}DAY-OF-WEEK BREAKDOWN{RST}")
    print("  " + "─" * 76)
    print(f"  {'Day':<5} {'Date':<12} {'Trd':>4} {'Win%':>5} {'Net':>10} "
          f"{'Cum':>10} {'Best':>9} {'Worst':>9}")
    print("  " + "─" * 76)

    daily = df.groupby("date").agg(
        trades=("realized_pnl", "count"),
        wins=("realized_pnl", lambda x: (x > 0).sum()),
        net=("realized_pnl", "sum"),
        best=("realized_pnl", "max"),
        worst=("realized_pnl", "min"),
    )

    cum = 0.0
    for i in range(6):  # Mon..Sat
        d = (week_start + timedelta(days=i)).date()
        if d in daily.index:
            row = daily.loc[d]
            cum += row["net"]
            wpct = row["wins"] / row["trades"] * 100 if row["trades"] else 0
            print(f"  {DAY_NAMES[i]:<5} {str(d):<12} "
                  f"{int(row['trades']):>4} {wpct:>4.1f}% "
                  f"{color_pnl(row['net'], '+10.2f')} "
                  f"{color_pnl(cum, '+10.2f')} "
                  f"{color_pnl(row['best'], '+9.2f')} "
                  f"{color_pnl(row['worst'], '+9.2f')}")
        else:
            print(f"  {DIM}{DAY_NAMES[i]:<5} {str(d):<12} {'—':>4} {'—':>5} "
                  f"{'—':>10} {'—':>10} {'—':>9} {'—':>9}{RST}")
    print("  " + "─" * 76)
    print()


def print_best_worst_trades(df, n: int = 3):
    """Show top-N winners and losers of the period."""
    if df.empty:
        return

    sorted_df = df.copy()
    sorted_df["realized_pnl"] = sorted_df["realized_pnl"].astype(float)

    winners = sorted_df.sort_values("realized_pnl", ascending=False).head(n)
    losers = sorted_df.sort_values("realized_pnl", ascending=True).head(n)
    losers = losers[losers["realized_pnl"] < 0]

    print(f"  {BOLD}BEST / WORST TRADES{RST}")
    print("  " + "─" * 100)
    print(f"  {'When':<12} {'Sym':<8} {'Strategy':<14} {'Side':<5} "
          f"{'P&L':>9} {'R':>7} {'Regime':<10} {'Reason':<14}")
    print("  " + "─" * 100)

    def _print_row(t, tag, tag_color):
        pnl = float(t.get("realized_pnl", 0) or 0)
        risk = t.get("initial_risk")
        try:
            r_mult = pnl / float(risk) if risk and float(risk) > 0 else None
        except (TypeError, ValueError):
            r_mult = None
        r_str = f"{r_mult:+5.2f}R" if r_mult is not None else "   —"
        when = (t["exit_time"].strftime("%a %H:%M")
                if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:12])
        sym = str(t.get("symbol", "?"))[:7]
        strat = str(t.get("strategy", "?"))[:13]
        side = str(t.get("side", "?"))[:4]
        regime = str(t.get("regime", ""))[:9]
        reason = str(t.get("exit_reason", "?"))[:13]
        side_c = G if side.upper().startswith("L") else R
        print(f"  {tag_color}{tag}{RST}{when:<10} {sym:<8} {strat:<14} "
              f"{side_c}{side:<5}{RST}"
              f"{color_pnl(pnl, '+9.2f')} {r_str:>7} "
              f"{regime:<10} {reason:<14}")

    if not winners.empty:
        for _, t in winners.iterrows():
            _print_row(t, "▲ ", G)
    if not losers.empty:
        for _, t in losers.iterrows():
            _print_row(t, "▼ ", R)
    print("  " + "─" * 100)
    print()


def generate_takeaways(df, m: dict, prev_m: dict):
    """Produce auto narrative — observations + improvement suggestions."""
    notes = []  # (icon, color, text)

    if not m:
        return [("ℹ", DIM, "No trades closed this week.")]

    # 1) Outcome headline
    if m["net_pnl"] > 0:
        notes.append(("✓", G, f"Profitable week: {m['net_pnl']:+.2f} across {m['trades']} trades."))
    elif m["net_pnl"] < 0:
        notes.append(("✗", R, f"Losing week: {m['net_pnl']:+.2f} across {m['trades']} trades."))
    else:
        notes.append(("•", Y, f"Flat week across {m['trades']} trades."))

    # 2) Win rate / PF sanity
    if m["win_rate"] < 35 and not math.isinf(m["pf"]) and m["pf"] < 1.0:
        notes.append(("⚠", R, f"Low win rate ({m['win_rate']:.0f}%) AND PF<1 ({m['pf']:.2f}) — entries are not edge."))
    elif m["win_rate"] >= 55 and (math.isinf(m["pf"]) or m["pf"] >= 1.5):
        notes.append(("✓", G, f"Strong win rate ({m['win_rate']:.0f}%) and PF {m['pf']:.2f}."))

    # 3) Avg win vs avg loss
    if m["avg_loss"] != 0:
        ratio = abs(m["avg_win"] / m["avg_loss"]) if m["avg_loss"] else float("inf")
        if ratio < 1.0 and m["win_rate"] < 60:
            notes.append(("⚠", R, f"Avg win ({m['avg_win']:+.2f}) < avg loss ({m['avg_loss']:+.2f}) "
                                  f"— losses are bigger than winners. Tighten SL or let winners run."))

    # 4) Loss streaks
    if m["max_loss_streak"] >= 4:
        notes.append(("⚠", R, f"{m['max_loss_streak']} consecutive losses at one point — "
                              f"check whether the circuit breaker engaged or should have."))

    # 5) Drawdown vs net
    if m["net_pnl"] > 0 and abs(m["max_dd"]) > m["net_pnl"] * 1.5:
        notes.append(("⚠", Y, f"Peak DD ({m['max_dd']:+.2f}) > 1.5× net P&L — equity curve was rough."))

    # 6) Outlier reliance
    if m["wins"] >= 3 and m["largest_win"] > 0:
        non_outlier_sum = m["gross_win"] - m["largest_win"]
        if non_outlier_sum < abs(m["gross_loss"]):
            notes.append(("⚠", Y, f"Best trade ({m['largest_win']:+.2f}) carried the week. "
                                  f"Strip it and the week is red — fragile result."))

    # 7) R analysis
    if not math.isnan(m["avg_r"]):
        if m["avg_r"] < 0:
            notes.append(("⚠", R, f"Avg R = {m['avg_r']:+.2f} — risk-adjusted, you are paying to trade."))
        elif m["avg_r"] >= 0.3:
            notes.append(("✓", G, f"Avg R = {m['avg_r']:+.2f} — risk-adjusted return is healthy."))

    # 8) Strategy red/green flags
    strat_rows = []
    for strat, grp in df.groupby("strategy"):
        pnl = grp["realized_pnl"].astype(float)
        strat_rows.append((str(strat), len(grp), pnl.sum(),
                           pnl.mean(), (pnl > 0).sum() / len(grp) * 100))
    strat_rows.sort(key=lambda x: x[2])
    for name, cnt, net, avg, wpct in strat_rows[:2]:
        if net < 0 and cnt >= 3:
            notes.append(("⚠", R, f"Strategy '{name}' is bleeding: {net:+.2f} over {cnt} trades "
                                  f"(WR {wpct:.0f}%) — consider disabling or retuning."))
    for name, cnt, net, avg, wpct in strat_rows[-2:]:
        if net > 0 and cnt >= 3:
            notes.append(("✓", G, f"Strategy '{name}' carried: {net:+.2f} over {cnt} trades (WR {wpct:.0f}%)."))

    # 9) Day-of-week pattern
    if "exit_time" in df.columns and len(df) >= 4:
        dow = df.copy()
        dow["dow"] = dow["exit_time"].dt.weekday
        dow_pnl = dow.groupby("dow")["realized_pnl"].sum()
        red_days = [DAY_NAMES[d] for d in dow_pnl.index if dow_pnl[d] < 0]
        green_days = [DAY_NAMES[d] for d in dow_pnl.index if dow_pnl[d] > 0]
        if len(red_days) >= 3:
            notes.append(("⚠", Y, f"Losing days: {', '.join(red_days)} — look for time-of-week bias."))
        if len(green_days) >= 1 and len(red_days) == 0:
            notes.append(("✓", G, f"All trading days green: {', '.join(green_days)}."))

    # 10) Regime read
    if "regime" in df.columns:
        for regime, grp in df.groupby("regime"):
            pnl = grp["realized_pnl"].astype(float)
            net = pnl.sum()
            if len(grp) >= 3 and net < 0:
                notes.append(("⚠", Y, f"Regime '{regime}' was unprofitable: {net:+.2f} over "
                                      f"{len(grp)} trades — review regime gating."))

    # 11) Exit reasons
    if "exit_reason" in df.columns:
        exit_grp = df.groupby("exit_reason")["realized_pnl"].agg(["count", "sum"])
        sl_row = exit_grp[exit_grp.index.astype(str).str.lower().str.contains("sl|stop")]
        if not sl_row.empty:
            sl_cnt = sl_row["count"].sum()
            if sl_cnt / m["trades"] > 0.5:
                notes.append(("⚠", Y, f"{int(sl_cnt)}/{m['trades']} trades hit stop-loss — "
                                      f"either entries are mistimed or SL is too tight."))

    # 12) Week-over-week change
    if prev_m:
        if prev_m.get("net_pnl", 0) > 0 and m["net_pnl"] < 0:
            notes.append(("⚠", R, f"Regression: last week {prev_m['net_pnl']:+.2f}, "
                                  f"this week {m['net_pnl']:+.2f}. What changed?"))
        elif prev_m.get("net_pnl", 0) < 0 and m["net_pnl"] > 0:
            notes.append(("✓", G, f"Recovery: last week {prev_m['net_pnl']:+.2f}, "
                                  f"this week {m['net_pnl']:+.2f}. Keep doing what's working."))

    return notes


def print_takeaways(notes):
    if not notes:
        return
    print(f"  {BOLD}TAKEAWAYS & SUGGESTIONS{RST}")
    print("  " + "─" * 88)
    for icon, color, text in notes:
        print(f"  {color}{icon}{RST} {text}")
    print("  " + "─" * 88)
    print()


# ── modes ────────────────────────────────────────────────────────────

def run_report(days: int = 30, symbol: str = None, strategy: str = None,
               journal: str = None):
    df = load_trades(days=days, symbol=symbol, strategy=strategy, journal=journal)
    filters = {"symbol": symbol, "strategy": strategy}
    print_header(days, len(df), filters)
    _print_journal_sources(journal)

    if df.empty:
        print(f"  {Y}No closed trades in the selected range.{RST}\n")
        return

    m = compute_metrics(df)
    print_exec_summary(m)
    print_strategy_scorecard(df)
    print_exit_regime(df)
    print_r_analysis(df)
    print_daily_summary(df)


def run_weekly(offset: int = 0, symbol: str = None, strategy: str = None,
               journal: str = None):
    start, end = week_bounds(offset)
    prev_start, prev_end = week_bounds(offset + 1)

    df = load_trades_window(start, end, symbol, strategy, journal)
    prev_df = load_trades_window(prev_start, prev_end, symbol, strategy, journal)

    filters = {"symbol": symbol, "strategy": strategy}
    print_week_header(start, end, len(df), filters, offset)
    _print_journal_sources(journal)

    if df.empty:
        print(f"  {Y}No closed trades in {start.strftime('%b %d')} → "
              f"{end.strftime('%b %d')}.{RST}\n")
        # still try to show prior week stats if any
        if not prev_df.empty:
            print(f"  {DIM}(Prior week had {len(prev_df)} trades.){RST}\n")
        return

    m = compute_metrics(df)
    prev_m = compute_metrics(prev_df) if not prev_df.empty else {}

    print_wow_compare(m, prev_m)
    print_exec_summary(m)
    print_day_of_week(df, start)
    print_strategy_scorecard(df)
    print_exit_regime(df)
    print_r_analysis(df)
    print_best_worst_trades(df, n=3)
    print_takeaways(generate_takeaways(df, m, prev_m))


# ── main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trade Analytics CLI")
    parser.add_argument("--live", action="store_true", help="Auto-refresh mode")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument("--interval", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument("--symbol", type=str, default=None, help="Filter by symbol (e.g. XAUUSDm)")
    parser.add_argument("--strategy", type=str, default=None, help="Filter by strategy name")
    parser.add_argument("--weekly", action="store_true",
                        help="Saturday weekly review: Mon–Sat with WoW compare + takeaways")
    parser.add_argument("--week-offset", type=int, default=0,
                        help="0 = current week, 1 = previous, etc. (only with --weekly)")
    parser.add_argument("--journal", type=str, default=None,
                        help="Substring filter on journal filename "
                             "(e.g. '1000' → only trade_journal_config_live_1000.csv). "
                             "Default: merge all trade_journal*.csv")
    args = parser.parse_args()

    if args.weekly:
        if args.live:
            while _running:
                clear()
                run_weekly(args.week_offset, args.symbol, args.strategy, args.journal)
                print(f"  {DIM}Refreshing in {args.interval}s  (Ctrl+C to exit){RST}")
                for _ in range(args.interval * 2):
                    if not _running:
                        break
                    time.sleep(0.5)
        else:
            run_weekly(args.week_offset, args.symbol, args.strategy, args.journal)
        return

    if args.live:
        while _running:
            clear()
            run_report(args.days, args.symbol, args.strategy, args.journal)
            print(f"  {DIM}Refreshing in {args.interval}s  (Ctrl+C to exit){RST}")
            for _ in range(args.interval * 2):
                if not _running:
                    break
                time.sleep(0.5)
    else:
        run_report(args.days, args.symbol, args.strategy, args.journal)


if __name__ == "__main__":
    main()
