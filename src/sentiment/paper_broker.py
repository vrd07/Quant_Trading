"""
Paper-trading bridge — the GATED execution step, in its safe (no-money) form.

This forward-tests the AI's decisions: it opens/closes simulated XAUUSD positions
from the AI signals, marks them against the live price each cycle, and records
realized R / P&L. NO real orders are ever sent. It is the gate that must show a
positive, real-money-free track record BEFORE any live wiring is even considered.

Risk discipline (deliberately simple and self-contained — the LIVE path would use
the bot's full RiskEngine instead):
  - one open paper position at a time (no pyramiding, no hedging)
  - a stop loss is REQUIRED to open
  - R is computed from entry/SL; exit at TP1 or SL (whichever price hits first)
  - position size is informational (% risk) — P&L is reported in R-multiples and
    in $ on a configurable notional risk-per-trade.

Pure-ish: pass the snapshot/decision in; the only side effects are the state
file and the trades CSV. Telegram notification is injected as a callable.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sentiment"
_STATE = _DIR / "paper_state_XAUUSD.json"
_TRADES = _DIR / "paper_trades_XAUUSD.csv"

# $ risked per paper trade at 1R — purely for a readable $ P&L on the sim.
RISK_PER_TRADE_USD = 50.0


def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(_STATE.read_text())
    except Exception:
        return {"position": None, "realized_r": 0.0, "realized_usd": 0.0, "trades": 0,
                "wins": 0, "losses": 0}


def _save_state(state: Dict[str, Any]) -> None:
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.replace(_STATE)
    except Exception:
        pass


def _log_trade(row: Dict[str, Any]) -> None:
    fields = ["closed_at", "opened_at", "side", "entry", "exit", "stop_loss",
              "take_profit", "r_multiple", "pnl_usd", "exit_reason", "gss_at_entry"]
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        header = not _TRADES.exists()
        with open(_TRADES, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if header:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass


def _r_at(side: str, entry: float, stop: float, exit_price: float) -> float:
    """Realized R-multiple: (move in favor) / (risk per unit)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    move = (exit_price - entry) if side == "LONG" else (entry - exit_price)
    return round(move / risk, 3)


def _fmt(x: Any) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "—"


def update(snapshot: Dict[str, Any], decision: Optional[Dict[str, Any]],
           notify: Optional[Callable[[str], bool]] = None) -> Dict[str, Any]:
    """Mark/close any open paper position against the live price, then (if a new
    actionable decision is given) maybe open one. Returns the updated state."""
    state = _load_state()
    price = snapshot.get("price")
    now = datetime.now(timezone.utc).isoformat()

    # 1) manage an open position
    pos = state.get("position")
    if pos and isinstance(price, (int, float)) and price > 0:
        side, entry, stop, tp = pos["side"], pos["entry"], pos["stop_loss"], pos["take_profit"]
        hit_sl = (price <= stop) if side == "LONG" else (price >= stop)
        hit_tp = (price >= tp) if side == "LONG" else (price <= tp)
        exit_price = exit_reason = None
        if hit_sl:
            exit_price, exit_reason = stop, "stop_loss"
        elif hit_tp:
            exit_price, exit_reason = tp, "take_profit"
        if exit_price is not None:
            r = _r_at(side, entry, stop, exit_price)
            pnl = round(r * RISK_PER_TRADE_USD, 2)
            state["realized_r"] = round(state.get("realized_r", 0.0) + r, 3)
            state["realized_usd"] = round(state.get("realized_usd", 0.0) + pnl, 2)
            state["trades"] = state.get("trades", 0) + 1
            state["wins"] = state.get("wins", 0) + (1 if r > 0 else 0)
            state["losses"] = state.get("losses", 0) + (1 if r < 0 else 0)
            _log_trade({
                "closed_at": now, "opened_at": pos.get("opened_at"), "side": side,
                "entry": entry, "exit": exit_price, "stop_loss": stop,
                "take_profit": tp, "r_multiple": r, "pnl_usd": pnl,
                "exit_reason": exit_reason, "gss_at_entry": pos.get("gss"),
            })
            state["position"] = None
            if notify:
                emoji = "✅" if r > 0 else "🛑"
                notify(
                    f"{emoji} <b>PAPER CLOSE — {side}</b> ({exit_reason})\n"
                    f"entry {_fmt(entry)} → exit {_fmt(exit_price)}  "
                    f"<b>{r:+.2f}R</b> (${pnl:+,.2f})\n"
                    f"paper total: {state['realized_r']:+.2f}R "
                    f"(${state['realized_usd']:+,.2f}) · "
                    f"{state['wins']}W/{state['losses']}L")
            pos = None

    # 2) open from a fresh actionable decision (one position at a time)
    if (decision and state.get("position") is None
            and (decision.get("decision") or "").upper() in ("LONG", "SHORT")):
        side = decision["decision"].upper()
        stop = float(decision.get("stop_loss") or 0)
        tp = float(decision.get("take_profit_1") or 0)
        size = float(decision.get("position_size_pct") or 0)
        if isinstance(price, (int, float)) and price > 0 and stop > 0 and tp > 0 and size > 0:
            # sanity: stop/tp on the correct side of entry
            ok = (stop < price < tp) if side == "LONG" else (tp < price < stop)
            if ok:
                state["position"] = {
                    "side": side, "entry": round(float(price), 2),
                    "stop_loss": round(stop, 2), "take_profit": round(tp, 2),
                    "size_pct": size, "opened_at": now,
                    "gss": (snapshot.get("gss", {}) or {}).get("total_score"),
                }
                if notify:
                    risk = abs(price - stop)
                    rr = abs(tp - price) / risk if risk else 0
                    notify(
                        f"📕 <b>PAPER OPEN — {side}</b>\n"
                        f"entry {_fmt(price)} · SL {_fmt(stop)} · TP {_fmt(tp)} "
                        f"· {rr:.1f}R target · size {size}%\n"
                        f"<i>paper forward-test — no real order.</i>")

    _save_state(state)
    return state
