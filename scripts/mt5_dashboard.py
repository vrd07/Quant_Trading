#!/usr/bin/env python3
"""
MT5 Live Analytics Dashboard — fetches ALL data directly from MetaTrader 5.

Data source: MT5 file bridge (NOT trade_journal.csv)
Sections:
  1. Account Summary  — balance, equity, floating P&L, drawdown from HWM
  2. Open Positions   — live positions with unrealised P&L
  3. Trade History    — closed deals from MT5 with durations
  4. Scorecard        — win rate, profit factor, max drawdown per symbol
  5. Daily P&L        — day-by-day breakdown from MT5 deal history
  6. Journal Diff     — mt5 vs trade_journal.csv discrepancy check

Usage:
    python scripts/mt5_dashboard.py              # one-shot
    python scripts/mt5_dashboard.py --live       # auto-refresh every 30 s
    python scripts/mt5_dashboard.py --days 14    # last 14 days of history
    python scripts/mt5_dashboard.py --symbol XAUUSD.e  # filter by symbol
"""

import sys
import os
import signal
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── ANSI colours ─────────────────────────────────────────────────────────────
G    = "\033[92m"   # green
R    = "\033[91m"   # red
Y    = "\033[93m"   # yellow
B    = "\033[94m"   # blue / info
C    = "\033[96m"   # cyan
DIM  = "\033[2m"
BOLD = "\033[1m"
RST  = "\033[0m"

def cpnl(val: float, fmt: str = "+.2f") -> str:
    c = G if val >= 0 else R
    return f"{c}{val:{fmt}}{RST}"

def clear():
    os.system("cls" if os.name == "nt" else "clear")

# ── Graceful shutdown ─────────────────────────────────────────────────────────
_running = True

def _sig(s, f):
    global _running
    _running = False

signal.signal(signal.SIGINT,  _sig)
signal.signal(signal.SIGTERM, _sig)

# ── MT5 connection ────────────────────────────────────────────────────────────

def get_connector():
    from src.connectors.mt5_connector import get_mt5_connector
    c = get_mt5_connector()
    if not c.connect():
        print(f"\n  {R}✗ Could not connect to MT5. Is the bridge file alive?{RST}\n")
        sys.exit(1)
    return c


def fetch_all(connector, days: int, symbol_filter: str = None):
    """Fetch account info, open positions, and closed deals from MT5."""
    account  = connector.get_account_info()
    open_pos = connector.get_positions()        # dict ticket→Position
    minutes  = days * 1440
    deals_raw = connector.get_closed_positions(minutes=minutes) or []

    # Normalise deals into plain dicts
    deals = []
    for d in deals_raw:
        sym = d.get("symbol", "")
        if symbol_filter and not sym.startswith(symbol_filter.rstrip(".")):
            continue
        profit     = float(d.get("profit", 0))
        swap       = float(d.get("swap", 0))
        commission = float(d.get("commission", 0))
        net_pnl    = profit + swap + commission
        comment    = d.get("comment", "")
        strategy   = _parse_strategy(comment)
        entry_t    = d.get("entry_time") or d.get("open_time")
        close_t    = d.get("time")       or d.get("close_time")
        entry_ts   = datetime.fromtimestamp(entry_t,  tz=timezone.utc) if entry_t  else None
        close_ts   = datetime.fromtimestamp(close_t,  tz=timezone.utc) if close_t  else None
        duration_m = (close_ts - entry_ts).total_seconds() / 60 if (entry_ts and close_ts) else None
        side       = "BUY" if int(d.get("type", 1)) == 1 else "SELL"

        deals.append({
            "ticket":     d.get("ticket"),
            "pos_ticket": d.get("position_ticket"),
            "symbol":     sym,
            "strategy":   strategy,
            "side":       side,
            "volume":     float(d.get("volume", 0)),
            "entry_price":float(d.get("entry_price", d.get("price_open", 0))),
            "exit_price": float(d.get("price", d.get("price_close", 0))),
            "profit":     profit,
            "swap":       swap,
            "commission": commission,
            "net_pnl":    net_pnl,
            "comment":    comment,
            "entry_time": entry_ts,
            "close_time": close_ts,
            "duration_m": duration_m,
        })

    deals.sort(key=lambda d: d["close_time"] or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)
    return account, open_pos, deals


