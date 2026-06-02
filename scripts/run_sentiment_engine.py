#!/usr/bin/env python3
"""
Market Sentiment Engine runner — assembles the GSS snapshot on a SLOW clock.

Each cycle it:
  1. Computes the REAL technical bias from our own 5m series (no API).
  2. Fetches the fundamental bias from FRED (real, if FRED_API_KEY is set;
     neutral otherwise).
  3. Leaves institutional / retail / news neutral until those feeds are built
     (each is honestly reported as MISSING, never faked).
  4. Builds the full context object (market_sentiment.md §5.1 / §9.2), pulling
     the live price from the running bot's monitor file when available.
  5. Writes two files:
       data/sentiment/gss_XAUUSD.json            (slim — nightly review consumes)
       data/metrics/sentiment_monitor_state.json (rich — the pop-up consumes)

This NEVER trades and NEVER touches the risk engine. It produces a score.
GSS stays advisory/display until it passes backtest.md.

Usage:
    python scripts/run_sentiment_engine.py                 # one cycle
    python scripts/run_sentiment_engine.py --loop 900      # every 15 min
    python scripts/run_sentiment_engine.py --once --print  # one cycle, echo JSON
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sentiment import GSSComponents, compute_gss                       # noqa: E402
from src.sentiment.feeds import (                                          # noqa: E402
    fetch_cot_net_long_wow_pct,
    fetch_etf_flow_3d,
    fetch_fundamental,
    fetch_news_sentiment,
    fetch_retail_long_pct,
)
from src.sentiment.gss import (                                            # noqa: E402
    MAX_FUNDAMENTAL, MAX_INSTITUTIONAL, MAX_NEWS, MAX_RETAIL, MAX_TECHNICAL,
    score_fundamental, score_institutional, score_news, score_retail,
)
from src.sentiment.store import append_gss_history, write_gss              # noqa: E402
from src.sentiment.technical import compute_technical                      # noqa: E402

MONITOR_FILE = PROJECT_ROOT / "data" / "metrics" / "sentiment_monitor_state.json"

# market_sentiment.md §8 — static reference levels (2026 regime).
PRICE_LEVELS = [
    {"label": "$4,700–$4,800 major resistance", "low": 4700, "high": 4800, "kind": "resistance"},
    {"label": "$4,550 consolidation ceiling", "low": 4550, "high": 4550, "kind": "resistance"},
    {"label": "$4,350–$4,400 breakout retest", "low": 4350, "high": 4400, "kind": "support"},
    {"label": "$4,200 CRITICAL weekly support", "low": 4200, "high": 4200, "kind": "critical"},
    {"label": "$4,000 structural bull floor", "low": 4000, "high": 4000, "kind": "critical"},
    {"label": "$3,450 2025 demand zone", "low": 3450, "high": 3450, "kind": "support"},
]


def _live_price(symbol: str = "XAUUSD") -> Optional[Dict[str, float]]:
    """Pull the latest bid/ask from the running bot's monitor file, if present."""
    metrics = PROJECT_ROOT / "data" / "metrics"
    candidates = []
    marker = PROJECT_ROOT / "config" / "ACTIVE_CONFIG"
    try:
        stem = Path(marker.read_text().strip().splitlines()[0].strip()).stem
        candidates.append(metrics / f"live_monitor_state_{stem}.json")
    except Exception:
        pass
    candidates.append(metrics / "live_monitor_state.json")
    for path in candidates:
        try:
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            for s in data.get("symbols", []) or []:
                tk = (s.get("ticker") or "").upper()
                if tk.startswith(symbol):
                    bid = float(s.get("bid", 0) or 0)
                    ask = float(s.get("ask", 0) or 0)
                    if bid > 0:
                        return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 2)
                                if ask else bid, "source": "mt5_live"}
        except Exception:
            continue
    return None


