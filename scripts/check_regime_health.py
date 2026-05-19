#!/usr/bin/env python3
"""
Daily sanity check for the ML regime classifier output.

Reads data/config_override_{SYMBOL}.json for the symbols listed (default:
auto-discover from filesystem), prints a one-line status per symbol, and
exits non-zero if any symbol that *should* have a calibrated model has
silently degraded to rule-based — the failure mode that left XAUUSD on
rules for 3+ weeks in April–May 2026.

Usage:
    python scripts/check_regime_health.py                    # all overrides
    python scripts/check_regime_health.py --symbols XAUUSD   # one symbol
    python scripts/check_regime_health.py --max-age-hours 36 # custom freshness

Exit codes:
    0  all healthy (ML active, fresh, confident)
    1  one or more symbols degraded (rule-based / stale / low-confidence)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# A symbol is "expected to have ML" only if its historical CSV is big enough
# to pass the classifier's `len(X) < 30` guard after warmup. Below this many
# 5m bars the classifier will (correctly) stay on rule-based.
ML_VIABLE_MIN_BARS = 12_000


def _hist_csv_bars(symbol: str) -> int:
    path = DATA_DIR / "historical" / f"{symbol}_5m_real.csv"
    if not path.exists():
        return 0
    with path.open() as f:
        return sum(1 for _ in f) - 1  # minus header


def _discover_overrides() -> list[Path]:
    return sorted(DATA_DIR.glob("config_override_*.json"))


def _check_one(path: Path, max_age_hours: float) -> tuple[bool, str]:
    """Returns (healthy, one_line_status). 'healthy' is False when this is a
    real degradation (vs an expected fallback due to thin history)."""
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return False, f"  ❌ {path.name}: unreadable ({e})"

    symbol = d.get("symbol", path.stem.replace("config_override_", ""))
    regime = d.get("regime", "?")
    conf = d.get("confidence", 0.0)
    clf = d.get("classifier", "?")
    gen_at = d.get("generated_at", "")

    age_hours = float("inf")
    if gen_at:
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(gen_at)
            age_hours = age.total_seconds() / 3600.0
        except Exception:
            pass

    is_ml = clf.startswith("RandomForest")
    bars = _hist_csv_bars(symbol)
    ml_viable = bars >= ML_VIABLE_MIN_BARS

    # Decide health
    problems = []
    if age_hours > max_age_hours:
        problems.append(f"stale {age_hours:.0f}h")
    if not is_ml and ml_viable:
        problems.append(f"rule-based despite {bars:,} bars available")
    if is_ml and conf < 0.55:
        problems.append(f"low confidence {conf:.0%}")

    badge = "✅" if not problems else "⚠️ "
    line = (
        f"  {badge} {symbol:<8} {regime:<8} "
        f"conf={conf:.0%}  {clf:<40s}  "
        f"age={age_hours:>4.1f}h  bars={bars:>6,}"
    )
    if problems:
        line += "\n      → " + "; ".join(problems)
    return (not problems), line


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--symbols", nargs="+", default=None,
                   help="Symbols to check (default: all config_override_*.json)")
    p.add_argument("--max-age-hours", type=float, default=36.0,
                   help="Max acceptable override age before flagging stale (default 36h)")
    args = p.parse_args()

    if args.symbols:
        paths = [DATA_DIR / f"config_override_{s}.json" for s in args.symbols]
    else:
        paths = _discover_overrides()

    if not paths:
        print("⚠️  No regime override files found in data/")
        return 1

    print("Regime classifier health:")
    all_ok = True
    for path in paths:
        ok, line = _check_one(path, args.max_age_hours)
        print(line)
        if not ok:
            all_ok = False

    if not all_ok:
        print("\n⚠️  One or more symbols are degraded. Investigate before going 24×7.")
        return 1
    print("\n✅ All checked symbols are on calibrated ML with fresh overrides.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
