"""
Opportunity gate — decides WHEN an AI trade decision is worth making.

Calling Claude every 15-min cycle is ~96 calls/day for nothing — in chop the
answer is always "FLAT". Instead this cheap, pure, deterministic gate runs every
cycle and only trips when the picture has *changed enough to re-decide*:

  - GSS entered/left the actionable zone (≤35 bearish or ≥65 bullish)
  - GSS moved materially since the last decision (≥ GSS_DELTA)
  - the regime label changed
  - a risk flag flipped ON
  - (refresh) GSS is in the actionable zone and a longer interval has passed

All gated by a minimum cooldown so even genuine triggers can't spam. No prior
decision → trip once (the initial read). Pure: pass `now` and `prev` in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ACTIONABLE_HIGH = 65.0
ACTIONABLE_LOW = 35.0
GSS_DELTA = 8.0


def _zone(gss: float) -> str:
    if gss >= ACTIONABLE_HIGH:
        return "bull"
    if gss <= ACTIONABLE_LOW:
        return "bear"
    return "mid"


def _parse_ts(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def evaluate_opportunity(
    snapshot: Dict[str, Any],
    prev: Optional[Dict[str, Any]],
    cooldown_min: float = 20.0,
    refresh_min: float = 90.0,
    now: Optional[datetime] = None,
) -> Tuple[bool, List[str]]:
    """Return (should_decide, reasons). `prev` is the last decision record."""
    now = now or datetime.now(timezone.utc)
    gss = float((snapshot.get("gss", {}) or {}).get("total_score", 50) or 50)
    regime = (snapshot.get("gss", {}) or {}).get("regime")
    flags = snapshot.get("risk_flags", {}) or {}

    if prev is None:
        return True, ["initial decision"]

    prev_ts = _parse_ts(prev.get("generated_at"))
    mins = (now - prev_ts).total_seconds() / 60.0 if prev_ts else 1e9
    if mins < cooldown_min:
        return False, [f"cooldown {mins:.0f}/{cooldown_min:.0f}m"]

    prev_gss = float(prev.get("gss_total") or 50)
    prev_flags = prev.get("risk_flags", {}) or {}
    reasons: List[str] = []

    if abs(gss - prev_gss) >= GSS_DELTA:
        reasons.append(f"GSS moved {prev_gss:.0f}->{gss:.0f}")
    if regime and regime != prev.get("regime"):
        reasons.append(f"regime {prev.get('regime')}->{regime}")
    if _zone(gss) != _zone(prev_gss):
        reasons.append(f"zone {_zone(prev_gss)}->{_zone(gss)}")
    for k, v in flags.items():
        if v and not prev_flags.get(k):
            reasons.append(f"flag {k} ON")

    # Periodic refresh while a real setup persists (so a standing LONG/SHORT view
    # gets re-checked), but never in chop — chop doesn't need re-deciding.
    if not reasons and _zone(gss) != "mid" and mins >= refresh_min:
        reasons.append(f"refresh ({mins:.0f}m in {_zone(gss)} zone)")

    return (len(reasons) > 0), reasons