def build_snapshot(symbol: str = "XAUUSD") -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    tech = compute_technical(symbol)
    fund = fetch_fundamental()
    fund_pts = score_fundamental(
        fed_policy=fund.fed_policy,
        real_yield_falling=fund.real_yield_falling,
        real_yield_10y=fund.real_yield_10y,
        dxy_falling=fund.dxy_falling,
        dxy_level=fund.dxy_level,
        cpi_yoy=fund.cpi_yoy,
        fiscal_stress=fund.fiscal_stress,
    )
    cot = fetch_cot_net_long_wow_pct()
    etf_flow = fetch_etf_flow_3d()
    inst_pts = (score_institutional(cot_net_long_wow_pct=cot, etf_flow_3d=etf_flow)
                if (cot is not None or etf_flow is not None) else None)
    retail_long = fetch_retail_long_pct()
    retail_pts = score_retail(retail_long)
    news_sent = fetch_news_sentiment()
    news_pts = score_news(news_sentiment_avg=news_sent) if news_sent is not None else None

    components = GSSComponents(
        fundamental=fund_pts,
        technical=tech["points"],
        institutional=inst_pts,
        retail=retail_pts,
        news=news_pts,
    )
    result = compute_gss(components)

    structure = tech.get("structure", {})
    live = _live_price(symbol)
    price = live["mid"] if live else structure.get("price")

    # Risk flags (market_sentiment.md §5.1) — derived from what we actually know.
    weekday = now.weekday()
    flags = {
        "dxy_surging": bool(fund.dxy_falling is False),
        "real_yields_spiking": bool(fund.real_yield_falling is False),
        "retail_extreme_long": bool(retail_long is not None and retail_long > 80),
        "geopolitical_shock": False,  # needs news feed
        "weekend_gap_risk": bool(weekday == 4 and now.hour >= 19),  # Fri late
    }

    def _component(name: str, pts, maxv: int, details: str) -> Dict[str, Any]:
        missing = name in result.missing
        return {
            "score": result.breakdown[name],
            "max": maxv,
            "live": not missing,
            "details": details if not missing else "no feed yet → neutral",
        }

    snapshot = {
        "generated_at": now.isoformat(),
        "asset": symbol,
        "price": price,
        "price_source": (live or {}).get("source", "local_5m_csv"),
        "gss": {
            "total_score": result.total,
            "regime": result.regime,
            "breakdown": result.breakdown,
        },
        "components": {
            "fundamental": _component(
                "fundamental", fund_pts, MAX_FUNDAMENTAL,
                f"fed={fund.fed_policy} real10y={fund.real_yield_10y} "
                f"yld_falling={fund.real_yield_falling} cpi={fund.cpi_yoy} "
                f"fiscal_stress={fund.fiscal_stress}"),
            "technical": _component(
                "technical", tech["points"], MAX_TECHNICAL,
                f"trend={structure.get('trend')} rsi={structure.get('rsi_14')} "
                f"macd={structure.get('macd_signal')} bb={structure.get('bb_state')}"),
            "institutional": _component(
                "institutional", inst_pts, MAX_INSTITUTIONAL,
                f"cot_wow={cot} etf_flow={etf_flow}"),
            "retail": _component(
                "retail", retail_pts, MAX_RETAIL, f"retail_long%={retail_long}"),
            "news": _component(
                "news", news_pts, MAX_NEWS, f"news_sent={news_sent}"),
        },
        "market_structure": {**structure, "session": _session_name(now)},
        "macro_context": {
            "fed_policy": fund.fed_policy,
            "real_yield_10y": fund.real_yield_10y,
            "real_yield_falling": fund.real_yield_falling,
            "dxy_falling": fund.dxy_falling,
            "cpi_yoy": fund.cpi_yoy,
            "fiscal_stress": fund.fiscal_stress,
            "next_high_impact_event": _next_event(symbol),
        },
        "position_status": _position_status(symbol),
        "risk_flags": flags,
        "price_levels": _annotate_levels(price),
        "recommendation": _recommendation(result.total, flags),
        "missing_components": result.missing,
        "feeds": {
            "technical": "LIVE (local 5m)",
            "fundamental": "LIVE (FRED)" if os.environ.get("FRED_API_KEY") else "OFF (set FRED_API_KEY)",
            "institutional": (
                f"LIVE (COT{'+ETF' if etf_flow is not None else ''})"
                if (cot is not None or etf_flow is not None) else "OFF (CFTC unreachable)"),
            "retail": "LIVE (Myfxbook)" if retail_long is not None else (
                "OFF (set MYFXBOOK_*)" if not os.environ.get("MYFXBOOK_EMAIL") else "OFF (login/outlook failed)"),
            "news": "LIVE (Alpha Vantage)" if news_sent is not None else (
                "OFF (set ALPHAVANTAGE_API_KEY)" if not os.environ.get("ALPHAVANTAGE_API_KEY") else "OFF (rate-limited?)"),
        },
    }
    return snapshot