def _parse_strategy(comment: str) -> str:
    if not comment:
        return "unknown"

    extracted = comment.split("|")[0].strip().lower() if "|" in comment else comment.strip().lower()

    known_strategies = {
        "kalman_regime",
        "vwap_deviation",
        "momentum_scalp",
        "donchian_breakout",
        "zscore_mean_reversion",
        "mini_medallion",
    }
    if extracted in known_strategies:
        return extracted

    partial_map = [
        ("kalman", "kalman_regime"),
        ("vwap", "vwap_deviation"),
        ("momentum", "momentum_scalp"),
        ("breakout", "donchian_breakout"),
        ("mean_rev", "zscore_mean_reversion"),
        ("zscore", "zscore_mean_reversion"),
        ("medallion", "mini_medallion"),
    ]
    for keyword, canonical in partial_map:
        if keyword in extracted:
            return canonical

    return extracted if extracted else "unknown"


# ── Sections ──────────────────────────────────────────────────────────────────

def section_header(days: int, n_deals: int, symbol_filter: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sym_label = f"  filter={symbol_filter}" if symbol_filter else "  all symbols"
    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════════╗{RST}")
    print(f"{BOLD}║       MT5 LIVE ANALYTICS DASHBOARD                   ║{RST}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════╝{RST}")
    print(f"  {DIM}{now}{RST}  │  {n_deals} MT5 deals  │  last {days}d{sym_label}")
    print()


def section_account(account: dict):
    bal   = float(account.get("balance",  0))
    eq    = float(account.get("equity",   0))
    free  = float(account.get("free_margin", account.get("margin_free", 0)))
    mlvl  = float(account.get("margin_level", 0))
    float_pnl = eq - bal

    print(f"  {BOLD}ACCOUNT SUMMARY{RST}")
    print(f"  {'─'*52}")
    print(f"  {'Balance':>14}  {BOLD}${bal:>10.2f}{RST}")
    print(f"  {'Equity':>14}  {BOLD}${eq:>10.2f}{RST}", end="")
    print(f"  ({cpnl(float_pnl, '+.2f')} floating)")
    print(f"  {'Free Margin':>14}  ${free:>10.2f}")
    if mlvl > 0:
        mlvl_color = G if mlvl > 200 else (Y if mlvl > 100 else R)
        print(f"  {'Margin Level':>14}  {mlvl_color}{mlvl:>9.1f}%{RST}")
    print(f"  {'─'*52}")
    print()


def section_open_positions(open_pos: dict, symbol_filter: str = None):
    positions = list(open_pos.values()) if isinstance(open_pos, dict) else open_pos
    if symbol_filter:
        positions = [p for p in positions
                     if str(getattr(p, "symbol", {}) or {}).startswith(symbol_filter.rstrip("."))]

    print(f"  {BOLD}OPEN POSITIONS{RST}  {DIM}({len(positions)} open){RST}")
    if not positions:
        print(f"  {DIM}  No open positions.{RST}")
        print()
        return

    print(f"  {'─'*80}")
    print(f"  {'Symbol':<12} {'Side':<5} {'Lots':<6} {'Entry':>8}  "
          f"{'Current':>8}  {'Unrealised':>11}  {'Duration':>9}")
    print(f"  {'─'*80}")

    for p in positions:
        sym    = str(getattr(p, "symbol", {}).ticker if hasattr(p, "symbol") else "?")[:12]
        side   = "BUY" if getattr(p, "is_long", True) else "SELL"
        lots   = float(getattr(p, "volume", 0))
        entry  = float(getattr(p, "entry_price", 0))
        cur    = float(getattr(p, "current_price", 0) or 0)
        upnl   = float(getattr(p, "unrealized_pnl", 0))
        opened = getattr(p, "open_time", None)
        dur    = ""
        if opened:
            mins = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            dur  = f"{mins:.0f}m"

        print(f"  {sym:<12} {side:<5} {lots:<6.2f} ${entry:>8.2f}  "
              f"${cur:>8.2f}  {cpnl(upnl, '+11.2f')}  {dur:>9}")

    print(f"  {'─'*80}")
    print()


def section_trade_history(deals: list, n: int = 25):
    print(f"  {BOLD}MT5 TRADE HISTORY{RST}  {DIM}(most recent {min(n, len(deals))} of {len(deals)} deals){RST}")
    if not deals:
        print(f"  {Y}  No closed deals from MT5 for this period.{RST}\n")
        return

    print(f"  {'─'*92}")
    print(f"  {'Close Time':<19} {'Symbol':<12} {'Side':<5} {'Lots':<5} "
          f"{'Entry':>9} {'Exit':>9} {'Net P&L':>10} {'Commission':>11} {'Dur':>6} {'Strategy'}")
    print(f"  {'─'*92}")

    subset = deals[:n]
    for d in subset:
        ct    = d["close_time"].strftime("%m-%d %H:%M") if d["close_time"] else "?"
        sym   = str(d["symbol"])[:11]
        side  = d["side"][:4]
        lots  = d["volume"]
        ep    = d["entry_price"]
        xp    = d["exit_price"]
        net   = d["net_pnl"]
        comm  = d["commission"]
        dur   = f"{d['duration_m']:.0f}m" if d["duration_m"] is not None else "?"
        strat = str(d["strategy"])[:12]

        ep_str = f"${ep:>8.2f}" if ep > 0 else "       ?"

        print(f"  {ct:<19} {sym:<12} {side:<5} {lots:<5.2f} "
              f"{ep_str:10} ${xp:>8.2f} "
              f"{cpnl(net, '+10.2f')} "
              f"{cpnl(comm, '+11.2f')} "
              f"{dur:>6} {DIM}{strat}{RST}")

    print(f"  {'─'*92}")
    total_net  = sum(d["net_pnl"] for d in deals)
    total_comm = sum(d["commission"] for d in deals)
    print(f"  {'':19} {'':12} {'':5} {'':5} {'':9} {'TOTAL':>9} "
          f"{cpnl(total_net, '+10.2f')} "
          f"{cpnl(total_comm, '+11.2f')}")
    print()


def section_scorecard(deals: list):
    if not deals:
        return

    print(f"  {BOLD}SCORECARD  (per symbol){RST}")
    print(f"  {'─'*76}")
    print(f"  {'Symbol':<14} {'Deals':>6} {'Wins':>5} {'Win%':>6} "
          f"{'Gross Win':>10} {'Gross Loss':>11} {'PF':>6} {'Net P&L':>10}")
    print(f"  {'─'*76}")

    by_sym: dict = {}
    for d in deals:
        s = d["symbol"]
        by_sym.setdefault(s, []).append(d)

    all_rows = []
    for sym, ds in by_sym.items():
        n     = len(ds)
        wins  = [x for x in ds if x["net_pnl"] > 0]
        gwin  = sum(x["net_pnl"] for x in wins)
        gloss = abs(sum(x["net_pnl"] for x in ds if x["net_pnl"] < 0))
        net   = sum(x["net_pnl"] for x in ds)
        pf    = gwin / gloss if gloss > 0 else float("inf")
        all_rows.append((sym, n, len(wins), net, gwin, gloss, pf))

    all_rows.sort(key=lambda r: r[3], reverse=True)
    grand = [0, 0, 0, 0.0, 0.0, 0.0]
    for sym, n, nw, net, gw, gl, pf in all_rows:
        pf_s = f"{pf:>5.2f}" if pf != float("inf") else "  ∞  "
        wpct = nw / n * 100 if n else 0
        print(f"  {sym:<14} {n:>6} {nw:>5} {wpct:>5.1f}% "
              f"{cpnl(gw, '+10.2f')} "
              f"{R}{gl:>10.2f}{RST} "
              f"{B}{pf_s}{RST} "
              f"{cpnl(net,'+10.2f')}")
        grand[0] += n; grand[1] += nw; grand[4] += gw; grand[5] += gl; grand[3] += net

    pf_g = grand[4] / grand[5] if grand[5] > 0 else float("inf")
    pf_s = f"{pf_g:>5.2f}" if pf_g != float("inf") else "  ∞  "
    wpct_g = grand[1] / grand[0] * 100 if grand[0] else 0
    print(f"  {'─'*76}")
    print(f"  {BOLD}{'TOTAL':<14}{RST} {int(grand[0]):>6} {int(grand[1]):>5} {wpct_g:>5.1f}% "
          f"{cpnl(grand[4], '+10.2f')} "
          f"{R}{grand[5]:>10.2f}{RST} "
          f"{B}{pf_s}{RST} "
          f"{cpnl(grand[3],'+10.2f')}")
    print()


def section_daily(deals: list):
    if not deals:
        return

    by_date: dict = {}
    for d in deals:
        ct = d["close_time"]
        if ct:
            key = ct.date()
            by_date.setdefault(key, []).append(d)

    if not by_date:
        return

    print(f"  {BOLD}DAILY P&L  (MT5 source){RST}")
    print(f"  {'─'*56}")
    print(f"  {'Date':<12} {'Deals':>6} {'Wins':>5} {'Win%':>6} "
          f"{'Net P&L':>10} {'Cum P&L':>10}")
    print(f"  {'─'*56}")

    cum = 0.0
    for date in sorted(by_date):
        ds   = by_date[date]
        net  = sum(x["net_pnl"] for x in ds)
        wins = sum(1 for x in ds if x["net_pnl"] > 0)
        wpct = wins / len(ds) * 100
        cum += net
        print(f"  {str(date):<12} {len(ds):>6} {wins:>5} {wpct:>5.1f}% "
              f"{cpnl(net, '+10.2f')} "
              f"{cpnl(cum, '+10.2f')}")

    print(f"  {'─'*56}")
    print()


def section_journal_diff(deals: list):
    """Quick diff: how many deals are in MT5 vs journal CSV."""
    journal_file = PROJECT_ROOT / "data" / "logs" / "trade_journal.csv"
    if not journal_file.exists():
        return

    try:
        import pandas as pd
        df = pd.read_csv(journal_file)
        j_total = len(df)
        j_pnl   = df["realized_pnl"].sum()
    except Exception:
        return

    mt5_total = len(deals)
    mt5_pnl   = sum(d["net_pnl"] for d in deals)
    gap       = j_total - mt5_total
    pnl_diff  = j_pnl - mt5_pnl

    print(f"  {BOLD}JOURNAL vs MT5 DIFF{RST}")
    print(f"  {'─'*44}")
    print(f"  {'Source':<14} {'Trades':>8}  {'Net P&L':>10}")
    print(f"  {'─'*44}")
    print(f"  {'MT5 (live)':14} {mt5_total:>8}  {cpnl(mt5_pnl, '+10.2f')}")
    print(f"  {'Journal CSV':14} {j_total:>8}  {cpnl(j_pnl,  '+10.2f')}")
    print(f"  {'─'*44}")

    gap_c = Y if gap != 0 else G
    pnl_c = Y if abs(pnl_diff) > 0.01 else G
    print(f"  {'Gap':14} {gap_c}{gap:>+8}{RST}  {pnl_c}{pnl_diff:>+10.2f}{RST}")
    if gap > 0:
        print(f"  {DIM}  {gap} journal entries have no MT5 deal "
              f"(paper/wrong account){RST}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def run_report(days: int, show_n: int, symbol_filter: str):
    connector = get_connector()
    try:
        account, open_pos, deals = fetch_all(connector, days, symbol_filter)
    finally:
        connector.disconnect()

    section_header(days, len(deals), symbol_filter)
    section_account(account)
    section_open_positions(open_pos, symbol_filter)
    section_trade_history(deals, n=show_n)
    section_scorecard(deals)
    section_daily(deals)
    section_journal_diff(deals)


def main():
    parser = argparse.ArgumentParser(description="MT5 Live Analytics Dashboard")
    parser.add_argument("--live",     action="store_true",
                        help="Auto-refresh every N seconds")
    parser.add_argument("--days",     type=int, default=30,
                        help="History lookback in days (default: 30)")
    parser.add_argument("--trades",   type=int, default=25,
                        help="Max trades to show in history table (default: 25)")
    parser.add_argument("--symbol",   type=str, default=None,
                        help="Filter by symbol prefix, e.g. XAUUSD")
    parser.add_argument("--interval", type=int, default=30,
                        help="Live refresh interval in seconds (default: 30)")
    args = parser.parse_args()

    if args.live:
        while _running:
            clear()
            run_report(args.days, args.trades, args.symbol)
            print(f"  {DIM}Refreshing in {args.interval}s  │  Ctrl+C to exit{RST}")
            for _ in range(args.interval * 2):
                if not _running:
                    break
                time.sleep(0.5)
    else:
        run_report(args.days, args.trades, args.symbol)


if __name__ == "__main__":
    main()
