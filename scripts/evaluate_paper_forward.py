#!/usr/bin/env python3
"""
Paper forward-test evaluator — does the (advisory) sentiment AI have real edge?

Reads the paper-trade ledger the engine accumulates
(``data/sentiment/paper_trades_XAUUSD.csv``, written by
``src/sentiment/paper_broker.py``) and grades it against the live-deploy gates in
``backtest.md`` §1 (G1–G8), with an honest sample-size guard so a handful of
lucky trades never reads as "edge".

This is a FORWARD test — a single going-forward stream — NOT the historical
walk-forward of backtest.md. Therefore:
  • G1–G6 + expectancy are computed directly and become meaningful only once the
    sample is large enough. We withhold any verdict under ``MIN_TRADES`` and flag
    G6 (≥60 trades/yr) until the stream is long enough to annualize.
  • G7 (out-of-sample walk-forward windows) is N/A here — it needs the historical
    run (``scripts/run_backtest.py``). A forward test COMPLEMENTS that gate, it
    never replaces it.
  • G8 (per-regime non-loss) is graded if ``regime_at_entry`` was logged.

Fills in the sim are cycle-granular (SL/TP marked against the ~15-min engine
price, no intrabar) → read results as OPTIMISTIC vs backtest.md's strict fills.

Usage:
    python scripts/evaluate_paper_forward.py
    python scripts/evaluate_paper_forward.py --equity 5000 --risk-usd 50 --json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEDGER = PROJECT_ROOT / "data" / "sentiment" / "paper_trades_XAUUSD.csv"
OUT_JSON = PROJECT_ROOT / "data" / "metrics" / "paper_eval_XAUUSD.json"

# backtest.md §1 thresholds.
G1_DAILY_WIN = 0.70      # ≥70% of trading days finish ≥ +0R
G2_WORST_DAY = -2.0      # no day worse than −2R
G3_PROFIT_FACTOR = 1.4
G4_SHARPE = 1.0
G5_MAX_DD_PCT = 12.0
G6_TRADES_PER_YEAR = 60
TRADING_DAYS = 252

# Below this many CLOSED trades, NO gate is trustworthy — the verdict is withheld
# regardless of how pretty the metrics look. backtest.md G6 wants ≥60/yr; this is
# the absolute floor before we even start believing the numbers.
MIN_TRADES = 30


def _default_equity() -> float:
    """Best-effort account size from the active config stem (…_5000 → 5000)."""
    try:
        marker = (PROJECT_ROOT / "config" / "ACTIVE_CONFIG").read_text().splitlines()[0]
        m = re.search(r"(\d{3,6})", Path(marker.strip()).stem)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return 5000.0


def load_trades(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    if df.empty or "r_multiple" not in df.columns:
        return pd.DataFrame()
    df["r_multiple"] = pd.to_numeric(df["r_multiple"], errors="coerce")
    return df.dropna(subset=["r_multiple"]).reset_index(drop=True)


def _gate(status: str, actual: Any, threshold: str, note: str = "") -> Dict[str, Any]:
    return {"status": status, "actual": actual, "threshold": threshold, "note": note}


def evaluate(df: pd.DataFrame, equity: float, risk_usd: float) -> Dict[str, Any]:
    n = len(df)
    out: Dict[str, Any] = {"n_trades": n, "equity_assumed": equity,
                           "risk_usd": risk_usd, "gates": {}, "metrics": {}}
    if n == 0:
        out["verdict"] = "NO DATA"
        out["verdict_note"] = (
            "No closed paper trades yet. The engine opens a paper trade only on an "
            "actionable LONG/SHORT with a valid SL/TP; in chop the AI stays FLAT, so "
            "the ledger stays empty. Re-run once setups have fired.")
        return out

    r = df["r_multiple"].astype(float)
    wins, losses = r[r > 0], r[r < 0]
    win_rate = len(wins) / n
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0  # negative
    expectancy = float(r.mean())  # == win_rate*avg_win + loss_rate*avg_loss
    gross_win, gross_loss = float(wins.sum()), float(abs(losses.sum()))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else math.inf
    total_r = float(r.sum())

    # Equity curve / max drawdown (R → $ → % of assumed equity, for G5).
    cum = r.cumsum()
    max_dd_r = float((cum - cum.cummax()).min())  # ≤ 0
    max_dd_pct = abs(max_dd_r) * risk_usd / equity * 100 if equity > 0 else float("nan")

    # Daily aggregation (G1 win-rate, G2 worst day, G4 Sharpe, Sortino).
    day = pd.to_datetime(df["closed_at"], utc=True, errors="coerce").dt.date
    daily = r.groupby(day).sum()
    n_days = int(daily.notna().sum())
    daily_win_rate = float((daily >= 0).mean()) if n_days else float("nan")
    worst_day = float(daily.min()) if n_days else float("nan")
    sd = float(daily.std(ddof=1)) if n_days >= 2 else 0.0
    sharpe = (float(daily.mean()) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else None
    dn = daily[daily < 0]
    dsd = float(dn.std(ddof=1)) if len(dn) >= 2 else 0.0
    sortino = (float(daily.mean()) / dsd * math.sqrt(TRADING_DAYS)) if dsd > 0 else None

    # Trades per year (G6) — annualize over the ledger's calendar span.
    opened = pd.to_datetime(df["opened_at"], utc=True, errors="coerce")
    closed = pd.to_datetime(df["closed_at"], utc=True, errors="coerce")
    span_days = float((closed.max() - opened.min()).total_seconds()) / 86400 if n else 0.0
    trades_per_year = n / max(span_days / 365.25, 1e-9) if span_days > 0 else float("nan")

    out["metrics"] = {
        "win_rate": round(win_rate, 3), "avg_win_r": round(avg_win, 3),
        "avg_loss_r": round(avg_loss, 3), "expectancy_r": round(expectancy, 3),
        "profit_factor": (round(profit_factor, 3) if math.isfinite(profit_factor) else None),
        "total_r": round(total_r, 2), "total_usd": round(total_r * risk_usd, 2),
        "max_drawdown_r": round(max_dd_r, 2), "max_drawdown_pct": round(max_dd_pct, 2),
        "n_days": n_days, "daily_win_rate": round(daily_win_rate, 3),
        "worst_day_r": round(worst_day, 3),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "sortino": round(sortino, 2) if sortino is not None else None,
        "span_days": round(span_days, 1),
        "trades_per_year": round(trades_per_year, 1) if math.isfinite(trades_per_year) else None,
    }

    enough = n >= MIN_TRADES

    def grade(passed: Optional[bool]) -> str:
        if not enough:
            return "INSUFFICIENT"
        if passed is None:
            return "INSUFFICIENT"
        return "PASS" if passed else "FAIL"

    g = out["gates"]
    g["G1_daily_win_rate"] = _gate(
        grade(daily_win_rate >= G1_DAILY_WIN if n_days else None),
        f"{daily_win_rate*100:.0f}%" if n_days else "—", "≥70% green days")
    g["G2_worst_day"] = _gate(
        grade(worst_day >= G2_WORST_DAY if n_days else None),
        f"{worst_day:+.2f}R" if n_days else "—", "≥ −2R")
    g["G3_profit_factor"] = _gate(
        grade(profit_factor >= G3_PROFIT_FACTOR),
        ("∞" if not math.isfinite(profit_factor) else f"{profit_factor:.2f}"), "≥ 1.4")
    g["G4_sharpe"] = _gate(
        grade(sharpe >= G4_SHARPE if sharpe is not None else None),
        f"{sharpe:.2f}" if sharpe is not None else "—", "≥ 1.0 (daily, annualized)")
    g["G5_max_drawdown"] = _gate(
        grade(max_dd_pct <= G5_MAX_DD_PCT),
        f"{max_dd_pct:.1f}%", f"≤ 12% (@ ${equity:,.0f} equity)")
    g["G6_trades_per_year"] = _gate(
        grade(trades_per_year >= G6_TRADES_PER_YEAR if math.isfinite(trades_per_year) else None),
        f"{trades_per_year:.0f}/yr" if math.isfinite(trades_per_year) else "—", "≥ 60/yr")
    g["G7_walk_forward_oos"] = _gate(
        "N/A", "—", "≥80% OOS windows",
        "walk-forward only — run scripts/run_backtest.py for this gate")

    # G8 — per-regime net R, if regime was logged at entry.
    if "regime_at_entry" in df.columns and df["regime_at_entry"].notna().any():
        per_regime = r.groupby(df["regime_at_entry"].fillna("?")).sum().round(2)
        out["per_regime_r"] = {str(k): float(v) for k, v in per_regime.items()}
        g["G8_regime_non_loss"] = _gate(
            grade(bool((per_regime >= 0).all())),
            ", ".join(f"{k}:{v:+.1f}R" for k, v in per_regime.items()),
            "no regime net-negative")
    else:
        g["G8_regime_non_loss"] = _gate("N/A", "—", "no regime net-negative",
                                        "regime_at_entry not logged on these trades")

    # Per-confidence breakdown (context, not a gate).
    if "confidence" in df.columns and df["confidence"].notna().any():
        out["per_confidence"] = {
            str(k): {"n": int(v.size), "total_r": round(float(v.sum()), 2),
                     "win_rate": round(float((v > 0).mean()), 2)}
            for k, v in r.groupby(df["confidence"].fillna("?"))}

    # Overall verdict.
    gradeable = [v["status"] for v in g.values() if v["status"] in ("PASS", "FAIL")]
    if not enough:
        out["verdict"] = "INSUFFICIENT SAMPLE"
        out["verdict_note"] = (
            f"Only {n} closed trade(s). Need ≥{MIN_TRADES} before any gate is "
            f"trustworthy (backtest.md G6 wants ≥60/yr). Metrics shown for tracking "
            f"only — do NOT act on them yet.")
    elif gradeable and all(s == "PASS" for s in gradeable):
        out["verdict"] = "PASS (forward)"
        out["verdict_note"] = (
            "Forward gates clear. This is live evidence of edge, NOT a substitute "
            "for the historical walk-forward — confirm with scripts/run_backtest.py "
            "before any live wiring.")
    else:
        failed = [k for k, v in g.items() if v["status"] == "FAIL"]
        out["verdict"] = "FAIL"
        out["verdict_note"] = "Failed: " + ", ".join(failed)
    return out


def format_report(res: Dict[str, Any]) -> str:
    L = ["", "═" * 66, "  PAPER FORWARD-TEST — sentiment AI edge check (backtest.md §1)",
         "═" * 66]
    if res["n_trades"] == 0:
        L += ["", "  " + res["verdict"], "  " + res["verdict_note"], ""]
        L += ["  Gates you'll be graded against once trades accumulate:",
              "    G1 daily win-rate ≥70%   G2 worst day ≥−2R   G3 PF ≥1.4",
              "    G4 Sharpe ≥1.0           G5 maxDD ≤12%       G6 ≥60 trades/yr", "",
              "═" * 66, ""]
        return "\n".join(L)

    m = res["metrics"]
    L += ["", f"  Sample : {res['n_trades']} trades over {m['span_days']:.0f} days "
              f"({m['n_days']} trading days)   ·   assumed equity ${res['equity_assumed']:,.0f}",
          f"  Result : {m['total_r']:+.2f}R  (${m['total_usd']:+,.2f})   ·   "
          f"expectancy {m['expectancy_r']:+.3f}R/trade", ""]
    pf = "∞" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}"
    sh = "—" if m["sharpe"] is None else f"{m['sharpe']:.2f}"
    so = "—" if m["sortino"] is None else f"{m['sortino']:.2f}"
    L += [f"  win-rate {m['win_rate']*100:.0f}%  avgWin {m['avg_win_r']:+.2f}R  "
          f"avgLoss {m['avg_loss_r']:+.2f}R   PF {pf}   Sharpe {sh}  Sortino {so}",
          f"  maxDD {m['max_drawdown_r']:.2f}R ({m['max_drawdown_pct']:.1f}%)   "
          f"daily-win {m['daily_win_rate']*100:.0f}%   worst-day {m['worst_day_r']:+.2f}R", ""]
    L.append("  " + "─" * 62)
    icon = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT": "⏳", "N/A": "·"}
    for key, gate in res["gates"].items():
        L.append(f"  {icon.get(gate['status'],'?')} {key:22} {gate['actual']:>14}  "
                 f"(need {gate['threshold']})")
        if gate["note"]:
            L.append(f"       └─ {gate['note']}")
    if res.get("per_regime_r"):
        L.append("  " + "─" * 62)
        L.append("  per-regime net R : " +
                 "  ".join(f"{k} {v:+.1f}R" for k, v in res["per_regime_r"].items()))
    L += ["  " + "─" * 62, "",
          f"  VERDICT: {res['verdict']}", f"  {res['verdict_note']}", "", "═" * 66, ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade the paper forward-test vs backtest.md gates.")
    ap.add_argument("--ledger", default=str(LEDGER), help="paper trades CSV")
    ap.add_argument("--equity", type=float, default=None,
                    help="account equity for maxDD%% (default: from ACTIVE_CONFIG)")
    ap.add_argument("--risk-usd", type=float, default=50.0,
                    help="$ risked per 1R in the sim (paper_broker RISK_PER_TRADE_USD)")
    ap.add_argument("--json", action="store_true", help="also write data/metrics/paper_eval_XAUUSD.json")
    args = ap.parse_args()

    equity = args.equity if args.equity is not None else _default_equity()
    df = load_trades(Path(args.ledger))
    res = evaluate(df, equity=equity, risk_usd=args.risk_usd)
    print(format_report(res))

    if args.json:
        try:
            OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
            OUT_JSON.write_text(json.dumps(res, indent=2, default=str))
            print(f"[eval] wrote {OUT_JSON}")
        except Exception as e:
            print(f"[eval] could not write JSON: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
