#!/usr/bin/env python3
"""
AI Trade Decision Layer (market_sentiment.md §5) — CLI.

Thin wrapper over src/sentiment/decision.py. Reads the assembled GSS context
(data/metrics/sentiment_monitor_state.json), asks Claude (Dalio + Simons prompt)
for a strict-JSON trade decision, validates/caps it, and writes:
  data/sentiment/ai_decision_XAUUSD.json   (latest)
  data/sentiment/ai_decisions_XAUUSD.csv   (append-only history)

This DOES NOT place orders (`executed: false`). For decisions THROUGHOUT THE DAY
when a setup forms, let the sentiment engine drive it instead:
  python scripts/run_sentiment_engine.py --loop 900    # auto-decides on opportunity

Usage:
    python scripts/ai_trade_decision.py            # one advisory decision now
    python scripts/ai_trade_decision.py --print    # also echo the decision
    python scripts/ai_trade_decision.py --dry-run  # print context, skip Claude
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sentiment.decision import make_decision  # noqa: E402

SNAPSHOT = PROJECT_ROOT / "data" / "metrics" / "sentiment_monitor_state.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="AI trade decision layer (advisory).")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--print", dest="echo", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build the context and print it; do not call Claude.")
    args = ap.parse_args()

    try:
        context = json.loads(SNAPSHOT.read_text())
    except Exception:
        print("[ai_decision] no sentiment snapshot — run scripts/run_sentiment_engine.py first",
              file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(context, indent=2, default=str))
        return 0

    record, source = make_decision(context, args.symbol, trigger="manual")
    g = context.get("gss", {}) or {}
    print(f"[ai_decision] GSS={g.get('total_score')} ({g.get('regime')}) → "
          f"{record['decision']} conf={record['confidence']} "
          f"size={record['position_size_pct']}% via {source}  (ADVISORY, not executed)")
    if args.echo:
        print(json.dumps(record, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
