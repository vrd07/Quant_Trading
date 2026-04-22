#!/usr/bin/env python3
"""
Live Trading Monitor — interactive pop-up window.

Polls `data/metrics/live_monitor_state.json` (produced by LiveMonitorEmitter)
and renders a compact dashboard designed to sit next to MT5:

  ┌─────────────── STATUS ───────────────┐
  │ ● RUNNING   Balance / Equity / P&L   │
  ├──────────────────────────────────────┤
  │ SYMBOLS · SIGNALS · POSITIONS        │
  │ JOURNAL (psychology) · ERRORS        │
  └──────────────────────────────────────┘

Designed for non-technical users:
  - Big color pill for bot state (green/yellow/red).
  - Plain-English error banner when something is wrong.
  - Live price ticks, regime, and market direction per symbol.
  - Trade journal shows "why we took this trade" per row.

Usage:
  python scripts/live_monitor.py                      # default 1-sec refresh
  python scripts/live_monitor.py --refresh 500        # refresh every 500 ms
  python scripts/live_monitor.py --no-topmost         # do not pin on top

Dependencies: Python stdlib only (tkinter is built-in).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Optional


# ── Color palette (TradingView-ish dark) ────────────────────────────────────
BG          = "#0B0E1A"
BG_PANEL    = "#141826"
BG_PANEL_2  = "#1B2030"
BORDER      = "#2A2F40"
TEXT        = "#E6E8EF"
TEXT_DIM    = "#8A93A6"
TEXT_FAINT  = "#606673"
GREEN       = "#00D68F"    # profits, long, up
RED         = "#FF4757"    # losses, short, down
GOLD        = "#FFB800"    # XAUUSD highlight / accent
YELLOW      = "#FFD166"    # warnings
BLUE        = "#38BDF8"    # info
PURPLE      = "#A78BFA"    # regime TREND
CYAN        = "#22D3EE"    # regime RANGE
ORANGE      = "#FB923C"    # regime VOLATILE

COLOR_BY_STATE = {
    "RUNNING":   GREEN,
    "STARTING":  BLUE,
    "PAUSED":    YELLOW,
    "HALTED":    RED,
    "ERROR":     RED,
    "STOPPED":   TEXT_FAINT,
    "UNKNOWN":   TEXT_FAINT,
}

COLOR_BY_REGIME = {
    "TREND":    PURPLE,
    "RANGE":    CYAN,
    "VOLATILE": ORANGE,
    "UNKNOWN":  TEXT_DIM,
}


def _fmt_money(val: float, signed: bool = False, dec: int = 2) -> str:
    try:
        if signed:
            return f"{val:+,.{dec}f}"
        return f"{val:,.{dec}f}"
    except Exception:
        return "—"


def _fmt_pct(val: float, signed: bool = True) -> str:
    try:
        return f"{val:+.2f}%" if signed else f"{val:.2f}%"
    except Exception:
        return "—"


def _fmt_uptime(seconds: int) -> str:
    try:
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}h {m:02d}m"
        return f"{m}m {s:02d}s"
    except Exception:
        return "—"


def _fmt_ts(iso: str) -> str:
    if not iso:
        return ""
    return iso[11:19] if len(iso) >= 19 else iso


# ─────────────────────────────────────────────────────────────────────────────
class LiveMonitorApp:
    def __init__(self, state_file: str, refresh_ms: int = 1000, topmost: bool = True):
        self.state_file = Path(state_file)
        self.refresh_ms = max(250, int(refresh_ms))

        self.root = tk.Tk()
        self.root.title("Quant Bot — Live Monitor")
        self.root.geometry("1080x780")
        self.root.minsize(920, 640)
        self.root.configure(bg=BG)
        try:
            self.root.attributes("-topmost", bool(topmost))
        except Exception:
            pass

        self._configure_style()
        self._build_layout()

        self._last_loaded: Dict[str, Any] = {}
        self._tick_blink = False
        self._missed_reads = 0

        self.root.after(50, self._tick)

    # ── style ──────────────────────────────────────────────────────────────
    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=BG_PANEL)
        style.configure("Panel2.TFrame", background=BG_PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Menlo", 11))
        style.configure("Panel.TLabel", background=BG_PANEL, foreground=TEXT, font=("Menlo", 11))
        style.configure("Panel.Dim.TLabel", background=BG_PANEL, foreground=TEXT_DIM, font=("Menlo", 10))
        style.configure("Panel.Heading.TLabel",
                        background=BG_PANEL, foreground=GOLD,
                        font=("Menlo", 11, "bold"))

        # Treeview — used for positions, signals, journal, errors
        style.configure("Mono.Treeview",
                        background=BG_PANEL_2,
                        fieldbackground=BG_PANEL_2,
                        foreground=TEXT,
                        rowheight=22,
                        borderwidth=0,
                        font=("Menlo", 10))
        style.configure("Mono.Treeview.Heading",
                        background=BORDER, foreground=GOLD,
                        font=("Menlo", 10, "bold"))
        style.map("Mono.Treeview",
                  background=[("selected", BORDER)],
                  foreground=[("selected", TEXT)])

    # ── layout ─────────────────────────────────────────────────────────────
    def _build_layout(self) -> None:
        # Top banner
        self.top_frame = tk.Frame(self.root, bg=BG, pady=8, padx=12)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)
        self._build_top_banner(self.top_frame)

        # Error banner (hidden until an error appears)
        self.error_banner = tk.Frame(self.root, bg=RED, height=0)
        self.error_banner.pack(side=tk.TOP, fill=tk.X)
        self.error_banner_label = tk.Label(
            self.error_banner, text="", bg=RED, fg="#0B0E1A",
            font=("Menlo", 12, "bold"), anchor="w", padx=14, pady=8,
        )
        self.error_banner_label.pack(fill=tk.X)
        self.error_banner.pack_forget()

        # Main body: 2-column grid
        body = tk.Frame(self.root, bg=BG, padx=12, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        body.grid_columnconfigure(0, weight=1, uniform="cols")
        body.grid_columnconfigure(1, weight=1, uniform="cols")
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(2, weight=1)

        # Row 0: account snapshot (spans both cols)
        self.account_panel = self._make_panel(body, "ACCOUNT & RISK")
        self.account_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        self._build_account_body(self.account_panel)

        # Row 1: symbols (left) + signals (right)
        self.symbols_panel = self._make_panel(body, "MARKET & SYMBOLS")
        self.symbols_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        self._build_symbols_body(self.symbols_panel)

        self.signals_panel = self._make_panel(body, "LIVE SIGNALS")
        self.signals_panel.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(0, 8))
        self._build_signals_body(self.signals_panel)

        # Row 2: positions + journal
        self.positions_panel = self._make_panel(body, "OPEN POSITIONS")
        self.positions_panel.grid(row=2, column=0, sticky="nsew", padx=(0, 4))
        self._build_positions_body(self.positions_panel)

        self.journal_panel = self._make_panel(body, "TRADE JOURNAL & PSYCHOLOGY")
        self.journal_panel.grid(row=2, column=1, sticky="nsew", padx=(4, 0))
        self._build_journal_body(self.journal_panel)

        # Footer
        footer = tk.Frame(self.root, bg=BG_PANEL_2, padx=10, pady=4)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.footer_left = tk.Label(footer, text="", bg=BG_PANEL_2, fg=TEXT_DIM,
                                    font=("Menlo", 9), anchor="w")
        self.footer_left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.footer_right = tk.Label(footer, text="", bg=BG_PANEL_2, fg=TEXT_DIM,
                                     font=("Menlo", 9), anchor="e")
        self.footer_right.pack(side=tk.RIGHT)

    def _make_panel(self, parent, title: str) -> tk.Frame:
        panel = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER,
                         highlightcolor=BORDER, highlightthickness=1, bd=0)
        header = tk.Frame(panel, bg=BG_PANEL)
        header.pack(fill=tk.X, padx=10, pady=(8, 0))
        tk.Label(header, text=title, bg=BG_PANEL, fg=GOLD,
                 font=("Menlo", 10, "bold")).pack(side=tk.LEFT)
        sep = tk.Frame(panel, bg=BORDER, height=1)
        sep.pack(fill=tk.X, padx=10, pady=(4, 6))
        return panel

    # --- top banner ---
    def _build_top_banner(self, parent) -> None:
        # Left: status pill + status message
        left = tk.Frame(parent, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)

        pill_row = tk.Frame(left, bg=BG)
        pill_row.pack(anchor="w")
        self.status_dot = tk.Label(pill_row, text="●", bg=BG, fg=TEXT_FAINT,
                                   font=("Menlo", 18, "bold"))
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_label = tk.Label(pill_row, text="STARTING", bg=BG, fg=TEXT,
                                     font=("Menlo", 17, "bold"))
        self.status_label.pack(side=tk.LEFT)
        self.status_sub = tk.Label(left, text="Connecting to bot…", bg=BG,
                                   fg=TEXT_DIM, font=("Menlo", 11))
        self.status_sub.pack(anchor="w", pady=(2, 0))

        # Right: balance / equity / P&L
        right = tk.Frame(parent, bg=BG)
        right.pack(side=tk.RIGHT)

        def kv(label, init="—", color=TEXT, big=False) -> tk.Label:
            col = tk.Frame(right, bg=BG)
            col.pack(side=tk.LEFT, padx=10)
            tk.Label(col, text=label, bg=BG, fg=TEXT_DIM,
                     font=("Menlo", 9, "bold")).pack(anchor="e")
            lbl = tk.Label(col, text=init, bg=BG, fg=color,
                           font=("Menlo", 16 if big else 14, "bold"))
            lbl.pack(anchor="e")
            return lbl

        self.val_balance = kv("BALANCE")
        self.val_equity = kv("EQUITY", big=True)
        self.val_daily = kv("TODAY P&L")
        self.val_total = kv("TOTAL P&L")
        self.val_positions = kv("OPEN")

    # --- account body ---
    def _build_account_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=12, pady=(0, 10))
        body.pack(fill=tk.X)

        row = tk.Frame(body, bg=BG_PANEL)
        row.pack(fill=tk.X)

        def kv(parent, label, init="—", color=TEXT) -> tk.Label:
            col = tk.Frame(parent, bg=BG_PANEL)
            col.pack(side=tk.LEFT, padx=(0, 18))
            tk.Label(col, text=label, bg=BG_PANEL, fg=TEXT_DIM,
                     font=("Menlo", 9, "bold")).pack(anchor="w")
            lbl = tk.Label(col, text=init, bg=BG_PANEL, fg=color,
                           font=("Menlo", 12, "bold"))
            lbl.pack(anchor="w")
            return lbl

        self.val_broker = kv(row, "BROKER / ACCOUNT")
        self.val_init_cap = kv(row, "INITIAL CAPITAL")
        self.val_margin = kv(row, "MARGIN USED")
        self.val_free_margin = kv(row, "FREE MARGIN")
        self.val_return_pct = kv(row, "RETURN %")
        self.val_uptime = kv(row, "BOT UPTIME")

        # risk bars row
        bar_row = tk.Frame(body, bg=BG_PANEL, pady=8)
        bar_row.pack(fill=tk.X)

        self.bar_daily = self._make_risk_bar(bar_row, "Daily loss used")
        self.bar_drawdown = self._make_risk_bar(bar_row, "Drawdown used")

    def _make_risk_bar(self, parent, label: str) -> Dict[str, Any]:
        wrap = tk.Frame(parent, bg=BG_PANEL)
        wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 20))
        header = tk.Frame(wrap, bg=BG_PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text=label, bg=BG_PANEL, fg=TEXT_DIM,
                 font=("Menlo", 9, "bold")).pack(side=tk.LEFT)
        val_lbl = tk.Label(header, text="0%", bg=BG_PANEL, fg=TEXT,
                           font=("Menlo", 10, "bold"))
        val_lbl.pack(side=tk.RIGHT)
        canvas = tk.Canvas(wrap, height=10, bg=BG_PANEL_2,
                           highlightthickness=0, bd=0)
        canvas.pack(fill=tk.X, pady=(3, 0))
        fill = canvas.create_rectangle(0, 0, 0, 10, fill=GREEN, width=0)
        return {"canvas": canvas, "fill": fill, "val": val_lbl}

    def _set_risk_bar(self, bar: Dict[str, Any], pct: float) -> None:
        pct = max(0.0, min(100.0, float(pct or 0)))
        bar["val"].config(text=f"{pct:.0f}%")
        w = bar["canvas"].winfo_width() or 1
        bar["canvas"].coords(bar["fill"], 0, 0, int(w * pct / 100), 10)
        color = GREEN if pct < 60 else (YELLOW if pct < 85 else RED)
        bar["canvas"].itemconfigure(bar["fill"], fill=color)

    # --- symbols body ---
    def _build_symbols_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=(0, 8))
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("sym", "bid", "ask", "spread", "regime", "dir", "atr")
        headings = ("Symbol", "Bid", "Ask", "Spread", "Regime", "Direction", "ATR %")
        widths = (90, 80, 80, 70, 90, 90, 70)
        self.tree_symbols = self._make_tree(body, cols, headings, widths, height=5)
        self.tree_symbols.pack(fill=tk.BOTH, expand=True)

        self.tree_symbols.tag_configure("up", foreground=GREEN)
        self.tree_symbols.tag_configure("down", foreground=RED)
        self.tree_symbols.tag_configure("flat", foreground=TEXT_DIM)
        self.tree_symbols.tag_configure("disabled", foreground=TEXT_FAINT)

    # --- signals body ---
    def _build_signals_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=(0, 8))
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("ts", "strat", "sym", "side", "conf", "status", "reason")
        headings = ("Time", "Strategy", "Symbol", "Side", "Conf", "Status", "Reason")
        widths = (70, 140, 80, 55, 55, 80, 280)
        self.tree_signals = self._make_tree(body, cols, headings, widths, height=8)
        self.tree_signals.pack(fill=tk.BOTH, expand=True)
        self.tree_signals.tag_configure("fired", foreground=GREEN)
        self.tree_signals.tag_configure("vetoed", foreground=YELLOW)
        self.tree_signals.tag_configure("received", foreground=TEXT)
        self.tree_signals.tag_configure("error", foreground=RED)

    # --- positions body ---
    def _build_positions_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=(0, 8))
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("sym", "side", "strat", "qty", "entry", "now", "sl", "tp", "pnl")
        headings = ("Symbol", "Side", "Strategy", "Qty", "Entry",
                    "Current", "S/L", "T/P", "P&L ($)")
        widths = (80, 55, 130, 55, 80, 80, 80, 80, 90)
        self.tree_positions = self._make_tree(body, cols, headings, widths, height=6)
        self.tree_positions.pack(fill=tk.BOTH, expand=True)
        self.tree_positions.tag_configure("win", foreground=GREEN)
        self.tree_positions.tag_configure("loss", foreground=RED)
        self.tree_positions.tag_configure("flat", foreground=TEXT)

    # --- journal body ---
    def _build_journal_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=(0, 8))
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("ts", "sym", "strat", "side", "entry", "exit", "pnl", "reason")
        headings = ("Closed", "Symbol", "Strategy", "Side",
                    "Entry", "Exit", "P&L", "Why / Exit")
        widths = (70, 80, 130, 55, 80, 80, 80, 300)
        self.tree_journal = self._make_tree(body, cols, headings, widths, height=6)
        self.tree_journal.pack(fill=tk.BOTH, expand=True)
        self.tree_journal.tag_configure("win", foreground=GREEN)
        self.tree_journal.tag_configure("loss", foreground=RED)

        # "Errors" sub-section below
        err_hdr = tk.Frame(panel, bg=BG_PANEL)
        err_hdr.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(err_hdr, text="RECENT WARNINGS / ERRORS", bg=BG_PANEL,
                 fg=GOLD, font=("Menlo", 10, "bold")).pack(side=tk.LEFT)
        err_sep = tk.Frame(panel, bg=BORDER, height=1)
        err_sep.pack(fill=tk.X, padx=10, pady=(4, 4))

        err_body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=(0, 8))
        err_body.pack(fill=tk.BOTH)
        err_cols = ("ts", "level", "friendly")
        err_head = ("Time", "Level", "Message")
        err_widths = (70, 80, 520)
        self.tree_errors = self._make_tree(err_body, err_cols, err_head, err_widths, height=4)
        self.tree_errors.pack(fill=tk.X)
        self.tree_errors.tag_configure("critical", foreground=RED)
        self.tree_errors.tag_configure("warning", foreground=YELLOW)
        self.tree_errors.tag_configure("info", foreground=TEXT_DIM)

    def _make_tree(self, parent, cols, headings, widths, height=6) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=cols, show="headings",
                            height=height, style="Mono.Treeview")
        for c, h, w in zip(cols, headings, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w", stretch=(c == cols[-1]))
        return tree

    # ── polling & rendering ────────────────────────────────────────────────
    def _load(self) -> Optional[Dict[str, Any]]:
        try:
            if not self.state_file.exists():
                return None
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _tick(self) -> None:
        data = self._load()
        if data is not None:
            self._missed_reads = 0
            self._last_loaded = data
            try:
                self._render(data)
            except Exception as e:
                # never crash the UI
                self.status_sub.config(text=f"render error: {e}", fg=YELLOW)
        else:
            self._missed_reads += 1
            if self._missed_reads == 3:
                self._set_status_no_data()

        self._tick_blink = not self._tick_blink
        self.root.after(self.refresh_ms, self._tick)

    def _set_status_no_data(self) -> None:
        self.status_dot.config(fg=TEXT_FAINT)
        self.status_label.config(text="NO DATA", fg=TEXT_FAINT)
        self.status_sub.config(
            text=f"Waiting for {self.state_file.name} — is the bot running?",
            fg=YELLOW,
        )
        self.error_banner.pack_forget()

    def _render(self, d: Dict[str, Any]) -> None:
        # ---- status ----
        status = d.get("status", {}) or {}
        state = status.get("state", "UNKNOWN")
        color = COLOR_BY_STATE.get(state, TEXT_FAINT)

        # Blink dot when running
        dot_color = color
        if state in ("STARTING", "RUNNING") and self._tick_blink:
            dot_color = BG_PANEL_2
        self.status_dot.config(fg=dot_color)
        self.status_label.config(text=state, fg=color)
        self.status_sub.config(text=status.get("message", ""), fg=TEXT_DIM)

        # ---- top-banner KPIs ----
        acc = d.get("account", {}) or {}
        self.val_balance.config(text=f"${_fmt_money(acc.get('balance', 0))}")
        self.val_equity.config(text=f"${_fmt_money(acc.get('equity', 0))}")

        daily = float(acc.get("daily_pnl", 0) or 0)
        self.val_daily.config(
            text=f"${_fmt_money(daily, signed=True)}",
            fg=(GREEN if daily > 0 else RED if daily < 0 else TEXT),
        )
        total = float(acc.get("return_usd", 0) or 0)
        self.val_total.config(
            text=f"${_fmt_money(total, signed=True)}",
            fg=(GREEN if total > 0 else RED if total < 0 else TEXT),
        )
        self.val_positions.config(
            text=str(acc.get("open_positions", 0) or 0),
            fg=(GOLD if (acc.get("open_positions", 0) or 0) > 0 else TEXT),
        )

        # ---- account detail row ----
        self.val_broker.config(text=(acc.get("broker", "") or "—")[:32])
        self.val_init_cap.config(text=f"${_fmt_money(acc.get('initial_capital', 0))}")
        self.val_margin.config(text=f"${_fmt_money(acc.get('margin', 0))}")
        self.val_free_margin.config(text=f"${_fmt_money(acc.get('free_margin', 0))}")
        ret_pct = float(acc.get("return_pct", 0) or 0)
        self.val_return_pct.config(
            text=_fmt_pct(ret_pct),
            fg=(GREEN if ret_pct > 0 else RED if ret_pct < 0 else TEXT),
        )
        self.val_uptime.config(text=_fmt_uptime((d.get("bot", {}) or {}).get("uptime_seconds", 0)))

        self._set_risk_bar(self.bar_daily, acc.get("daily_loss_limit_used_pct", 0))
        self._set_risk_bar(self.bar_drawdown, acc.get("drawdown_used_pct", 0))

        # ---- error banner ----
        errs = d.get("errors", []) or []
        critical_active = (state in ("HALTED", "ERROR")) or (
            errs and errs[0].get("level") == "CRITICAL"
        )
        if critical_active and errs:
            msg = errs[0].get("friendly") or errs[0].get("msg", "Unknown error")
            self.error_banner_label.config(text=f"⚠  {msg}")
            self.error_banner.pack(side=tk.TOP, fill=tk.X, after=self.top_frame)
        else:
            self.error_banner.pack_forget()

        # ---- symbols ----
        self._clear(self.tree_symbols)
        for s in d.get("symbols", []) or []:
            if not s.get("enabled", False):
                tag = "disabled"
            else:
                direction = (s.get("direction") or "FLAT").upper()
                tag = {"UP": "up", "DOWN": "down"}.get(direction, "flat")
            arrow = {"UP": "▲", "DOWN": "▼"}.get((s.get("direction") or "").upper(), "■")
            self.tree_symbols.insert("", "end", values=(
                s.get("ticker", "—"),
                _fmt_money(s.get("bid", 0), dec=3),
                _fmt_money(s.get("ask", 0), dec=3),
                _fmt_money(s.get("spread", 0), dec=4),
                (s.get("regime") or "UNKNOWN"),
                f"{arrow}  {(s.get('direction') or 'FLAT')}",
                f"{float(s.get('atr_pct', 0) or 0):.2f}",
            ), tags=(tag,))

        # ---- signals ----
        self._clear(self.tree_signals)
        for sig in (d.get("signals", []) or [])[:20]:
            status_v = (sig.get("status", "") or "").upper()
            tag = {"FIRED": "fired", "VETOED": "vetoed",
                   "ERROR": "error"}.get(status_v, "received")
            self.tree_signals.insert("", "end", values=(
                _fmt_ts(sig.get("ts", "")),
                (sig.get("strategy") or "")[:20],
                sig.get("symbol") or "",
                (sig.get("side") or "")[:5].upper(),
                f"{float(sig.get('confidence', 0) or 0):.0f}",
                status_v or "RCVD",
                (sig.get("reason") or "")[:80],
            ), tags=(tag,))

        # ---- positions ----
        self._clear(self.tree_positions)
        for p in d.get("positions", []) or []:
            pnl = float(p.get("pnl", 0) or 0)
            tag = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
            self.tree_positions.insert("", "end", values=(
                p.get("symbol") or "",
                p.get("side") or "",
                (p.get("strategy") or "")[:18],
                f"{float(p.get('qty', 0) or 0):.2f}",
                _fmt_money(p.get("entry", 0), dec=3),
                _fmt_money(p.get("current", 0), dec=3),
                _fmt_money(p.get("sl", 0), dec=3) if p.get("sl") else "—",
                _fmt_money(p.get("tp", 0), dec=3) if p.get("tp") else "—",
                _fmt_money(pnl, signed=True),
            ), tags=(tag,))

        # ---- journal ----
        self._clear(self.tree_journal)
        for j in (d.get("journal", []) or [])[:15]:
            pnl = float(j.get("pnl", 0) or 0)
            tag = "win" if pnl > 0 else "loss"
            self.tree_journal.insert("", "end", values=(
                _fmt_ts(j.get("ts_close", "")),
                j.get("symbol") or "",
                (j.get("strategy") or "")[:18],
                (j.get("side") or "")[:5],
                _fmt_money(j.get("entry", 0), dec=3),
                _fmt_money(j.get("exit", 0), dec=3),
                _fmt_money(pnl, signed=True),
                (j.get("psychology") or j.get("exit_reason") or "")[:90],
            ), tags=(tag,))

        # ---- errors ----
        self._clear(self.tree_errors)
        for e in (errs or [])[:10]:
            lvl = (e.get("level") or "INFO").upper()
            tag = {"CRITICAL": "critical", "ERROR": "critical",
                   "WARNING": "warning"}.get(lvl, "info")
            self.tree_errors.insert("", "end", values=(
                _fmt_ts(e.get("ts", "")),
                lvl,
                (e.get("friendly") or e.get("msg") or "")[:180],
            ), tags=(tag,))

        # ---- footer ----
        bot = d.get("bot", {}) or {}
        sess = d.get("session", {}) or {}
        left_bits = [
            f"cfg={Path(bot.get('config_file', '')).name or '?'}",
            f"env={bot.get('env', '?')}",
            f"iter={bot.get('loop_iteration', 0)}",
        ]
        if sess.get("name"):
            left_bits.append(f"session={sess.get('name')}")
        if sess.get("news_blackout"):
            left_bits.append("NEWS BLACKOUT")
        self.footer_left.config(text="   ·   ".join(left_bits))
        self.footer_right.config(text=f"updated {_fmt_ts(d.get('updated_at', ''))}  ·  Ctrl-Q to quit")

    @staticmethod
    def _clear(tree: ttk.Treeview) -> None:
        for iid in tree.get_children():
            tree.delete(iid)

    # ── run ────────────────────────────────────────────────────────────────
    def run(self) -> None:
        # Ctrl-Q / Cmd-Q to close
        self.root.bind("<Control-q>", lambda _e: self.root.destroy())
        self.root.bind("<Command-q>", lambda _e: self.root.destroy())
        self.root.mainloop()


def main() -> int:
    p = argparse.ArgumentParser(description="Live trading bot monitor (pop-up).")
    p.add_argument("--state-file", default="data/metrics/live_monitor_state.json",
                   help="Path to live monitor JSON produced by the bot.")
    p.add_argument("--refresh", type=int, default=1000,
                   help="Refresh interval in milliseconds (default: 1000)")
    p.add_argument("--no-topmost", action="store_true",
                   help="Do not pin the window on top of other windows.")
    args = p.parse_args()

    root = Path(__file__).resolve().parent.parent
    state = args.state_file
    if not os.path.isabs(state):
        state = str(root / state)

    app = LiveMonitorApp(state_file=state, refresh_ms=args.refresh,
                         topmost=not args.no_topmost)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
