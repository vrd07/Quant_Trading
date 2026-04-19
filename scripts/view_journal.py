#!/usr/bin/env python3
"""
Trade Analytics CLI — Run alongside or after the trading system.

Usage:
    python scripts/view_journal.py                     # one-shot report (30d)
    python scripts/view_journal.py --live              # auto-refresh every 30s
    python scripts/view_journal.py --days 7            # look back 7 days
    python scripts/view_journal.py --symbol XAUUSDm    # filter by symbol
    python scripts/view_journal.py --strategy breakout # filter by strategy

Sections:
  1. Executive summary (win rate, PF, expectancy, Sharpe, max DD, streaks)
  2. Strategy scorecard (usage, win/loss, PF, expectancy, avg hold)
  3. Symbol × Side breakdown
  4. Exit-reason + Regime mix
  5. Hour-of-day P&L distribution
  6. R-multiple analysis (risk-adjusted returns)
  7. Daily P&L with cumulative equity
  8. Recent trade log
"""

import sys
import os
import signal
import time
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
import argparse

# ── setup paths ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_FILE = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"

# ── graceful shutdown ────────────────────────────────────────────────
_running = True


def _handle_signal(sig, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── helpers ──────────────────────────────────────────────────────────

G = "\033[92m"      # green
R = "\033[91m"      # red
Y = "\033[93m"      # yellow
B = "\033[94m"      # blue
C = "\033[96m"      # cyan
M = "\033[95m"      # magenta
DIM = "\033[2m"     # dim
BOLD = "\033[1m"    # bold
RST = "\033[0m"     # reset


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


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def load_trades(days: int = 30, symbol: str = None, strategy: str = None):
    """Load trade journal, filter by recency + optional symbol/strategy."""
    import pandas as pd

    if not JOURNAL_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(JOURNAL_FILE)
    if df.empty:
        return df

    df["exit_time"] = pd.to_datetime(df["exit_time"], errors="coerce")
    df["entry_time"] = pd.to_datetime(df["entry_time"], errors="coerce")

    if df["exit_time"].dt.tz is None:
        df["exit_time"] = df["exit_time"].dt.tz_localize("UTC")
    if df["entry_time"].dt.tz is None:
        df["entry_time"] = df["entry_time"].dt.tz_localize("UTC")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df[df["exit_time"] >= cutoff].copy()

    if symbol:
        df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if strategy:
        df = df[df["strategy"].astype(str).str.lower() == strategy.lower()]

    df.sort_values("exit_time", ascending=False, inplace=True)
    return df


# ── metrics ──────────────────────────────────────────────────────────

def compute_metrics(df):
    """Return dict of portfolio-level metrics."""
    import numpy as np

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

    # Expectancy: avg $ earned per trade
    expectancy = pnl.mean()

    # Profit factor
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe (per-trade, annualized assuming ~252 trading days; scaled by trades/day)
    std = pnl.std(ddof=1) if n > 1 else 0
    sharpe = (pnl.mean() / std) * math.sqrt(252) if std > 0 else float("nan")

    # Max drawdown from equity curve
    eq = pnl.sort_index(ascending=False).iloc[::-1].cumsum()  # chronological cumsum
    running_max = eq.cummax()
    drawdown = eq - running_max
    max_dd = drawdown.min() if len(drawdown) else 0.0

    # Streaks (chronological order)
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

    # R-multiples where initial_risk is present
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
        "sharpe": sharpe,
        "max_dd": max_dd,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "avg_duration_sec": df["duration_seconds"].astype(float).mean(),
        "avg_r": avg_r,
        "sum_r": sum_r,
        "r_count": len(r_multiples) if r_multiples is not None else 0,
    }


