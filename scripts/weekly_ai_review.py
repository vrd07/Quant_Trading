#!/usr/bin/env python3
"""
Weekly AI review — Saturday deep assessment (ADVISORY ONLY).

Once a week it gathers the week's evidence — bot trades (manual excluded), the
sentiment PAPER forward-test record, and the GSS history — and asks Claude
(reasoning like Ray Dalio + Jim Simons) to assess: what worked, what didn't, is
there a real edge forming, and what to change. It writes a markdown report and
can post a short summary to Telegram.

Like the nightly review, it NEVER edits configs, the risk engine, or strategy
code. Any change it proposes is for a human to apply.

Usage:
    python scripts/weekly_ai_review.py                 # current week
    python scripts/weekly_ai_review.py --week-offset 1 # last completed week
    python scripts/weekly_ai_review.py --notify        # also Telegram a summary
    python scripts/weekly_ai_review.py --dry-run       # build context, skip Claude
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "data" / "logs"
SENT_DIR = PROJECT_ROOT / "data" / "sentiment"
REPORT_DIR = PROJECT_ROOT / "docs" / "weekly"

sys.path.insert(0, str(PROJECT_ROOT))
from src.monitoring.live_monitor_emitter import _is_manual_strategy  # noqa: E402


def active_config_stem() -> str:
    try:
        return Path((PROJECT_ROOT / "config" / "ACTIVE_CONFIG")
                    .read_text().strip().splitlines()[0].strip()).stem
    except Exception:
        return "config_live_1000"


def week_bounds(offset: int = 0):
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)
    start = datetime.combine(monday, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=5, hours=23, minutes=59, seconds=59)
    return start, end


def _in_week(ts: str, start, end) -> bool:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return start <= dt <= end
    except Exception:
        return False


def _read_csv(path: Path) -> List[Dict[str, Any]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _stats(pnls: List[float]) -> Dict[str, Any]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    decided = len(wins) + len(losses)
    gl = -sum(losses)
    return {
        "n": len(pnls), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / decided * 100, 1) if decided else 0.0,
        "total": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else None,
    }


def build_context(offset: int) -> Dict[str, Any]:
    start, end = week_bounds(offset)
    stem = active_config_stem()

    # bot trades (journal), manual excluded, deduped
    journal = LOG_DIR / f"trade_journal_{stem}.csv"
    if not journal.exists():
        journal = LOG_DIR / "trade_journal.csv"
    bot_pnls: List[float] = []
    seen: set = set()
    for r in _read_csv(journal):
        if _is_manual_strategy(r.get("strategy")):
            continue
        if not _in_week(r.get("exit_time", ""), start, end):
            continue
        did = (r.get("mt5_ticket") or "").strip() or (r.get("trade_id") or "").strip()
        if did and did in seen:
            continue
        if did:
            seen.add(did)
        try:
            bot_pnls.append(float(r.get("realized_pnl") or 0))
        except Exception:
            pass

    # paper forward-test trades this week (R + $)
    paper_rows = [r for r in _read_csv(SENT_DIR / "paper_trades_XAUUSD.csv")
                  if _in_week(r.get("closed_at", ""), start, end)]
    paper_r = [float(r.get("r_multiple") or 0) for r in paper_rows]
    paper_usd = sum(float(r.get("pnl_usd") or 0) for r in paper_rows)

    # GSS history this week
    gss_rows = [r for r in _read_csv(SENT_DIR / "gss_history_XAUUSD.csv")
                if _in_week(r.get("timestamp", ""), start, end)]
    gss_vals = []
    regimes: Dict[str, int] = {}
    for r in gss_rows:
        try:
            gss_vals.append(float(r.get("gss_total") or 0))
        except Exception:
            pass
        reg = r.get("regime", "?")
        regimes[reg] = regimes.get(reg, 0) + 1

    return {
        "week": f"{start.date()} → {end.date()}",
        "config": stem,
        "bot_trades": _stats(bot_pnls),
        "paper": {
            **_stats(paper_r),
            "total_R": round(sum(paper_r), 2),
            "total_usd": round(paper_usd, 2),
            "trades": [
                {"side": r.get("side"), "R": r.get("r_multiple"),
                 "exit_reason": r.get("exit_reason"), "gss": r.get("gss_at_entry")}
                for r in paper_rows
            ],
        },
        "gss_history": {
            "samples": len(gss_vals),
            "avg": round(sum(gss_vals) / len(gss_vals), 1) if gss_vals else None,
            "min": round(min(gss_vals), 1) if gss_vals else None,
            "max": round(max(gss_vals), 1) if gss_vals else None,
            "regime_distribution": regimes,
        },
    }


PROMPT = """\
You are the WEEKLY reviewer of an automated XAUUSD trading system with an AI
sentiment layer. Assess the week like Ray Dalio (radical truth: what actually
happened, what we got wrong, which principle to update) fused with Jim Simons
(is a measurable edge forming, net of costs and given the sample size, or is
this noise?). The sentiment layer is in PAPER forward-test — not live.