def _bot_state() -> Optional[Dict[str, Any]]:
    """Read the running bot's live_monitor_state JSON (active config first)."""
    metrics = PROJECT_ROOT / "data" / "metrics"
    paths = []
    try:
        stem = Path((PROJECT_ROOT / "config" / "ACTIVE_CONFIG")
                    .read_text().strip().splitlines()[0].strip()).stem
        paths.append(metrics / f"live_monitor_state_{stem}.json")
    except Exception:
        pass
    paths.append(metrics / "live_monitor_state.json")
    for p in paths:
        try:
            if p.exists():
                return json.loads(p.read_text())
        except Exception:
            continue
    return None


def _position_status(symbol: str) -> Dict[str, Any]:
    """Current bot position on `symbol` from the live monitor (§5.1)."""
    state = _bot_state() or {}
    for p in state.get("positions", []) or []:
        if (p.get("symbol") or "").upper().startswith(symbol):
            return {
                "current_position": (p.get("side") or "none").lower(),
                "qty": p.get("qty", 0),
                "entry": p.get("entry", 0),
                "unrealized_pnl": p.get("pnl", 0),
            }
    return {"current_position": "none", "unrealized_pnl": 0}


def _next_event(symbol: str) -> Optional[str]:
    """Nearest upcoming high-impact news event from the bot's news block (§5.1)."""
    state = _bot_state() or {}
    upcoming = (state.get("news", {}) or {}).get("upcoming", []) or []
    for e in upcoming:
        title = e.get("title") or e.get("currency") or "event"
        when = e.get("time_ist") or ""
        return f"{title} {when}".strip()
    return None


def _session_name(now: datetime) -> str:
    m = now.hour * 60 + now.minute
    if 13 * 60 <= m < 17 * 60:
        return "ny_london_overlap"
    if 13 * 60 <= m < 22 * 60:
        return "new_york"
    if 8 * 60 <= m < 17 * 60:
        return "london"
    if m < 9 * 60:
        return "tokyo"
    return "off_session"


def _annotate_levels(price: Optional[float]) -> list:
    out = []
    for lv in PRICE_LEVELS:
        rel = "—"
        if price is not None:
            if price > lv["high"]:
                rel = "above"
            elif price < lv["low"]:
                rel = "below"
            else:
                rel = "AT"
        out.append({**lv, "rel": rel})
    return out


def _recommendation(total: float, flags: Dict[str, bool]) -> Dict[str, Any]:
    """Deterministic mapping of GSS → action (market_sentiment.md §4.3), with the
    one proven override (never fight the dollar). NOT an order — advisory text."""
    if flags["dxy_surging"] and flags["real_yields_spiking"]:
        return {"action": "FLAT/SHORT", "note": "DXY + real yields both rising — override bullish GSS"}
    if total >= 65:
        action = "LONG"
    elif total >= 50:
        action = "LONG (reduced)"
    elif total >= 35:
        action = "FLAT / chop"
    elif total >= 20:
        action = "SHORT (reduced)"
    else:
        action = "SHORT"
    note = "size −50% (retail extreme long)" if flags["retail_extreme_long"] else ""
    return {"action": action, "note": note}


def _write(snapshot: Dict[str, Any]) -> None:
    MONITOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MONITOR_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, default=str))
    os.replace(tmp, MONITOR_FILE)
    # Slim score for the nightly review (reuse the store contract).
    from src.sentiment.gss import GSSResult
    res = GSSResult(
        total=snapshot["gss"]["total_score"],
        regime=snapshot["gss"]["regime"],
        breakdown=snapshot["gss"]["breakdown"],
        missing=snapshot["missing_components"],
    )
    write_gss(snapshot["asset"], res, source_detail={"feeds": snapshot["feeds"]})
    # Accumulate one row per cycle for the future GSS backtest.
    append_gss_history(snapshot["asset"], snapshot)


def main() -> int:
    ap = argparse.ArgumentParser(description="Market Sentiment Engine runner.")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--loop", type=int, default=0, help="Seconds between cycles (0 = once).")
    ap.add_argument("--once", action="store_true", help="Single cycle (default).")
    ap.add_argument("--print", dest="echo", action="store_true", help="Echo the JSON.")
    args = ap.parse_args()

    def cycle() -> None:
        snap = build_snapshot(args.symbol)
        _write(snap)
        g = snap["gss"]
        print(f"[sentiment] {snap['generated_at']}  GSS={g['total_score']} "
              f"({g['regime']})  price={snap['price']}  "
              f"missing={snap['missing_components']}")
        if args.echo:
            print(json.dumps(snap, indent=2, default=str))

    if args.loop and not args.once:
        print(f"[sentiment] loop every {args.loop}s — Ctrl-C to stop")
        try:
            while True:
                cycle()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\n[sentiment] stopped")
    else:
        cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