# ── section printers ─────────────────────────────────────────────────

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
    """Section 1: Headline KPIs in two compact rows."""
    if not m:
        print(f"  {Y}No closed trades found.{RST}\n")
        return

    print(f"  {BOLD}EXECUTIVE SUMMARY{RST}")
    print("  " + "─" * 88)
    # Row 1: P&L, win rate, PF, expectancy
    print(
        f"  Net P&L   {color_pnl(m['net_pnl'], '+10.2f'):>10}  │  "
        f"Win Rate {m['win_rate']:>5.1f}% ({m['wins']}W / {m['losses']}L)  │  "
        f"Profit Factor {fmt_pf(m['pf'])}  │  "
        f"Expectancy {color_pnl(m['expectancy'], '+7.2f')}"
    )
    # Row 2: Sharpe, max DD, streaks, avg hold
    sharpe_str = f"{m['sharpe']:>5.2f}" if not math.isnan(m['sharpe']) else "  n/a"
    sharpe_c = G if (not math.isnan(m['sharpe']) and m['sharpe'] > 1) else (Y if not math.isnan(m['sharpe']) else DIM)
    print(
        f"  Sharpe    {sharpe_c}{sharpe_str}{RST}       │  "
        f"Max DD   {color_pnl(m['max_dd'], '+8.2f')}            │  "
        f"Streaks   W:{G}{m['max_win_streak']:>2}{RST} / L:{R}{m['max_loss_streak']:>2}{RST}         │  "
        f"Avg Hold   {fmt_duration(m['avg_duration_sec'])}"
    )
    # Row 3: win/loss averages, largest trades, R-multiples
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
    """Section 2: Per-strategy performance."""
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
            "expectancy": pnl.mean(),  # same as avg per-trade P&L
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


