"""
LiveMonitorEmitter — bot-side producer for the interactive live-monitor pop-up.

Writes a single consolidated JSON snapshot at `data/metrics/live_monitor_state.json`
containing everything the standalone `scripts/live_monitor.py` Tkinter window
renders: account, symbols, signals, positions, journal, errors, bot health.

Design:
  - Non-blocking — every call is wrapped in try/except; bot never crashes here.
  - Atomic writes — temp-file + rename so the consumer never reads half a file.
  - Ring buffers (deque) for signals / errors / trade-close events.
  - Zero extra deps — stdlib only (json, pathlib, threading, logging, time).

The consumer is expected to tolerate any missing field (graceful degrade).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional


def _j(val: Any) -> Any:
    """JSON-safe coercion for Decimal/datetime/misc objects."""
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, (list, tuple)):
        return [_j(v) for v in val]
    if isinstance(val, dict):
        return {k: _j(v) for k, v in val.items()}
    return val


def _friendly_error(msg: str) -> str:
    """Translate engineer-jargon errors into plain English for non-technical users."""
    m = msg.lower()
    if "connection" in m or "timeout" in m or "heartbeat" in m:
        return "Connection to MT5 is unstable — the bot is trying to reconnect."
    if "kill switch" in m:
        return "Kill switch ACTIVE — trading halted. Check risk settings."
    if "drawdown" in m:
        return "Drawdown limit reached — trading paused to protect the account."
    if "daily loss" in m:
        return "Daily loss limit reached — no new trades until tomorrow."
    if "symbol" in m and "not found" in m:
        return "A symbol is missing from your MT5 account. Check broker symbol names."
    if "margin" in m:
        return "Not enough free margin to open new trades."
    if "news blackout" in m or "news filter" in m:
        return "High-impact news nearby — signals are being suppressed."
    if "kalman" in m and ("nan" in m or "invalid" in m):
        return "Kalman regime filter hit bad data — skipping this bar."
    return msg[:140]


class _RingBufferHandler(logging.Handler):
    """Captures ERROR/CRITICAL log lines into the emitter's error ring buffer."""

    def __init__(self, emitter: "LiveMonitorEmitter"):
        super().__init__(level=logging.WARNING)
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname
            msg = record.getMessage()
            if level in ("WARNING", "ERROR", "CRITICAL"):
                self.emitter.record_error(level, msg)
        except Exception:
            pass  # never propagate errors from the handler


