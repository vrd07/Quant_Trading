#!/usr/bin/env python3
"""
Nightly AI trade review — ADVISORY ONLY.

Every night it gathers the day's BOT trades (manual MT5 clicks excluded, each
trade counted once), the active regime override, and the latest GSS (if the
sentiment engine is producing one), then asks Claude — instructed to think like
a disciplined principles-driven trader (Ray Dalio's radical-truth review +
Jim Simons' statistical rigor) — to write a short review and PROPOSE config
tweaks.

Hard guarantee: this script NEVER edits configs, the risk engine, or strategy
state. It reads, summarizes, and writes a markdown report to docs/nightly/. Any
change it proposes is a suggestion for a human to apply. The risk engine keeps
its absolute veto; an LLM does not get write access to a live money account.

Usage:
    python scripts/nightly_ai_review.py                 # today, active config
    python scripts/nightly_ai_review.py --date 2026-06-01
    python scripts/nightly_ai_review.py --dry-run       # build context, skip Claude
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "data" / "logs"
REPORT_DIR = PROJECT_ROOT / "docs" / "nightly"

sys.path.insert(0, str(PROJECT_ROOT))
from src.monitoring.live_monitor_emitter import _is_manual_strategy  # noqa: E402


# ── resolution helpers (mirror weekly_report.py conventions) ─────────────────
def active_config_stem() -> str:
    marker = PROJECT_ROOT / "config" / "ACTIVE_CONFIG"
    try:
        return Path(marker.read_text().strip().splitlines()[0].strip()).stem
    except (OSError, IndexError):
        return "config_live_1000"


def find_journal(stem: str) -> Optional[Path]:
    cand = LOG_DIR / f"trade_journal_{stem}.csv"
    if cand.exists():
        return cand
    legacy = LOG_DIR / "trade_journal.csv"
    return legacy if legacy.exists() else None


def _claude_bin() -> Optional[str]:
    explicit = Path.home() / ".local" / "bin" / "claude"
    if explicit.exists():
        return str(explicit)
    return shutil.which("claude")


# ── context assembly ─────────────────────────────────────────────────────────
def collect_day_trades(journal: Path, day: str) -> List[Dict[str, Any]]:
    """Bot trades closed on `day` (UTC, YYYY-MM-DD). Manual excluded, deduped."""
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    with open(journal, newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            if _is_manual_strategy(rec.get("strategy")):
                continue
            if not (rec.get("exit_time", "") or "").startswith(day):
                continue
            did = (rec.get("mt5_ticket") or "").strip() or (rec.get("trade_id") or "").strip()
            if did:
                if did in seen:
                    continue
                seen.add(did)
            rows.append(rec)
    return rows


def summarize(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [float(t.get("realized_pnl") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    decided = len(wins) + len(losses)
    by_strat: Dict[str, float] = {}
    for t in trades:
        by_strat[t.get("strategy", "?")] = round(
            by_strat.get(t.get("strategy", "?"), 0) + float(t.get("realized_pnl") or 0), 2)
    gross_loss = -sum(losses)
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / decided * 100, 1) if decided else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "best": round(max(pnls), 2) if pnls else 0.0,
        "worst": round(min(pnls), 2) if pnls else 0.0,
        "pnl_by_strategy": by_strat,
    }


def read_regime_override() -> Dict[str, Any]:
    p = PROJECT_ROOT / "data" / "config_override_XAUUSD.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def read_gss(symbol: str = "XAUUSD") -> Optional[Dict[str, Any]]:
    try:
        from src.sentiment.store import read_gss as _rg
        return _rg(symbol, max_age_minutes=24 * 60)
    except Exception:
        return None


def build_context(day: str) -> Dict[str, Any]:
    stem = active_config_stem()
    journal = find_journal(stem)
    trades = collect_day_trades(journal, day) if journal else []
    return {
        "date": day,
        "config": stem,
        "summary": summarize(trades),
        "trades": [
            {
                "strategy": t.get("strategy"),
                "side": t.get("side"),
                "pnl": float(t.get("realized_pnl") or 0),
                "exit_reason": t.get("exit_reason"),
                "regime": t.get("regime"),
                "signal_strength": t.get("signal_strength"),
            }
            for t in trades
        ],
        "regime_override": read_regime_override().get("generated_at", ""),
        "gss": read_gss(),
    }


PROMPT_HEADER = """\
You are the nightly reviewer for an automated XAUUSD trading bot. Review like a
disciplined principles-driven trader: Ray Dalio's radical-truth post-mortem
(what actually happened, what would have to be true for today's decisions to be
right, what principle to update) fused with Jim Simons' statistical rigor (is
there a real edge net of costs, or just noise across this few samples?).

You are ADVISORY ONLY. Do NOT instruct anyone to disable the risk engine. Any
config change you suggest is a PROPOSAL a human will review before applying.
Be terse and concrete. Output GitHub-flavored markdown with these sections:
  ## Verdict (one line)
  ## What happened today
  ## Edge check (Simons) — is this signal or noise given the sample size?
  ## Principle to update (Dalio)
  ## Proposed changes (human-approve only) — or "none"

Here is today's context as JSON:
"""


def run_claude(context: Dict[str, Any]) -> Optional[str]:
    claude = _claude_bin()
    if not claude:
        return None
    prompt = PROMPT_HEADER + json.dumps(context, indent=2, default=str)
    try:
        r = subprocess.run(
            [claude, "-p", prompt],
            cwd=str(PROJECT_ROOT), text=True, capture_output=True, timeout=300,
        )
    except Exception as e:
        print(f"  [warn] claude invocation failed: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return None
    return r.stdout.strip() or None


def fallback_report(context: Dict[str, Any]) -> str:
    """Deterministic non-AI report so the job always produces something."""
    s = context["summary"]
    pf = s["profit_factor"]
    return (
        f"## Verdict (one line)\n"
        f"{s['trades']} bot trades, {s['total_pnl']:+.2f} P&L, "
        f"win-rate {s['win_rate_pct']}%, PF {pf if pf is not None else 'n/a'}.\n\n"
        f"## What happened today\n"
        f"- P&L by strategy: {json.dumps(s['pnl_by_strategy'])}\n"
        f"- Best {s['best']:+.2f} / Worst {s['worst']:+.2f}\n\n"
        f"## Edge check (Simons)\n"
        f"- Only {s['trades']} samples today — far too few to claim an edge. "
        f"Track the rolling sample, not the single day.\n\n"
        f"## Principle to update (Dalio)\n"
        f"- (claude CLI unavailable — manual review)\n\n"
        f"## Proposed changes (human-approve only)\n- none\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly advisory AI trade review.")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true",
                    help="Build and print context only; do not call Claude.")
    args = ap.parse_args()

    context = build_context(args.date)
    if args.dry_run:
        print(json.dumps(context, indent=2, default=str))
        return 0

    review = run_claude(context) or fallback_report(context)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / f"nightly_review_{args.date}.md"
    header = f"# Nightly AI Review — {args.date} ({context['config']})\n\n"
    out.write_text(header + review + "\n")
    print(f"[nightly_ai_review] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