def print_symbol_side_matrix(df):
    """Section 3: Symbol × Side P&L matrix."""
    if df.empty:
        return

    print(f"  {BOLD}SYMBOL × SIDE BREAKDOWN{RST}")
    print("  " + "─" * 80)
    print(f"  {'Symbol':<12} {'Side':<6} {'Trd':>4} {'Win%':>5} "
          f"{'Net':>10} {'Avg':>9} {'Best':>9} {'Worst':>9} {'PF':>6}")
    print("  " + "─" * 80)

    for (sym, side), grp in df.groupby(["symbol", "side"]):
        pnl = grp["realized_pnl"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_win = wins.sum()
        gross_loss = abs(losses.sum())
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        print(
            f"  {str(sym)[:11]:<12} {str(side)[:5]:<6} {len(grp):>4} "
            f"{(pnl > 0).sum() / len(grp) * 100:>4.1f}% "
            f"{color_pnl(pnl.sum(), '+10.2f')} "
            f"{color_pnl(pnl.mean(), '+9.2f')} "
            f"{color_pnl(pnl.max(), '+9.2f')} "
            f"{color_pnl(pnl.min(), '+9.2f')} "
            f"{fmt_pf(pf)}"
        )
    print("  " + "─" * 80)
    print()


def print_exit_regime(df):
    """Section 4: Exit reason + regime distribution, side-by-side."""
    if df.empty:
        return

    # Exit reasons
    exit_rows = []
    for reason, grp in df.groupby("exit_reason"):
        pnl = grp["realized_pnl"].astype(float)
        exit_rows.append((str(reason)[:18], len(grp), pnl.sum(), pnl.mean(),
                          (pnl > 0).sum() / len(grp) * 100))
    exit_rows.sort(key=lambda r: r[2], reverse=True)

    # Regimes
    regime_rows = []
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


def print_hour_distribution(df):
    """Section 5: P&L by hour-of-day (UTC) — where you make/lose money."""
    if df.empty:
        return
    import pandas as pd

    df = df.copy()
    df["hour"] = df["exit_time"].dt.hour
    grp = df.groupby("hour").agg(
        trades=("realized_pnl", "count"),
        net=("realized_pnl", "sum"),
        wins=("realized_pnl", lambda x: (x > 0).sum()),
    )

    if grp.empty:
        return

    max_abs = max(abs(grp["net"].max()), abs(grp["net"].min()), 1)

    print(f"  {BOLD}HOURLY P&L DISTRIBUTION (UTC){RST}")
    print("  " + "─" * 72)
    print(f"  {'Hr':>3} {'Trd':>4} {'Win%':>5} {'Net':>9}   {'Bar':<40}")
    print("  " + "─" * 72)
    for h in range(24):
        if h not in grp.index:
            continue
        row = grp.loc[h]
        wpct = row["wins"] / row["trades"] * 100
        bar_len = int(abs(row["net"]) / max_abs * 20)
        if row["net"] >= 0:
            bar = f"{' ' * 20}{G}{'█' * bar_len}{RST}"
        else:
            bar = f"{' ' * (20 - bar_len)}{R}{'█' * bar_len}{RST}{' ' * 20}"
        print(f"  {h:>2}h {int(row['trades']):>4} {wpct:>4.1f}% "
              f"{color_pnl(row['net'], '+9.2f')}   {bar}")
    print("  " + "─" * 72)
    print()


def print_r_analysis(df):
    """Section 6: R-multiple distribution (risk-adjusted outcomes)."""
    import numpy as np

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
    """Section 7: Daily P&L breakdown with cumulative equity."""
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
    for date, row in daily.iterrows():
        cum += row["net"]
        wpct = row["wins"] / row["trades"] * 100 if row["trades"] else 0
        print(f"  {str(date):<12} {int(row['trades']):>4} {wpct:>4.1f}% "
              f"{color_pnl(row['net'], '+10.2f')} "
              f"{color_pnl(cum, '+10.2f')} "
              f"{color_pnl(row['best'], '+9.2f')} "
              f"{color_pnl(row['worst'], '+9.2f')}")

    print("  " + "─" * 68)
    print()


def print_trade_log(df, n: int = 15):
    """Section 8: Recent trade log."""
    if df.empty:
        return

    recent = df.head(n)

    print(f"  {BOLD}RECENT TRADES{RST}  {DIM}(latest {len(recent)}){RST}")
    print("  " + "─" * 100)
    print(f"  {'Exit Time':<16} {'Symbol':<10} {'Strat':<12} {'Side':<5} "
          f"{'Entry':>9} {'Exit':>9} {'P&L':>9} {'%':>7} {'R':>6} {'Dur':>6} {'Reason':<12}")
    print("  " + "─" * 100)

    for _, t in recent.iterrows():
        dur_s = float(t.get("duration_seconds", 0) or 0)
        pnl = float(t.get("realized_pnl", 0) or 0)
        pnl_pct = float(t.get("pnl_pct", 0) or 0)
        risk = t.get("initial_risk")
        try:
            r_mult = pnl / float(risk) if risk and float(risk) > 0 else None
        except (TypeError, ValueError):
            r_mult = None
        strat = str(t.get("strategy", "?"))[:11]
        sym = str(t.get("symbol", "?"))[:9]
        side = str(t.get("side", "?"))[:4]
        entry = float(t.get("entry_price", 0) or 0)
        exit_ = float(t.get("exit_price", 0) or 0)
        reason = str(t.get("exit_reason", "?"))[:11]
        exit_t = (t["exit_time"].strftime("%m-%d %H:%M")
                  if hasattr(t["exit_time"], "strftime") else str(t["exit_time"])[:16])

        side_c = G if side.upper().startswith("L") else R
        r_str = f"{r_mult:+5.2f}R" if r_mult is not None else "   — "

        print(
            f"  {exit_t:<16} {sym:<10} {strat:<12} {side_c}{side:<5}{RST}"
            f"{entry:>9.2f} {exit_:>9.2f} "
            f"{color_pnl(pnl, '+9.2f')} "
            f"{color_pnl(pnl_pct, '+6.2f')}% "
            f"{r_str:>6} "
            f"{fmt_duration(dur_s):>6} "
            f"{reason:<12}"
        )
    print("  " + "─" * 100)
    print()


# ── main ─────────────────────────────────────────────────────────────

def run_report(days: int = 30, symbol: str = None, strategy: str = None):
    df = load_trades(days=days, symbol=symbol, strategy=strategy)
    filters = {"symbol": symbol, "strategy": strategy}
    print_header(days, len(df), filters)

    if df.empty:
        print(f"  {Y}No closed trades in the selected range.{RST}\n")
        return

    m = compute_metrics(df)
    print_exec_summary(m)
    print_strategy_scorecard(df)
    print_symbol_side_matrix(df)
    print_exit_regime(df)
    print_hour_distribution(df)
    print_r_analysis(df)
    print_daily_summary(df)
    print_trade_log(df, n=15)


def main():
    parser = argparse.ArgumentParser(description="Trade Analytics CLI")
    parser.add_argument("--live", action="store_true", help="Auto-refresh mode")
    parser.add_argument("--days", type=int, default=30, help="Lookback days (default: 30)")
    parser.add_argument("--interval", type=int, default=30, help="Refresh interval in seconds")
    parser.add_argument("--symbol", type=str, default=None, help="Filter by symbol (e.g. XAUUSDm)")
    parser.add_argument("--strategy", type=str, default=None, help="Filter by strategy name")
    args = parser.parse_args()

    if args.live:
        while _running:
            clear()
            run_report(args.days, args.symbol, args.strategy)
            print(f"  {DIM}Refreshing in {args.interval}s  (Ctrl+C to exit){RST}")
            for _ in range(args.interval * 2):
                if not _running:
                    break
                time.sleep(0.5)
    else:
        run_report(args.days, args.symbol, args.strategy)


if __name__ == "__main__":
    main()