class LiveMonitorEmitter:
    """Produces the live-monitor JSON state file consumed by scripts/live_monitor.py."""

    def __init__(
        self,
        state_file: str = "data/metrics/live_monitor_state.json",
        config_file: str = "",
        env: str = "live",
    ):
        self.state_path = Path(state_file)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_file = config_file
        self.env = env
        self.started_at = datetime.now(timezone.utc)
        self._lock = threading.Lock()

        # Ring buffers
        self._signals: deque = deque(maxlen=30)
        self._errors: deque = deque(maxlen=20)
        self._trade_closes: deque = deque(maxlen=20)

        # Derived status
        self._status_state = "STARTING"
        self._status_message = "Initialising trading system..."

        # Throttle snapshot writes to avoid disk thrash (1 Hz is plenty for UI)
        self._last_write_ts = 0.0
        self._min_write_interval_sec = 1.0

        self._log_handler: Optional[_RingBufferHandler] = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def install_log_handler(self) -> None:
        """Attach a root-logger handler so every ERROR/CRITICAL propagates into the UI."""
        try:
            self._log_handler = _RingBufferHandler(self)
            logging.getLogger().addHandler(self._log_handler)
        except Exception:
            pass

    def shutdown(self, message: str = "Bot stopped") -> None:
        self._status_state = "STOPPED"
        self._status_message = message
        try:
            self._flush()
        except Exception:
            pass
        try:
            if self._log_handler is not None:
                logging.getLogger().removeHandler(self._log_handler)
        except Exception:
            pass

    # ── event recorders ───────────────────────────────────────────────

    def record_signal(
        self,
        strategy: str,
        symbol: str,
        side: str,
        confidence: float = 0.0,
        price: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        status: str = "RECEIVED",
        reason: str = "",
    ) -> None:
        """Append a signal to the ring buffer. Safe to call from the hot loop."""
        try:
            with self._lock:
                self._signals.appendleft({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "strategy": strategy,
                    "symbol": symbol,
                    "side": side,
                    "confidence": round(float(confidence or 0), 1),
                    "price": round(float(price or 0), 5),
                    "sl": round(float(sl or 0), 5),
                    "tp": round(float(tp or 0), 5),
                    "status": status,         # RECEIVED | FIRED | VETOED | ERROR
                    "reason": reason[:200],
                })
        except Exception:
            pass

    def mark_last_signal(self, status: str, reason: str = "") -> None:
        """Update the outcome of the most recently-received signal."""
        try:
            with self._lock:
                if self._signals:
                    self._signals[0]["status"] = status
                    if reason:
                        self._signals[0]["reason"] = reason[:200]
        except Exception:
            pass

    def record_error(self, level: str, msg: str) -> None:
        try:
            with self._lock:
                self._errors.appendleft({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "level": level,
                    "msg": msg[:240],
                    "friendly": _friendly_error(msg),
                })
        except Exception:
            pass

    def record_trade_close(
        self,
        strategy: str,
        symbol: str,
        side: str,
        entry: float,
        exit_price: float,
        pnl: float,
        sl: float = 0.0,
        tp: float = 0.0,
        exit_reason: str = "unknown",
        psychology: str = "",
    ) -> None:
        try:
            with self._lock:
                self._trade_closes.appendleft({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "strategy": strategy,
                    "symbol": symbol,
                    "side": side,
                    "entry": round(float(entry or 0), 5),
                    "exit": round(float(exit_price or 0), 5),
                    "pnl": round(float(pnl or 0), 2),
                    "sl": round(float(sl or 0), 5),
                    "tp": round(float(tp or 0), 5),
                    "exit_reason": exit_reason,
                    "psychology": psychology[:240],
                })
        except Exception:
            pass

    def set_status(self, state: str, message: str) -> None:
        """state ∈ {RUNNING, PAUSED, HALTED, ERROR, STARTING, STOPPED}"""
        self._status_state = state
        self._status_message = message

    # ── snapshot writer ───────────────────────────────────────────────

    def write_snapshot(self, trading_system, force: bool = False) -> None:
        """
        Collect state from the running TradingSystem and write the JSON file.

        Safe to call every loop iteration — internally throttled to 1 Hz.
        """
        now_ts = time.time()
        if not force and (now_ts - self._last_write_ts) < self._min_write_interval_sec:
            return

        try:
            snap = self._build_snapshot(trading_system)
            self._flush(snap)
            self._last_write_ts = now_ts
        except Exception as e:
            # swallow — monitor emission must never break the bot
            try:
                self.record_error("WARNING", f"live_monitor_emitter: {e}")
            except Exception:
                pass

    # ── internals ─────────────────────────────────────────────────────

    def _build_snapshot(self, ts) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        account = self._collect_account(ts)
        symbols = self._collect_symbols(ts)
        positions = self._collect_positions(ts)
        strategies = self._collect_strategies(ts)
        session = self._collect_session(ts)
        news = self._collect_news(ts)
        performance = self._collect_performance_stats()
        journal = self._collect_journal_from_csv()

        # Auto-derive status if the bot is healthy / halted
        state, message = self._derive_status(ts, account)

        with self._lock:
            signals = list(self._signals)
            errors = list(self._errors)
            trade_closes = list(self._trade_closes)

        # Overlay live trade-close events on top of CSV journal (live events first)
        combined_journal = trade_closes + [
            j for j in journal
            if not any(
                tc.get("symbol") == j.get("symbol") and
                tc.get("ts", "")[:19] == j.get("ts_close", "")[:19]
                for tc in trade_closes
            )
        ]

        return {
            "updated_at": now.isoformat(),
            "bot": {
                "running": bool(getattr(ts, "running", False)),
                "loop_iteration": int(getattr(ts, "loop_iteration", 0)),
                "uptime_seconds": int((now - self.started_at).total_seconds()),
                "env": self.env,
                "config_file": self.config_file,
            },
            "status": {
                "state": state,
                "message": message,
                "color": self._color_for_state(state),
            },
            "account": account,
            "symbols": symbols,
            "positions": positions,
            "signals": signals,
            "journal": combined_journal[:15],
            "strategies": strategies,
            "session": session,
            "news": news,
            "performance": performance,
            "errors": errors,
        }

    # ---- collectors ----

    def _collect_account(self, ts) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "initial_capital": 0.0, "balance": 0.0, "equity": 0.0,
            "margin": 0.0, "free_margin": 0.0,
            "return_usd": 0.0, "return_pct": 0.0,
            "daily_pnl": 0.0, "daily_loss_limit_used_pct": 0.0,
            "daily_loss_limit_used_usd": 0.0, "daily_loss_limit_usd": 0.0,
            "drawdown_used_pct": 0.0,
            "drawdown_used_usd": 0.0, "drawdown_limit_usd": 0.0,
            "open_positions": 0,
            "broker": "",
        }
        try:
            cfg = getattr(ts, "config", {}) or {}
            init_bal = float(cfg.get("account", {}).get("initial_balance", 0) or 0)
            out["initial_capital"] = init_bal
            out["broker"] = cfg.get("account", {}).get("broker", "") or cfg.get("broker", "")
        except Exception:
            pass

        try:
            info = ts.connector.get_account_info() if ts.connector else {}
            bal = float(info.get("balance", 0) or 0)
            eq = float(info.get("equity", 0) or 0)
            if bal > 0:
                out["balance"] = bal
            if eq > 0:
                out["equity"] = eq
            out["margin"] = float(info.get("margin", 0) or 0)
            out["free_margin"] = float(info.get("free_margin", 0) or 0)
            if not out["broker"]:
                out["broker"] = str(info.get("company", "") or info.get("server", ""))
        except Exception:
            pass

        try:
            if ts.portfolio_engine:
                stats = ts.portfolio_engine.get_statistics()
                out["open_positions"] = int(stats.get("total_positions", 0))
        except Exception:
            pass

        try:
            daily = float(ts._get_daily_pnl())
            out["daily_pnl"] = round(daily, 2)
        except Exception:
            pass

        # ── Derive "% of daily loss limit used" and "% of drawdown used"
        # directly from config + live account state to avoid coupling to the
        # full RiskMetrics snapshot (which needs positions + balance args).
        try:
            cfg = getattr(ts, "config", {}) or {}
            risk_cfg = cfg.get("risk", {}) or {}
            max_daily_pct = float(risk_cfg.get("max_daily_loss_pct", 0.025) or 0.025)
            max_dd_pct = float(risk_cfg.get("max_drawdown_pct", 0.07) or 0.07)

            base = out["balance"] or out["initial_capital"] or 0.0
            if base > 0:
                daily_loss_abs = -out["daily_pnl"] if out["daily_pnl"] < 0 else 0.0
                daily_limit = max_daily_pct * base
                out["daily_loss_limit_usd"] = round(daily_limit, 2)
                out["daily_loss_limit_used_usd"] = round(daily_loss_abs, 2)
                if daily_limit > 0:
                    out["daily_loss_limit_used_pct"] = round(
                        min(100.0, daily_loss_abs / daily_limit * 100.0), 2
                    )

                # Drawdown is only meaningful once we have a live equity read.
                if out["equity"] > 0:
                    hwm = None
                    try:
                        hwm = float(ts.risk_engine.equity_high_water_mark) if ts.risk_engine else None
                    except Exception:
                        hwm = None
                    if not hwm or hwm <= 0:
                        hwm = max(out["equity"], base)
                    dd_abs = max(0.0, hwm - out["equity"])
                    dd_limit = max_dd_pct * hwm
                    out["drawdown_limit_usd"] = round(dd_limit, 2)
                    out["drawdown_used_usd"] = round(dd_abs, 2)
                    if dd_limit > 0:
                        out["drawdown_used_pct"] = round(
                            min(100.0, dd_abs / dd_limit * 100.0), 2
                        )
        except Exception:
            pass

        if out["initial_capital"] > 0 and out["equity"] > 0:
            ret = out["equity"] - out["initial_capital"]
            out["return_usd"] = round(ret, 2)
            out["return_pct"] = round(ret / out["initial_capital"] * 100, 3)

        return out

    def _collect_symbols(self, ts) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        try:
            cfg_syms = (getattr(ts, "config", {}) or {}).get("symbols", {}) or {}
        except Exception:
            cfg_syms = {}

        for ticker, cfg in cfg_syms.items():
            row = {
                "ticker": ticker,
                "enabled": bool(cfg.get("enabled", False)),
                "bid": 0.0, "ask": 0.0, "spread": 0.0,
                "regime": "UNKNOWN", "direction": "FLAT", "atr_pct": 0.0,
            }
            # live tick — prefer the cached DataEngine tick, fall back to connector
            try:
                tick = None
                eng = getattr(ts, "data_engine", None)
                if eng is not None and hasattr(eng, "get_latest_tick"):
                    tick = eng.get_latest_tick(ticker)
                if tick is None and ts.connector is not None and hasattr(ts.connector, "get_current_tick"):
                    tick = ts.connector.get_current_tick(ticker)
                if tick is not None:
                    if isinstance(tick, dict):
                        bid = float(tick.get("bid", 0) or 0)
                        ask = float(tick.get("ask", 0) or 0)
                    else:
                        bid = float(getattr(tick, "bid", 0) or 0)
                        ask = float(getattr(tick, "ask", 0) or 0)
                    row["bid"] = round(bid, 5)
                    row["ask"] = round(ask, 5)
                    row["spread"] = round(ask - bid, 5)
            except Exception:
                pass

            # regime from override file
            try:
                base = ticker.split(".")[0].upper()
                override_path = Path(f"data/config_override_{base}.json")
                if override_path.exists():
                    with open(override_path) as f:
                        od = json.load(f)
                    row["regime"] = od.get("regime", "UNKNOWN")
            except Exception:
                pass

            # direction + ATR% from candles if available
            try:
                eng = getattr(ts, "data_engine", None)
                if eng is not None:
                    for tf in ("5m", "15m", "1h"):
                        bars = eng.get_bars(ticker, tf)
                        if bars is not None and len(bars) >= 20:
                            last_close = float(bars["close"].iloc[-1])
                            prev_close = float(bars["close"].iloc[-20])
                            if last_close > prev_close * 1.001:
                                row["direction"] = "UP"
                            elif last_close < prev_close * 0.999:
                                row["direction"] = "DOWN"
                            else:
                                row["direction"] = "FLAT"
                            # rough ATR% from high-low range
                            hl = (bars["high"] - bars["low"]).tail(14).mean()
                            if last_close > 0:
                                row["atr_pct"] = round(float(hl / last_close * 100), 3)
                            break
            except Exception:
                pass

            rows.append(row)
        return rows

    def _collect_positions(self, ts) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            positions = ts.connector.get_positions() if ts.connector else {}
        except Exception:
            positions = {}
        for pid, pos in (positions or {}).items():
            try:
                sym_obj = getattr(pos, "symbol", None)
                sym_tkr = getattr(sym_obj, "ticker", None) or str(sym_obj) if sym_obj else "?"
                side_obj = getattr(pos, "side", "?")
                side = getattr(side_obj, "value", None) or getattr(side_obj, "name", None) or str(side_obj)
                meta = getattr(pos, "metadata", {}) or {}
                out.append({
                    "ticket": str(meta.get("mt5_ticket", pid)),
                    "symbol": sym_tkr,
                    "side": str(side).upper(),
                    "qty": float(getattr(pos, "quantity", 0) or 0),
                    "entry": float(getattr(pos, "entry_price", 0) or 0),
                    "current": float(getattr(pos, "current_price", 0) or 0),
                    "sl": float(getattr(pos, "stop_loss", 0) or 0),
                    "tp": float(getattr(pos, "take_profit", 0) or 0),
                    "pnl": float(getattr(pos, "unrealized_pnl", 0) or 0),
                    "strategy": str(meta.get("strategy", "")),
                    "opened_at": str(getattr(pos, "entry_time", "") or ""),
                })
            except Exception:
                continue
        return out

    def _collect_strategies(self, ts) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        try:
            cfg = (getattr(ts, "config", {}) or {}).get("strategies", {}) or {}
        except Exception:
            cfg = {}

        # try to pull current regime weights from the first enabled symbol
        weights: Dict[str, float] = {}
        try:
            override = getattr(ts, "_regime_override", None) or {}
            weights = override.get("weights", {}) or {}
        except Exception:
            pass

        for name, c in cfg.items():
            if not isinstance(c, dict):
                continue
            if name in ("min_bars_required", "primary_timeframe"):
                continue
            rows.append({
                "name": name,
                "enabled": bool(c.get("enabled", False)),
                "timeframe": c.get("timeframe", "5m"),
                "weight": round(float(weights.get(name, 1.0) or 1.0), 2),
            })
        rows.sort(key=lambda r: (not r["enabled"], r["name"]))
        return rows

    def _collect_session(self, ts) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "active_name": "",
            "news_blackout": False,
            "regime_generated_at": "",
            "time_left_min": None,
            "all": [],
        }

        now = datetime.now(timezone.utc)
        now_hhmm = now.strftime("%H:%M")

        # Pull session config directly — cheap and gives us the full list.
        try:
            sessions_cfg = (
                (getattr(ts, "config", {}) or {})
                .get("trading_hours", {})
                .get("sessions", []) or []
            )
        except Exception:
            sessions_cfg = []

        def _hhmm_to_min(s: str) -> int:
            try:
                hh, mm = s.split(":")
                return int(hh) * 60 + int(mm)
            except Exception:
                return 0

        now_min = now.hour * 60 + now.minute
        active_name = ""

        for s in sessions_cfg:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name", "") or "session")
            start = s.get("start", "00:00")
            end = s.get("end", "23:59")
            enabled = bool(s.get("enabled", True))

            active = False
            if enabled:
                if start <= end:
                    active = start <= now_hhmm < end
                else:  # cross-midnight window (e.g. asia 22:00–07:00)
                    active = now_hhmm >= start or now_hhmm < end

            # Minutes remaining in this session (only meaningful when active)
            mins_left = None
            if active:
                end_min = _hhmm_to_min(end)
                if start <= end:
                    mins_left = max(0, end_min - now_min)
                else:
                    mins_left = ((end_min - now_min) % (24 * 60))
                if not active_name:
                    active_name = name
                    out["time_left_min"] = mins_left

            out["all"].append({
                "name": name,
                "start": start,
                "end": end,
                "enabled": enabled,
                "active": active,
                "lot_mult": float(s.get("lot_size_multiplier", 1.0) or 1.0),
                "strategies": list(s.get("strategies", []) or []),
                "mins_left": mins_left,
            })

        out["active_name"] = active_name

        # News blackout + regime metadata (unchanged)
        try:
            sm = getattr(ts, "_session_mgr", None)
            state = getattr(sm, "state", None) if sm else None
            out["news_blackout"] = bool(getattr(state, "news_blackout", False)) if state else False
        except Exception:
            pass
        try:
            override = getattr(ts, "_regime_override", None) or {}
            out["regime_generated_at"] = override.get("generated_at", "")
        except Exception:
            pass
        return out

    def _collect_news(self, ts) -> Dict[str, Any]:
        """Pull news-filter status and upcoming high-impact events, emitted in IST."""
        ist_offset = timedelta(hours=5, minutes=30)
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + ist_offset
        out: Dict[str, Any] = {
            "ist_now": now_ist.strftime("%H:%M:%S"),
            "ist_date": now_ist.strftime("%a %d %b %Y"),
            "utc_now": now_utc.strftime("%H:%M:%S"),
            "blackout": False,
            "blackout_reason": "",
            "upcoming": [],
        }

        sm = getattr(ts, "_session_mgr", None)
        try:
            state = getattr(sm, "state", None) if sm else None
            if state is not None:
                out["blackout"] = bool(getattr(state, "news_blackout", False))
                out["blackout_reason"] = str(
                    getattr(state, "news_blackout_reason", "") or ""
                )
        except Exception:
            pass

        # Try several common attribute names for the upcoming-events cache.
        events: List[Any] = []
        try:
            for attr in ("upcoming_events", "upcoming_news", "news_events", "events"):
                ev = getattr(sm, attr, None) if sm else None
                if ev:
                    events = list(ev)
                    break
            if not events and sm is not None:
                nf = getattr(sm, "news_filter", None)
                if nf is not None:
                    for attr in ("upcoming_events", "upcoming_news", "events"):
                        ev = getattr(nf, attr, None)
                        if ev:
                            events = list(ev)
                            break
        except Exception:
            events = []

        def _to_dt(v: Any) -> Optional[datetime]:
            if v is None:
                return None
            if isinstance(v, datetime):
                return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
            if isinstance(v, str):
                try:
                    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    return None
            return None

        for e in events[:20]:
            try:
                if isinstance(e, dict):
                    t = _to_dt(
                        e.get("time_utc") or e.get("time") or e.get("datetime")
                        or e.get("timestamp")
                    )
                    impact = str(e.get("impact", "") or "").upper()
                    currency = str(e.get("currency", "") or e.get("ccy", ""))
                    title = str(e.get("title", "") or e.get("event", "") or e.get("name", ""))
                else:
                    t = _to_dt(
                        getattr(e, "time_utc", None) or getattr(e, "time", None)
                        or getattr(e, "datetime", None) or getattr(e, "timestamp", None)
                    )
                    impact = str(getattr(e, "impact", "") or "").upper()
                    currency = str(getattr(e, "currency", "") or getattr(e, "ccy", ""))
                    title = str(
                        getattr(e, "title", "") or getattr(e, "event", "")
                        or getattr(e, "name", "")
                    )
                if t is None:
                    continue
                mins_until = int((t - now_utc).total_seconds() / 60)
                if mins_until < -15:
                    continue
                t_ist = t + ist_offset
                out["upcoming"].append({
                    "time_ist": t_ist.strftime("%H:%M"),
                    "date_ist": t_ist.strftime("%d %b"),
                    "impact": impact or "MED",
                    "currency": currency or "",
                    "title": title[:70],
                    "mins_until": mins_until,
                })
            except Exception:
                continue

        out["upcoming"].sort(key=lambda r: r["mins_until"])
        out["upcoming"] = out["upcoming"][:6]
        return out

    def _collect_performance_stats(self) -> Dict[str, Any]:
        """Derive live trading stats (win-rate, PF, streak, expectancy) from journal CSV."""
        out: Dict[str, Any] = {
            "total_trades": 0, "wins": 0, "losses": 0, "scratches": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0,
            "current_streak": 0, "streak_type": "",
            "total_pnl": 0.0,
        }
        path = Path("data/logs/trade_journal.csv")
        if not path.exists():
            return out

        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                seek_from = max(0, size - 200 * 1024)
                f.seek(seek_from)
                tail = f.read().decode("utf-8", errors="replace")
            with open(path, "r", encoding="utf-8") as f:
                header_line = f.readline().strip()
        except Exception:
            return out

        if not header_line:
            return out
        header = [h.strip() for h in header_line.split(",")]
        try:
            pnl_idx = header.index("realized_pnl")
        except ValueError:
            return out

        lines = [l for l in tail.splitlines() if l.strip()]
        if not lines:
            return out
        # Skip first (possibly partial) line when we seeked mid-file
        data_lines = lines[1:] if seek_from > 0 else (
            lines[1:] if lines[0] == header_line else lines
        )

        pnls: List[float] = []
        for ln in data_lines:
            parts = ln.split(",")
            if len(parts) <= pnl_idx:
                continue
            try:
                pnls.append(float(parts[pnl_idx] or 0))
            except Exception:
                continue

        if not pnls:
            return out

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        scratches = [p for p in pnls if p == 0]

        out["total_trades"] = len(pnls)
        out["wins"] = len(wins)
        out["losses"] = len(losses)
        out["scratches"] = len(scratches)
        out["total_pnl"] = round(sum(pnls), 2)
        if pnls:
            decided = len(wins) + len(losses)
            out["win_rate"] = round(len(wins) / decided * 100, 1) if decided else 0.0
        if wins:
            out["avg_win"] = round(sum(wins) / len(wins), 2)
            out["best_trade"] = round(max(wins), 2)
        if losses:
            out["avg_loss"] = round(sum(losses) / len(losses), 2)
            out["worst_trade"] = round(min(losses), 2)

        gross_win = sum(wins)
        gross_loss = -sum(losses)
        if gross_loss > 0:
            out["profit_factor"] = round(gross_win / gross_loss, 2)
        elif gross_win > 0:
            out["profit_factor"] = 999.0

        wr = out["win_rate"] / 100.0
        out["expectancy"] = round(wr * out["avg_win"] - (1 - wr) * abs(out["avg_loss"]), 2)

        # Current streak from the end of the trade list
        streak = 0
        stype = ""
        for p in reversed(pnls):
            if p > 0:
                if stype in ("", "W"):
                    stype = "W"; streak += 1
                else:
                    break
            elif p < 0:
                if stype in ("", "L"):
                    stype = "L"; streak += 1
                else:
                    break
            else:
                continue  # scratches do not break a streak
        out["current_streak"] = streak
        out["streak_type"] = stype
        return out

    def _collect_journal_from_csv(self) -> List[Dict[str, Any]]:
        """Read last 15 closed trades directly from the journal CSV (cheap)."""
        path = Path("data/logs/trade_journal.csv")
        if not path.exists():
            return []
        try:
            # Read only last ~20 KB to avoid loading entire file
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                seek_from = max(0, size - 20 * 1024)
                f.seek(seek_from)
                tail = f.read().decode("utf-8", errors="replace")
        except Exception:
            return []

        lines = [l for l in tail.splitlines() if l.strip()]
        if len(lines) < 2:
            return []

        # Header: find the first line with expected keywords
        header_line = None
        # If we seeked mid-file, discard the first (likely partial) line
        data_lines = lines[1:] if seek_from > 0 else lines[1:] if "," in lines[0] else lines
        if seek_from == 0:
            header_line = lines[0]
            data_lines = lines[1:]
        else:
            # Pull real header from the very top of the file
            try:
                with open(path, "r", encoding="utf-8") as f:
                    header_line = f.readline().strip()
            except Exception:
                return []

        header = [h.strip() for h in header_line.split(",")]
        rows = []
        for ln in data_lines[-20:]:
            parts = ln.split(",")
            if len(parts) < len(header):
                continue
            rec = dict(zip(header, parts[:len(header)]))
            try:
                pnl = float(rec.get("realized_pnl", 0) or 0)
            except Exception:
                pnl = 0.0
            try:
                entry = float(rec.get("entry_price", 0) or 0)
            except Exception:
                entry = 0.0
            try:
                exit_p = float(rec.get("exit_price", 0) or 0)
            except Exception:
                exit_p = 0.0

            strategy = rec.get("strategy", "")
            exit_reason = rec.get("exit_reason", "unknown")
            psychology = self._psychology_for(strategy, rec.get("side", ""), exit_reason, pnl)

            rows.append({
                "ts_close": rec.get("exit_time", "") or rec.get("entry_time", ""),
                "strategy": strategy,
                "symbol": rec.get("symbol", ""),
                "side": (rec.get("side", "") or "").upper(),
                "entry": round(entry, 5),
                "exit": round(exit_p, 5),
                "pnl": round(pnl, 2),
                "sl": 0.0,
                "tp": 0.0,
                "exit_reason": exit_reason,
                "psychology": psychology,
            })
        rows.reverse()  # most recent first
        return rows[:15]

    @staticmethod
    def _psychology_for(strategy: str, side: str, exit_reason: str, pnl: float) -> str:
        """Generate a short plain-English 'why we took this trade'."""
        s = (strategy or "").lower()
        side = (side or "").upper()
        base = {
            "kalman_regime": "Kalman trend filter + OU mean-reversion combo",
            "breakout": "Donchian breakout with higher-timeframe confirmation",
            "mean_reversion": "OU z-score extreme — fade away from the mean",
            "momentum": "Short-term ROC + ADX momentum kicker",
            "vwap": "Deviation from 30-period VWAP — revert to fair value",
            "mini_medallion": "Composite of 10 weak alphas — threshold ±3σ",
            "structure_break_retest": "Break of structure then retest + rejection",
            "fibonacci_retracement": "Pullback into the 50–61.8% Golden Zone",
            "descending_channel_breakout": "Descending channel + HL shift → breakout",
            "smc_ob_strategy": "ICT order-block: formed → sweep → entry",
            "supply_demand": "Retest of a fresh supply/demand zone post-impulse",
            "asia_range_fade": "Fade the Asia low-volatility range (UTC 09–14)",
        }
        why = base.get(s, f"{s or 'strategy'} signal")
        direction = "long" if side == "LONG" else ("short" if side == "SHORT" else side.lower())
        outcome = "closed at profit" if pnl > 0 else ("stopped out" if pnl < 0 else "closed flat")
        reason_text = exit_reason.replace("_", " ")
        return f"{why}; entered {direction}; {outcome} ({reason_text})"

    # ---- status derivation ----

    def _derive_status(self, ts, account: Dict[str, Any]):
        # explicit override wins
        if self._status_state not in ("", "STARTING"):
            # keep latest unless bot is clearly running healthily
            pass

        try:
            ks_active = bool(ts.risk_engine.kill_switch.is_active()) if ts.risk_engine else False
        except Exception:
            ks_active = False

        if ks_active:
            return "HALTED", "Kill switch active — trading stopped. Inspect risk/state files."

        # Check error ring for CRITICAL in last 60s
        try:
            now = datetime.now(timezone.utc)
            for e in list(self._errors)[:5]:
                if e.get("level") == "CRITICAL":
                    ets = datetime.fromisoformat(e["ts"])
                    if (now - ets).total_seconds() < 60:
                        return "ERROR", e.get("friendly", "Critical error — check logs.")
        except Exception:
            pass

        if not getattr(ts, "running", False):
            return self._status_state, self._status_message

        daily_used = float(account.get("daily_loss_limit_used_pct", 0) or 0)
        dd_used = float(account.get("drawdown_used_pct", 0) or 0)
        if daily_used >= 100:
            return "PAUSED", "Daily loss limit reached — paused until tomorrow."
        if dd_used >= 100:
            return "HALTED", "Max drawdown reached — account protection engaged."
        if daily_used >= 75 or dd_used >= 75:
            return "RUNNING", f"Caution: {max(daily_used, dd_used):.0f}% of a risk limit used."
        return "RUNNING", "All systems operational."

    @staticmethod
    def _color_for_state(state: str) -> str:
        return {
            "RUNNING": "green",
            "STARTING": "blue",
            "PAUSED": "yellow",
            "HALTED": "red",
            "ERROR": "red",
            "STOPPED": "gray",
        }.get(state, "gray")

    # ---- atomic write ----

    def _flush(self, snap: Optional[Dict[str, Any]] = None) -> None:
        if snap is None:
            # emit a minimal lifecycle snapshot
            snap = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "status": {"state": self._status_state, "message": self._status_message,
                           "color": self._color_for_state(self._status_state)},
                "bot": {"running": False, "env": self.env, "config_file": self.config_file},
            }
        tmp = self.state_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_j(snap), f, separators=(",", ":"))
        os.replace(tmp, self.state_path)
