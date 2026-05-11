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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Optional

IST = timezone(timedelta(hours=5, minutes=30))


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


def _fmt_ts_ist(iso: str) -> str:
    """Convert a UTC ISO-8601 timestamp to IST HH:MM:SS."""
    if not iso:
        return ""
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime("%H:%M:%S")
    except Exception:
        return iso[11:19] if len(iso) >= 19 else iso


# ─────────────────────────────────────────────────────────────────────────────
class LiveMonitorApp:
    def __init__(self, state_file: str, refresh_ms: int = 1000, topmost: bool = True):
        self.state_file = Path(state_file)
        self.refresh_ms = max(250, int(refresh_ms))

        self.root = tk.Tk()
        self.root.title("Quant Bot — Live Monitor")
        self.root.geometry("1320x1040")
        self.root.minsize(1120, 880)
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
        body.grid_rowconfigure(0, weight=0)   # account (fixed)
        body.grid_rowconfigure(1, weight=0)   # sessions | news  (fixed)
        body.grid_rowconfigure(2, weight=2, minsize=180)  # symbols | signals — pinned so MARKET & SYMBOLS tree stays visible
        body.grid_rowconfigure(3, weight=0)   # performance | positions (small, fixed)
        body.grid_rowconfigure(4, weight=4, minsize=260)  # journal — guaranteed min height
        body.grid_rowconfigure(5, weight=0, minsize=95)   # errors — guaranteed min height

        # Row 0: account snapshot (spans both cols)
        self.account_panel = self._make_panel(body, "ACCOUNT & RISK")
        self.account_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        self._build_account_body(self.account_panel)

        # Row 1: sessions (left) + news IST (right)
        self.sessions_panel = self._make_panel(body, "TRADING SESSIONS")
        self.sessions_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(0, 6))
        self._build_sessions_body(self.sessions_panel)

        self.news_panel = self._make_panel(body, "MARKET NEWS (IST)")
        self.news_panel.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(0, 6))
        self._build_news_body(self.news_panel)

        # Row 2: symbols (left) + signals (right)
        self.symbols_panel = self._make_panel(body, "MARKET & SYMBOLS")
        self.symbols_panel.grid(row=2, column=0, sticky="nsew", padx=(0, 4), pady=(0, 6))
        self._build_symbols_body(self.symbols_panel)

        self.signals_panel = self._make_panel(body, "LIVE SIGNALS")
        self.signals_panel.grid(row=2, column=1, sticky="nsew", padx=(4, 0), pady=(0, 6))
        self._build_signals_body(self.signals_panel)

        # Row 3: performance metrics (left) + open positions (right, small)
        self.performance_panel = self._make_panel(body, "PERFORMANCE METRICS")
        self.performance_panel.grid(row=3, column=0, sticky="nsew", padx=(0, 4), pady=(0, 6))
        self._build_performance_body(self.performance_panel)

        self.positions_panel = self._make_panel(body, "OPEN POSITIONS")
        self.positions_panel.grid(row=3, column=1, sticky="nsew", padx=(4, 0), pady=(0, 6))
        self._build_positions_body(self.positions_panel)

        # Row 4: trade journal (spans both, huge)
        self.journal_panel = self._make_panel(body, "TRADE JOURNAL & PSYCHOLOGY")
        self.journal_panel.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        self._build_journal_body(self.journal_panel)

        # Row 5: errors (spans both, tiny)
        self.errors_panel = self._make_panel(body, "RECENT WARNINGS / ERRORS")
        self.errors_panel.grid(row=5, column=0, columnspan=2, sticky="nsew")
        self._build_errors_body(self.errors_panel)

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
        # Three-column grid: status pill (left) | centered user+quote | KPIs (right).
        # Weights 2:3:3 give the right-hand KPI column the same space as the centre
        # column so all five values render; the username's centre point shifts ~80 px
        # left compared to a 1:2:1 split.
        parent.grid_columnconfigure(0, weight=2, uniform="banner")
        parent.grid_columnconfigure(1, weight=3, uniform="banner")
        parent.grid_columnconfigure(2, weight=3, uniform="banner")

        # ── Column 0: state pill + sub-message ────────────────────────────
        left = tk.Frame(parent, bg=BG)
        left.grid(row=0, column=0, sticky="nw")

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

        # ── Column 1: centered trader name + quote (sits on the RUNNING row) ─
        center = tk.Frame(parent, bg=BG)
        center.grid(row=0, column=1, sticky="n")

        self.user_label = tk.Label(
            center, text="", bg=BG, fg=GOLD,
            font=("Menlo", 20, "bold"), anchor="center", justify="center",
        )
        self.user_label.pack(anchor="center")
        self.quote_label = tk.Label(
            center, text="", bg=BG, fg=YELLOW,
            font=("Menlo", 10, "italic"), anchor="center", justify="center",
            wraplength=600,
        )
        self.quote_label.pack(anchor="center", pady=(2, 0))
        self.quote_author_label = tk.Label(
            center, text="", bg=BG, fg=TEXT_FAINT,
            font=("Menlo", 9), anchor="center", justify="center",
        )
        self.quote_author_label.pack(anchor="center")

        # ── Column 2: balance / equity / P&L KPIs ─────────────────────────
        right = tk.Frame(parent, bg=BG)
        right.grid(row=0, column=2, sticky="ne")

        def kv(label, init="—", color=TEXT, big=False) -> tk.Label:
            col = tk.Frame(right, bg=BG)
            col.pack(side=tk.LEFT, padx=5)
            tk.Label(col, text=label, bg=BG, fg=TEXT_DIM,
                     font=("Menlo", 8, "bold")).pack(anchor="e")
            lbl = tk.Label(col, text=init, bg=BG, fg=color,
                           font=("Menlo", 13 if big else 11, "bold"))
            lbl.pack(anchor="e")
            return lbl

        self.val_balance = kv("BALANCE")
        self.val_equity = kv("EQUITY", big=True)
        self.val_daily = kv("TODAY P&L")
        self.val_total = kv("TOTAL P&L")
        self.val_positions = kv("OPEN")

    # --- account body ---
    def _build_account_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=12, pady=5)
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

    def _set_risk_bar(
        self,
        bar: Dict[str, Any],
        pct: float,
        used_usd: Optional[float] = None,
        limit_usd: Optional[float] = None,
    ) -> None:
        pct = max(0.0, min(100.0, float(pct or 0)))
        label = f"{pct:.0f}%"
        if used_usd is not None and limit_usd is not None and limit_usd > 0:
            label = (
                f"{pct:.0f}%  "
                f"(${_fmt_money(used_usd)} / ${_fmt_money(limit_usd)})"
            )
        elif used_usd is not None:
            label = f"{pct:.0f}%  (${_fmt_money(used_usd)})"
        bar["val"].config(text=label)
        w = bar["canvas"].winfo_width() or 1
        bar["canvas"].coords(bar["fill"], 0, 0, int(w * pct / 100), 10)
        color = GREEN if pct < 60 else (YELLOW if pct < 85 else RED)
        bar["canvas"].itemconfigure(bar["fill"], fill=color)

    # --- sessions body ---
    def _build_sessions_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
        body.pack(fill=tk.BOTH, expand=True)

        # Top line: current session + countdown
        hdr = tk.Frame(body, bg=BG_PANEL)
        hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(hdr, text="ACTIVE:", bg=BG_PANEL, fg=TEXT_DIM,
                 font=("Menlo", 9, "bold")).pack(side=tk.LEFT)
        self.session_active_lbl = tk.Label(hdr, text="—", bg=BG_PANEL, fg=TEXT,
                                           font=("Menlo", 11, "bold"))
        self.session_active_lbl.pack(side=tk.LEFT, padx=(6, 14))

        tk.Label(hdr, text="ENDS IN:", bg=BG_PANEL, fg=TEXT_DIM,
                 font=("Menlo", 9, "bold")).pack(side=tk.LEFT)
        self.session_countdown_lbl = tk.Label(hdr, text="—", bg=BG_PANEL, fg=TEXT,
                                              font=("Menlo", 11, "bold"))
        self.session_countdown_lbl.pack(side=tk.LEFT, padx=(6, 0))

        self.session_utc_lbl = tk.Label(hdr, text="—", bg=BG_PANEL, fg=GOLD,
                                        font=("Menlo", 11, "bold"))
        self.session_utc_lbl.pack(side=tk.RIGHT)
        tk.Label(hdr, text="UTC NOW:", bg=BG_PANEL, fg=TEXT_DIM,
                 font=("Menlo", 9, "bold")).pack(side=tk.RIGHT, padx=(0, 6))

        # Table of all configured sessions (compact: narrower cols, height=4)
        cols = ("active", "name", "window", "lot", "strats")
        headings = ("", "Session", "UTC Window", "Lot ×", "Strategies")
        widths = (24, 100, 110, 55, 240)
        self.tree_sessions = self._make_tree(body, cols, headings, widths, height=4)
        self.tree_sessions.pack(fill=tk.BOTH, expand=True)
        self.tree_sessions.tag_configure("active", foreground=GREEN,
                                         background=BG_PANEL_2)
        self.tree_sessions.tag_configure("idle", foreground=TEXT_DIM)
        self.tree_sessions.tag_configure("disabled", foreground=TEXT_FAINT)

    # --- news body (IST) ---
    def _build_news_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)

        # Header: clock glyph + IST time/date on left, status chip on right.
        hdr = tk.Frame(body, bg=BG_PANEL)
        hdr.pack(fill=tk.X, pady=(0, 8))

        left = tk.Frame(hdr, bg=BG_PANEL)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="◷", bg=BG_PANEL, fg=GOLD,
                 font=("Menlo", 14)).pack(side=tk.LEFT, padx=(0, 6))
        self.news_ist_lbl = tk.Label(left, text="—", bg=BG_PANEL, fg=GOLD,
                                     font=("Menlo", 12, "bold"))
        self.news_ist_lbl.pack(side=tk.LEFT)

        self.news_chip = tk.Label(hdr, text="  NEWS CLEAR  ", bg=BG_PANEL_2, fg=TEXT_DIM,
                                  font=("Menlo", 9, "bold"), padx=4, pady=3)
        self.news_chip.pack(side=tk.RIGHT)

        # Scrollable cards container — canvas + inner frame. Cards are CREATED
        # ONCE and reused; each tick only call .config() / pack_forget() on
        # existing widgets so the panel never blinks (no destroy/recreate).
        canvas_wrap = tk.Frame(body, bg=BG_PANEL)
        canvas_wrap.pack(fill=tk.X, expand=False)

        self.news_canvas = tk.Canvas(
            canvas_wrap, bg=BG_PANEL,
            highlightthickness=0, bd=0, height=150,
        )
        news_scroll = ttk.Scrollbar(
            canvas_wrap, orient="vertical", command=self.news_canvas.yview,
        )
        self.news_canvas.configure(yscrollcommand=news_scroll.set)
        news_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.news_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.news_cards_frame = tk.Frame(self.news_canvas, bg=BG_PANEL)
        self._news_canvas_window = self.news_canvas.create_window(
            (0, 0), window=self.news_cards_frame, anchor="nw",
        )

        def _on_frame_configure(_e):
            self.news_canvas.configure(scrollregion=self.news_canvas.bbox("all"))

        def _on_canvas_configure(e):
            self.news_canvas.itemconfig(self._news_canvas_window, width=e.width)

        self.news_cards_frame.bind("<Configure>", _on_frame_configure)
        self.news_canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.news_canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e):
            self.news_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            self.news_canvas.bind_all("<Button-4>", _on_mousewheel)
            self.news_canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(_e):
            self.news_canvas.unbind_all("<MouseWheel>")
            self.news_canvas.unbind_all("<Button-4>")
            self.news_canvas.unbind_all("<Button-5>")

        self.news_canvas.bind("<Enter>", _bind_wheel)
        self.news_canvas.bind("<Leave>", _unbind_wheel)

        self._news_card_pool: list = []
        self._news_empty_lbl = tk.Label(
            self.news_cards_frame,
            text="✓  No high-impact events ahead",
            bg=BG_PANEL, fg=TEXT_DIM,
            font=("Menlo", 10, "italic"), pady=14,
        )

    def _make_news_card(self) -> Dict[str, Any]:
        outer = tk.Frame(self.news_cards_frame, bg=BG_PANEL, pady=3)
        stripe = tk.Frame(outer, bg=BG_PANEL_2, width=4)
        stripe.pack(side=tk.LEFT, fill=tk.Y)
        card = tk.Frame(outer, bg=BG_PANEL_2, padx=10, pady=6)
        card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        top = tk.Frame(card, bg=BG_PANEL_2)
        top.pack(fill=tk.X)
        title_lbl = tk.Label(top, text="—", bg=BG_PANEL_2, fg=TEXT,
                             font=("Menlo", 10, "bold"), anchor="w")
        title_lbl.pack(side=tk.LEFT)
        ccy_lbl = tk.Label(top, text=" — ", bg=GOLD, fg=BG,
                           font=("Menlo", 8, "bold"), padx=4)
        ccy_lbl.pack(side=tk.RIGHT)

        bot = tk.Frame(card, bg=BG_PANEL_2)
        bot.pack(fill=tk.X, pady=(4, 0))
        time_lbl = tk.Label(bot, text="⏱ — IST", bg=BG_PANEL_2, fg=GOLD,
                            font=("Menlo", 9, "bold"))
        time_lbl.pack(side=tk.LEFT, padx=(0, 8))
        impact_lbl = tk.Label(bot, text=" MED ", bg=YELLOW, fg=BG,
                              font=("Menlo", 8, "bold"), padx=4)
        impact_lbl.pack(side=tk.LEFT)
        urgency_lbl = tk.Label(bot, text="", bg=RED, fg=BG,
                               font=("Menlo", 8, "bold"), padx=4)
        # urgency_lbl is packed/unpacked dynamically — initially hidden.
        countdown_lbl = tk.Label(bot, text="—", bg=BG_PANEL_2, fg=TEXT_DIM,
                                 font=("Menlo", 10, "bold"))
        countdown_lbl.pack(side=tk.RIGHT)

        return {
            "outer": outer, "stripe": stripe, "bot": bot,
            "title": title_lbl, "ccy": ccy_lbl,
            "time": time_lbl, "impact": impact_lbl,
            "urgency": urgency_lbl, "countdown": countdown_lbl,
            "impact_widget_str": str(impact_lbl),
        }

    def _render_news_cards(self, upcoming: list) -> None:
        """Update the persistent card widgets in place — no destroy/recreate."""
        if not upcoming:
            for c in self._news_card_pool:
                if c["outer"].winfo_ismapped():
                    c["outer"].pack_forget()
            if not self._news_empty_lbl.winfo_ismapped():
                self._news_empty_lbl.pack(fill=tk.X)
            return

        if self._news_empty_lbl.winfo_ismapped():
            self._news_empty_lbl.pack_forget()

        visible = upcoming
        for i, e in enumerate(visible):
            if i >= len(self._news_card_pool):
                self._news_card_pool.append(self._make_news_card())
            card = self._news_card_pool[i]
            if not card["outer"].winfo_ismapped():
                card["outer"].pack(fill=tk.X)

            mins = int(e.get("mins_until", 0) or 0)
            impact = (e.get("impact") or "").upper()
            if 0 <= mins <= 30:
                stripe, accent, urgency = RED, RED, "IMMINENT"
            elif mins < 0:
                stripe, accent, urgency = TEXT_FAINT, TEXT_DIM, "LIVE/PAST"
            elif impact == "HIGH":
                stripe, accent, urgency = ORANGE, ORANGE, ""
            elif impact == "MEDIUM":
                stripe, accent, urgency = YELLOW, YELLOW, ""
            else:
                stripe, accent, urgency = TEXT_FAINT, TEXT_DIM, ""

            if mins < 0:
                countdown = f"{abs(mins)}m ago"
            elif mins < 60:
                countdown = f"in {mins}m"
            else:
                h, m = divmod(mins, 60)
                countdown = f"in {h}h {m:02d}m"

            card["stripe"].config(bg=stripe)
            card["title"].config(text=(e.get("title") or "—")[:48])
            card["ccy"].config(text=f" {e.get('currency') or '—'} ")
            card["time"].config(text=f"⏱ {e.get('time_ist', '—')} IST")
            card["impact"].config(text=f" {impact or 'MED'} ", bg=accent)
            if urgency:
                card["urgency"].config(text=f" {urgency} ")
                if not card["urgency"].winfo_ismapped():
                    card["urgency"].pack(
                        in_=card["bot"], side=tk.LEFT, padx=(4, 0),
                        after=card["impact"],
                    )
            elif card["urgency"].winfo_ismapped():
                card["urgency"].pack_forget()
            card["countdown"].config(
                text=countdown,
                fg=accent if mins <= 30 else TEXT_DIM,
            )

        # Hide trailing unused cards (preserves order — we never re-pack mid-list).
        for j in range(len(visible), len(self._news_card_pool)):
            if self._news_card_pool[j]["outer"].winfo_ismapped():
                self._news_card_pool[j]["outer"].pack_forget()

    # --- performance metrics body (single compact row) ---
    def _build_performance_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=10, pady=4)
        body.pack(fill=tk.BOTH, expand=True)

        def kv(parent, label, init="—", color=TEXT) -> tk.Label:
            col = tk.Frame(parent, bg=BG_PANEL)
            col.pack(side=tk.LEFT, padx=(0, 8))
            tk.Label(col, text=label, bg=BG_PANEL, fg=TEXT_DIM,
                     font=("Menlo", 8, "bold")).pack(anchor="w")
            lbl = tk.Label(col, text=init, bg=BG_PANEL, fg=color,
                           font=("Menlo", 11, "bold"))
            lbl.pack(anchor="w")
            return lbl

        row1 = tk.Frame(body, bg=BG_PANEL)
        row1.pack(fill=tk.X)
        self.val_perf_trades = kv(row1, "TRADES")
        self.val_perf_winrate = kv(row1, "WR")
        self.val_perf_pf = kv(row1, "PF")
        self.val_perf_exp = kv(row1, "EXP")
        self.val_perf_avgwin = kv(row1, "AVG W")
        self.val_perf_avgloss = kv(row1, "AVG L")
        self.val_perf_streak = kv(row1, "STREAK")
        self.val_perf_totalpnl = kv(row1, "TOTAL")

    # --- symbols body ---
    def _build_symbols_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
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
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
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

    # --- positions body (compact, 2 visible + scroll for the rest) ---
    def _build_positions_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
        body.pack(fill=tk.X, expand=False)

        cols = ("sym", "side", "qty", "entry", "now", "sl", "tp", "pnl", "dur")
        headings = ("Symbol", "Side", "Qty", "Entry",
                    "Current", "S/L", "T/P", "P&L ($)", "Duration")
        widths = (70, 50, 50, 70, 70, 70, 70, 80, 80)
        self.tree_positions = self._make_tree(body, cols, headings, widths, height=2)
        pos_scroll = ttk.Scrollbar(body, orient="vertical",
                                   command=self.tree_positions.yview)
        self.tree_positions.configure(yscrollcommand=pos_scroll.set)
        pos_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_positions.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.tree_positions.tag_configure("win", foreground=GREEN)
        self.tree_positions.tag_configure("loss", foreground=RED)
        self.tree_positions.tag_configure("flat", foreground=TEXT)

    # --- journal body (huge — takes most of the vertical space) ---
    def _build_journal_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("ts", "ts_ist", "sym", "strat", "side", "entry", "exit", "pnl", "dur", "reason")
        headings = ("Closed (UTC)", "Closed (IST)", "Symbol", "Strategy", "Side",
                    "Entry", "Exit", "P&L", "Duration", "Why / Exit")
        widths = (90, 90, 75, 120, 50, 75, 75, 80, 75, 360)
        self.tree_journal = self._make_tree(body, cols, headings, widths, height=16)
        self.tree_journal.pack(fill=tk.BOTH, expand=True)
        self.tree_journal.tag_configure("win", foreground=GREEN)
        self.tree_journal.tag_configure("loss", foreground=RED)
        self.tree_journal.tag_configure("flat", foreground=TEXT)

    # --- errors body (compact standalone strip) ---
    def _build_errors_body(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=2)
        body.pack(fill=tk.BOTH, expand=True)

        cols = ("ts", "level", "friendly")
        headings = ("Time", "Level", "Message")
        widths = (70, 80, 900)
        self.tree_errors = self._make_tree(body, cols, headings, widths, height=3)
        self.tree_errors.pack(fill=tk.BOTH, expand=True)
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

        # ---- user profile (centered username strip with quote underneath) ----
        user = d.get("user", {}) or {}
        uname = str(user.get("username", "") or "").strip()
        quote = str(user.get("quote", "") or "").strip()
        author = str(user.get("author", "") or "").strip()
        self.user_label.config(text=uname)
        self.quote_label.config(text=(f"“{quote}”" if quote else ""))
        self.quote_author_label.config(text=(f"— {author}" if author else ""))

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

        self._set_risk_bar(
            self.bar_daily,
            acc.get("daily_loss_limit_used_pct", 0),
            used_usd=acc.get("daily_loss_limit_used_usd"),
            limit_usd=acc.get("daily_loss_limit_usd"),
        )
        self._set_risk_bar(
            self.bar_drawdown,
            acc.get("drawdown_used_pct", 0),
            used_usd=acc.get("drawdown_used_usd"),
            limit_usd=acc.get("drawdown_limit_usd"),
        )

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

        # ---- sessions ----
        sess = d.get("session", {}) or {}
        active_name = (sess.get("active_name") or "").strip()
        if active_name:
            self.session_active_lbl.config(text=active_name.upper(), fg=GREEN)
        else:
            self.session_active_lbl.config(text="NONE", fg=TEXT_FAINT)

        mins = sess.get("time_left_min")
        if isinstance(mins, (int, float)) and mins is not None:
            h, m = divmod(int(mins), 60)
            self.session_countdown_lbl.config(
                text=(f"{h}h {m:02d}m" if h > 0 else f"{m}m"),
                fg=(YELLOW if mins < 15 else TEXT),
            )
        else:
            self.session_countdown_lbl.config(text="—", fg=TEXT_FAINT)

        self.session_utc_lbl.config(
            text=datetime.now(timezone.utc).strftime("%H:%M:%S"),
            fg=GOLD,
        )

        self._clear(self.tree_sessions)
        for s in (sess.get("all") or []):
            if not s.get("enabled", True):
                tag = "disabled"
                marker = "○"
            elif s.get("active"):
                tag = "active"
                marker = "●"
            else:
                tag = "idle"
                marker = "·"
            strats = s.get("strategies") or []
            strats_txt = (
                ", ".join(strats) if len(strats) <= 6
                else f"{len(strats)} strategies"
            )
            self.tree_sessions.insert("", "end", values=(
                marker,
                (s.get("name") or "").upper(),
                f"{s.get('start', '?')} – {s.get('end', '?')}",
                f"{float(s.get('lot_mult', 1.0)):.2f}",
                strats_txt or "—",
            ), tags=(tag,))

        # ---- news (IST) ----
        news = d.get("news", {}) or {}
        ist_now = news.get("ist_now", "") or ""
        ist_date = news.get("ist_date", "") or ""
        self.news_ist_lbl.config(
            text=f"{ist_now}  ·  {ist_date}" if ist_date else ist_now or "—",
            fg=GOLD,
        )
        if news.get("blackout") or sess.get("news_blackout"):
            self.news_chip.config(text=" NEWS BLACKOUT ", bg=RED, fg=BG)
        else:
            self.news_chip.config(text=" NEWS CLEAR ", bg=BG_PANEL_2, fg=TEXT_DIM)

        self._render_news_cards(news.get("upcoming") or [])

        # ---- performance metrics ----
        perf = d.get("performance", {}) or {}
        total_trades = int(perf.get("total_trades", 0) or 0)
        wins = int(perf.get("wins", 0) or 0)
        losses = int(perf.get("losses", 0) or 0)
        wr = float(perf.get("win_rate", 0) or 0)
        pf = float(perf.get("profit_factor", 0) or 0)
        exp_v = float(perf.get("expectancy", 0) or 0)
        avg_w = float(perf.get("avg_win", 0) or 0)
        avg_l = float(perf.get("avg_loss", 0) or 0)
        total_pnl = float(perf.get("total_pnl", 0) or 0)
        streak = int(perf.get("current_streak", 0) or 0)
        stype = (perf.get("streak_type") or "").upper()

        self.val_perf_trades.config(text=f"{total_trades}  ({wins}W / {losses}L)")
        self.val_perf_winrate.config(
            text=f"{wr:.1f}%" if total_trades else "—",
            fg=(GREEN if wr >= 55 else YELLOW if wr >= 45 else RED) if total_trades else TEXT,
        )
        self.val_perf_pf.config(
            text=(f"{pf:.2f}" if pf < 900 else "∞") if total_trades else "—",
            fg=(GREEN if pf >= 1.5 else YELLOW if pf >= 1.0 else RED) if total_trades else TEXT,
        )
        self.val_perf_exp.config(
            text=f"${_fmt_money(exp_v, signed=True)}" if total_trades else "—",
            fg=(GREEN if exp_v > 0 else RED if exp_v < 0 else TEXT),
        )
        self.val_perf_avgwin.config(
            text=f"${_fmt_money(avg_w)}" if wins else "—", fg=GREEN if wins else TEXT,
        )
        self.val_perf_avgloss.config(
            text=f"${_fmt_money(avg_l)}" if losses else "—", fg=RED if losses else TEXT,
        )
        if streak and stype:
            streak_color = GREEN if stype == "W" else RED
            self.val_perf_streak.config(text=f"{streak}{stype}", fg=streak_color)
        else:
            self.val_perf_streak.config(text="—", fg=TEXT)
        self.val_perf_totalpnl.config(
            text=f"${_fmt_money(total_pnl, signed=True)}" if total_trades else "—",
            fg=(GREEN if total_pnl > 0 else RED if total_pnl < 0 else TEXT),
        )

        # ---- symbols ----
        self._clear(self.tree_symbols)
        for s in d.get("symbols", []) or []:
            if not s.get("enabled", False):
                tag = "disabled"
            else:
                direction = (s.get("direction") or "FLAT").upper()
                tag = {"UP": "up", "DOWN": "down"}.get(direction, "flat")
            arrow = {"UP": "▲", "DOWN": "▼"}.get((s.get("direction") or "").upper(), "■")

            # MTA alignment count: e.g. "3/3" means all timeframes agree.
            # Per scripts/backtest_mta_direction.py, 3/3 alignment ≈ 2-3x
            # the conditional forward return of single-TF on UP signals.
            n_aligned = int(s.get("mta_n_aligned", 0) or 0)
            n_total = int(s.get("mta_n_total", 0) or 0)
            if n_total > 0:
                align_str = f" {n_aligned}/{n_total}"
            else:
                align_str = ""

            self.tree_symbols.insert("", "end", values=(
                s.get("ticker", "—"),
                _fmt_money(s.get("bid", 0), dec=3),
                _fmt_money(s.get("ask", 0), dec=3),
                _fmt_money(s.get("spread", 0), dec=4),
                (s.get("regime") or "UNKNOWN"),
                f"{arrow}  {(s.get('direction') or 'FLAT')}{align_str}",
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
                f"{float(p.get('qty', 0) or 0):.2f}",
                _fmt_money(p.get("entry", 0), dec=3),
                _fmt_money(p.get("current", 0), dec=3),
                _fmt_money(p.get("sl", 0), dec=3) if p.get("sl") else "—",
                _fmt_money(p.get("tp", 0), dec=3) if p.get("tp") else "—",
                _fmt_money(pnl, signed=True),
                (p.get("duration") or "—"),
            ), tags=(tag,))

        # ---- journal ----
        self._clear(self.tree_journal)
        for j in (d.get("journal", []) or [])[:15]:
            pnl = float(j.get("pnl", 0) or 0)
            tag = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"
            ts_close = j.get("ts_close", "") or ""
            self.tree_journal.insert("", "end", values=(
                _fmt_ts(ts_close),
                _fmt_ts_ist(ts_close),
                j.get("symbol") or "",
                (j.get("strategy") or "")[:18],
                (j.get("side") or "")[:5],
                _fmt_money(j.get("entry", 0), dec=3),
                _fmt_money(j.get("exit", 0), dec=3),
                _fmt_money(pnl, signed=True),
                (j.get("duration") or "—"),
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
        left_bits = [
            f"cfg={Path(bot.get('config_file', '')).name or '?'}",
            f"env={bot.get('env', '?')}",
            f"iter={bot.get('loop_iteration', 0)}",
        ]
        if sess.get("active_name"):
            left_bits.append(f"session={sess.get('active_name')}")
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
