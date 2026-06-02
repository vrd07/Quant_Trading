"""
Persist the latest GSS to data/sentiment/gss_{symbol}.json.

Atomic temp-file + rename so a reader never sees a half-written file (same
pattern as live_monitor_emitter). Read side returns None on any problem — the
consumer treats "no score" as neutral, never as a signal.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .gss import GSSResult

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SENTIMENT_DIR = _PROJECT_ROOT / "data" / "sentiment"

# Append-only history — one row per engine cycle. This is the dataset the future
# GSS backtest reads to test whether the score actually predicts XAUUSD moves.
_HISTORY_FIELDS = [
    "timestamp", "price", "price_source", "gss_total", "regime",
    "fundamental", "technical", "institutional", "retail", "news",
    "missing", "recommendation",
]


def _path_for(symbol: str) -> Path:
    return _SENTIMENT_DIR / f"gss_{symbol}.json"


def write_gss(symbol: str, result: GSSResult, source_detail: Optional[Dict[str, Any]] = None) -> None:
    """Atomically write the latest score. Never raises into the caller."""
    try:
        _SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": symbol,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gss_total": result.total,
            "regime": result.regime,
            "breakdown": result.breakdown,
            "missing": result.missing,
            "source_detail": source_detail or {},
        }
        dest = _path_for(symbol)
        fd, tmp = tempfile.mkstemp(dir=str(_SENTIMENT_DIR), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, dest)
    except Exception:
        # Persisting a sentiment score must never break the caller.
        pass


def append_gss_history(symbol: str, snapshot: Dict[str, Any]) -> None:
    """Append one row of the rich snapshot to data/sentiment/gss_history_{symbol}.csv.

    Append-only and header-on-create, so it accumulates across runs and is safe
    to read while the engine is writing. Never raises into the caller.
    """
    try:
        _SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)
        path = _SENTIMENT_DIR / f"gss_history_{symbol}.csv"
        gss = snapshot.get("gss", {}) or {}
        bd = gss.get("breakdown", {}) or {}
        row = {
            "timestamp": snapshot.get("generated_at", ""),
            "price": snapshot.get("price", ""),
            "price_source": snapshot.get("price_source", ""),
            "gss_total": gss.get("total_score", ""),
            "regime": gss.get("regime", ""),
            "fundamental": bd.get("fundamental", ""),
            "technical": bd.get("technical", ""),
            "institutional": bd.get("institutional", ""),
            "retail": bd.get("retail", ""),
            "news": bd.get("news", ""),
            "missing": "|".join(snapshot.get("missing_components", []) or []),
            "recommendation": (snapshot.get("recommendation", {}) or {}).get("action", ""),
        }
        write_header = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_HISTORY_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass


def read_gss(symbol: str, max_age_minutes: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Return the latest stored score, or None if missing/stale/unreadable.

    A stale score is treated as no score on purpose: the consumer must fall back
    to neutral rather than trade on hours-old sentiment.
    """
    try:
        path = _path_for(symbol)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if max_age_minutes is not None:
            gen = datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - gen).total_seconds() / 60.0
            if age_min > max_age_minutes:
                return None
        return data
    except Exception:
        return None