You are ADVISORY ONLY. Do not tell anyone to disable the risk engine. Proposals
are for a human to apply. Be terse, concrete, markdown. Sections:
  ## Verdict (one line)
  ## Bot vs paper this week
  ## Edge forming? (Simons — sample size honesty)
  ## What to change (human-approve only) — or "none"
  ## What would have to be TRUE to go live (Dalio)

CONTEXT (JSON):
"""


def _claude_bin() -> Optional[str]:
    explicit = Path.home() / ".local" / "bin" / "claude"
    if explicit.exists():
        return str(explicit)
    return shutil.which("claude")


def run_claude(context: Dict[str, Any]) -> Optional[str]:
    claude = _claude_bin()
    if not claude:
        return None
    try:
        r = subprocess.run([claude, "-p", PROMPT + json.dumps(context, indent=2, default=str)],
                           cwd=str(PROJECT_ROOT), text=True, capture_output=True, timeout=420)
    except Exception as e:
        print(f"  [warn] claude failed: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return None
    return r.stdout.strip() or None


def fallback(context: Dict[str, Any]) -> str:
    b, p = context["bot_trades"], context["paper"]
    return (
        f"## Verdict (one line)\nBot {b['total']:+.2f} ({b['n']} trades); "
        f"paper {p['total_R']:+.2f}R (${p['total_usd']:+,.2f}, {p['n']} trades).\n\n"
        f"## Bot vs paper this week\n- bot: {json.dumps(b)}\n- paper: {json.dumps({k: p[k] for k in ('n','wins','losses','win_rate_pct','total_R','total_usd')})}\n\n"
        f"## Edge forming?\n- {p['n']} paper trades is too few to conclude — keep accumulating.\n\n"
        f"## What to change\n- none (claude CLI unavailable — manual review)\n\n"
        f"## What would have to be TRUE to go live\n- a positive paper expectancy over a meaningful sample.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly advisory AI review (Saturday).")
    ap.add_argument("--week-offset", type=int, default=0)
    ap.add_argument("--notify", action="store_true", help="Telegram a short summary.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    context = build_context(args.week_offset)
    if args.dry_run:
        print(json.dumps(context, indent=2, default=str))
        return 0

    review = run_claude(context) or fallback(context)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"ai_review_{context['week'].split(' ')[0]}.md"
    out.write_text(f"# Weekly AI Review — {context['week']} ({context['config']})\n\n" + review + "\n")
    print(f"[weekly_ai_review] wrote {out}")

    if args.notify:
        try:
            from src.sentiment.notify import notify_text
            head = review.split("\n\n")[0][:600]
            ok = notify_text(f"🗓 <b>Weekly AI Review</b> ({context['week']})\n{head}")
            print(f"[weekly_ai_review] telegram: {ok}")
        except Exception as e:
            print(f"  [warn] notify failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
