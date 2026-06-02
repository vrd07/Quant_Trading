"""
AI Trade Decision core (market_sentiment.md §5) — importable by the CLI and the
engine loop.

Turns a GSS context object into a strict-JSON trade decision via Claude (with a
Dalio + Simons prompt), validates/caps it, and persists it. Produces a decision;
it does NOT place orders — `executed` is always False here. Live execution is a
separate, gated step that must route through the bot's RiskEngine veto.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DECISION_JSON = _PROJECT_ROOT / "data" / "sentiment" / "ai_decision_XAUUSD.json"
_DECISION_CSV = _PROJECT_ROOT / "data" / "sentiment" / "ai_decisions_XAUUSD.csv"

_DECISIONS = {"LONG", "SHORT", "FLAT", "REDUCE"}
_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}

PROMPT = """\
You are the decision layer for an automated XAUUSD (spot gold) trader. Reason
like two disciplined minds fused:
- Ray Dalio: state what would have to be TRUE for this trade to work; never be
  arrogant about a position; weight the downside; if signals conflict, prefer
  doing nothing over forcing a view.
- Jim Simons: act only on a measurable edge; if the evidence is weak or mixed,
  the expected value is ~0 and the correct size is small or zero; size by
  conviction, not emotion; respect the math and the costs.

You receive a structured market context (the Gold Sentiment Score and its parts,
market structure, macro, risk flags, current position). Output ONE trading
decision as STRICT JSON and NOTHING ELSE.

HARD RULES:
1. Respect the GSS regime but override it if risk flags warrant.
2. Never fight the dollar: if dxy_surging AND real_yields_spiking, do not go
   LONG regardless of GSS — go FLAT or SHORT.
3. If retail_extreme_long is true, cut position_size_pct by ~half.
4. Avoid new entries within ~2h of a high-impact US event
   (next_high_impact_event) — prefer FLAT/REDUCE.
5. Stop loss from ATR: ~1.5x ATR for swing, ~1.0x ATR for day trades.
6. position_size_pct is a PERCENT of equity at risk, capped at 2.0. When the
   edge is weak/mixed (GSS 35-65, or components disagree), use 0.0-0.5.
7. If a component is in missing_components, treat it as unknown — do not assume
   it is favorable.

Output EXACTLY this JSON shape (numbers, not strings, for prices/sizes):
{
  "decision": "LONG | SHORT | FLAT | REDUCE",
  "confidence": "HIGH | MEDIUM | LOW",
  "entry_zone": {"min": 0, "max": 0},
  "stop_loss": 0,
  "take_profit_1": 0,
  "take_profit_2": 0,
  "position_size_pct": 0.0,
  "rationale": "one or two sentences, concrete",
  "override_reason": null
}

CONTEXT:
"""


def _claude_bin() -> Optional[str]:
    explicit = Path.home() / ".local" / "bin" / "claude"
    if explicit.exists():
        return str(explicit)
    return shutil.which("claude")


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def run_claude(context: Dict[str, Any], timeout: int = 300) -> Optional[Dict[str, Any]]:
    claude = _claude_bin()
    if not claude:
        return None
    prompt = PROMPT + json.dumps(context, indent=2, default=str)
    try:
        r = subprocess.run([claude, "-p", prompt], cwd=str(_PROJECT_ROOT),
                           text=True, capture_output=True, timeout=timeout)
    except Exception as e:
        print(f"  [warn] claude failed: {e}", file=sys.stderr)
        return None
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return None
    return _extract_json(r.stdout)


def validate(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce the model's JSON into a safe, well-typed decision (size hard-capped)."""
    out: Dict[str, Any] = {}
    d = str(decision.get("decision", "FLAT")).upper().strip()
    out["decision"] = d if d in _DECISIONS else "FLAT"
    c = str(decision.get("confidence", "LOW")).upper().strip()
    out["confidence"] = c if c in _CONFIDENCE else "LOW"

    def num(v) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    ez = decision.get("entry_zone", {}) or {}
    out["entry_zone"] = {"min": num(ez.get("min")), "max": num(ez.get("max"))}
    out["stop_loss"] = num(decision.get("stop_loss"))
    out["take_profit_1"] = num(decision.get("take_profit_1"))
    out["take_profit_2"] = num(decision.get("take_profit_2"))
    out["position_size_pct"] = max(0.0, min(2.0, num(decision.get("position_size_pct"))))
    out["rationale"] = str(decision.get("rationale", ""))[:500]
    out["override_reason"] = (str(decision["override_reason"])[:300]
                              if decision.get("override_reason") else None)
    return out


def fallback_decision(context: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic decision from GSS when Claude is unavailable (advisory)."""
    rec = (context.get("recommendation", {}) or {}).get("action", "FLAT / chop")
    d = ("LONG" if "LONG" in rec else "SHORT" if "SHORT" in rec else "FLAT")
    return {
        "decision": d, "confidence": "LOW",
        "entry_zone": {"min": 0, "max": 0},
        "stop_loss": 0, "take_profit_1": 0, "take_profit_2": 0,
        "position_size_pct": 0.0,
        "rationale": f"claude CLI unavailable — deterministic GSS map ({rec}).",
        "override_reason": None,
    }


def persist(symbol: str, context: Dict[str, Any], decision: Dict[str, Any],
            source: str, trigger: str = "") -> Dict[str, Any]:
    record = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asset": symbol,
        "source": source,                       # "claude" | "fallback"
        "trigger": trigger,                     # why this decision fired
        "gss_total": (context.get("gss", {}) or {}).get("total_score"),
        "regime": (context.get("gss", {}) or {}).get("regime"),
        "price": context.get("price"),
        "risk_flags": context.get("risk_flags", {}) or {},
        "executed": False,                      # never auto-executed here
        **decision,
    }
    _DECISION_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DECISION_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, default=str))
    tmp.replace(_DECISION_JSON)

    fields = ["generated_at", "asset", "source", "trigger", "gss_total", "regime",
              "price", "decision", "confidence", "position_size_pct", "stop_loss",
              "take_profit_1", "take_profit_2", "executed", "rationale", "override_reason"]
    write_header = not _DECISION_CSV.exists()
    with open(_DECISION_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(record)
    return record


def make_decision(context: Dict[str, Any], symbol: str = "XAUUSD",
                  trigger: str = "") -> Tuple[Dict[str, Any], str]:
    """Full path: Claude (or deterministic fallback) → validate → persist."""
    raw = run_claude(context)
    if raw is not None:
        decision, source = validate(raw), "claude"
    else:
        decision, source = fallback_decision(context), "fallback"
    record = persist(symbol, context, decision, source, trigger)
    return record, source


def load_last_decision() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_DECISION_JSON.read_text())
    except Exception:
        return None
