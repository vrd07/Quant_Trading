"""
Performance Dashboard — Concise P&L + Strategy Analytics.

Outputs:
  1. Account snapshot  (equity, return, open positions)
  2. Trade log         (every closed trade: strategy, side, P&L)
  3. Strategy scorecard (per-strategy: count %, win%, loss%, net P&L)

All numbers are USD or percentage — nothing else.
"""

from typing import Dict, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import pandas as pd

from ..portfolio.portfolio_engine import PortfolioEngine
from .trade_journal import TradeJournal


class PerformanceDashboard:
    """Concise trading analytics dashboard."""

    def __init__(
        self,
        portfolio: PortfolioEngine,
        journal: TradeJournal,
        initial_capital: Decimal,
        data_engine=None,
    ):
        self.portfolio = portfolio
        self.journal = journal
        self.initial_capital = initial_capital
        self.data_engine = data_engine  # kept for interface compat

        from .logger import get_logger
        self.logger = get_logger(__name__)

    # ── public API (used by main.py) ────────────────────────────────

    def print_dashboard(self) -> None:
        """Print full dashboard to stdout."""
        self._print_account_snapshot()
        self._print_trade_log()
        self._print_strategy_scorecard()

    def save_snapshot(self, output_file: str) -> None:
        """Save JSON snapshot for later analysis."""
        import json
        from pathlib import Path

        data = self._build_snapshot_dict()
        out = Path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(data, f, indent=2)
        self.logger.info(f"Dashboard snapshot saved to {output_file}")

    def print_recent_trades(self, count: int = 10) -> None:
        """Alias kept for backward compat — delegates to trade log."""
        self._print_trade_log(n=count)

    def get_recent_trades(self, count: int = 10) -> List[Dict]:
        """Return most recent trade dicts."""
        trades = self.journal.get_trades()
        trades.sort(key=lambda t: t.get("exit_time", ""), reverse=True)
        return trades[:count]

    # ── 1. Account Snapshot ─────────────────────────────────────────

    def _print_account_snapshot(self) -> None:
        stats = self.portfolio.get_statistics()
        equity = Decimal(str(stats.get("total_pnl", 0))) + self.initial_capital
        ret = equity - self.initial_capital
        ret_pct = float(ret / self.initial_capital * 100) if self.initial_capital else 0

        print()
        print("╔══════════════════════════════════════════════════╗")
        print("║           TRADING ANALYTICS DASHBOARD            ║")
        print("╚══════════════════════════════════════════════════╝")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        print("  ACCOUNT")
        print(f"  ├─ Capital      ${float(self.initial_capital):>10,.2f}")
        print(f"  ├─ Equity       ${float(equity):>10,.2f}")
        print(f"  ├─ Return       ${float(ret):>+10,.2f}  ({ret_pct:+.2f}%)")
        print(f"  ├─ Unrealized   ${stats.get('unrealized_pnl', 0):>+10,.2f}")
        print(f"  ├─ Realized     ${stats.get('realized_pnl', 0):>+10,.2f}")
        print(f"  └─ Positions    {stats['total_positions']}  "
              f"(L:{stats['long_positions']}  S:{stats['short_positions']})")

    # ── 2. Trade Log ────────────────────────────────────────────────

    def _print_trade_log(self, n: int = 15) -> None:
        trades = self.journal.get_trades()
        if not trades:
            print("\n  No closed trades yet.\n")
            return

        trades.sort(key=lambda t: t.get("exit_time", ""), reverse=True)
        recent = trades[:n]

        print()
        print("  RECENT TRADES")
        print("  " + "─" * 82)
        print(f"  {'Strategy':<16} {'Side':<5} {'Entry':>9}  {'Exit':>9}  "
              f"{'P&L ($)':>10}  {'P&L (%)':>8}  {'Duration':>8}")
        print("  " + "─" * 82)

        for t in recent:
            dur_min = t.get("duration_seconds", 0) / 60
            pnl = t.get("realized_pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            strat = t.get("strategy", "?")[:15]
            side = t.get("side", "?")[:4]
            entry = t.get("entry_price", 0)
            exit_ = t.get("exit_price", 0)

            # Color: green for win, red for loss
            color = "\033[92m" if pnl >= 0 else "\033[91m"
            reset = "\033[0m"

            print(f"  {strat:<16} {side:<5} "
                  f"${entry:>8.2f}  ${exit_:>8.2f}  "
                  f"{color}${pnl:>+9.2f}{reset}  "
                  f"{color}{pnl_pct:>+7.2f}%{reset}  "
                  f"{dur_min:>6.1f}m")

        print("  " + "─" * 82)
        print(f"  Showing {len(recent)} of {len(trades)} total trades")

    # ── 3. Strategy Scorecard ───────────────────────────────────────

    def _print_strategy_scorecard(self) -> None:
        trades = self.journal.get_trades()
        if not trades:
            return

        df = pd.DataFrame(trades)
        total = len(df)

        print()
        print("  STRATEGY SCORECARD")
        print("  " + "─" * 76)
        print(f"  {'Strategy':<16} {'Trades':>7} {'Usage%':>7} "
              f"{'Wins':>5} {'Win%':>6} {'Loss%':>6} "
              f"{'Net P&L':>10} {'Avg P&L':>9}")
        print("  " + "─" * 76)

        grouped = df.groupby("strategy")
        rows = []
        for strat, grp in grouped:
            cnt = len(grp)
            wins = (grp["realized_pnl"] > 0).sum()
            losses = (grp["realized_pnl"] < 0).sum()
            net = grp["realized_pnl"].sum()
            avg = grp["realized_pnl"].mean()
            win_pct = wins / cnt * 100 if cnt else 0
            loss_pct = losses / cnt * 100 if cnt else 0
            usage_pct = cnt / total * 100

            rows.append((strat, cnt, usage_pct, wins, win_pct, loss_pct, net, avg))

        # Sort by net P&L descending
        rows.sort(key=lambda r: r[6], reverse=True)

        for strat, cnt, usage, wins, wpct, lpct, net, avg in rows:
            color = "\033[92m" if net >= 0 else "\033[91m"
            reset = "\033[0m"

            print(f"  {str(strat)[:15]:<16} {cnt:>7} {usage:>6.1f}% "
                  f"{wins:>5} {wpct:>5.1f}% {lpct:>5.1f}% "
                  f"{color}${net:>+9.2f}{reset} "
                  f"{color}${avg:>+8.2f}{reset}")

        # Totals row
        total_pnl = df["realized_pnl"].sum()
        total_wins = (df["realized_pnl"] > 0).sum()
        total_win_pct = total_wins / total * 100 if total else 0
        total_loss_pct = 100 - total_win_pct

        print("  " + "─" * 76)
        tc = "\033[92m" if total_pnl >= 0 else "\033[91m"
        print(f"  {'TOTAL':<16} {total:>7} {'100.0':>6}% "
              f"{total_wins:>5} {total_win_pct:>5.1f}% {total_loss_pct:>5.1f}% "
              f"{tc}${total_pnl:>+9.2f}\033[0m")
        print()

    # ── helpers ──────────────────────────────────────────────────────

    def _build_snapshot_dict(self) -> Dict:
        stats = self.portfolio.get_statistics()
        equity = Decimal(str(stats.get("total_pnl", 0))) + self.initial_capital

        trades = self.journal.get_trades()
        df = pd.DataFrame(trades) if trades else pd.DataFrame()

        strategy_stats = {}
        if len(df):
            total = len(df)
            for strat, grp in df.groupby("strategy"):
                cnt = len(grp)
                wins = int((grp["realized_pnl"] > 0).sum())
                strategy_stats[strat] = {
                    "trades": cnt,
                    "usage_pct": round(cnt / total * 100, 1),
                    "win_pct": round(wins / cnt * 100, 1) if cnt else 0,
                    "loss_pct": round((cnt - wins) / cnt * 100, 1) if cnt else 0,
                    "net_pnl": round(float(grp["realized_pnl"].sum()), 2),
                    "avg_pnl": round(float(grp["realized_pnl"].mean()), 2),
                }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "initial_capital": float(self.initial_capital),
            "current_equity": float(equity),
            "total_return_pct": float((equity - self.initial_capital) / self.initial_capital * 100) if self.initial_capital else 0,
            "open_positions": stats["total_positions"],
            "total_trades": len(trades),
            "strategy_stats": strategy_stats,
        }
