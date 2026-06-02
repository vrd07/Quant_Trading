#!/usr/bin/env python3
"""
Market Sentiment Monitor — interactive pop-up window.

Renders the Gold Sentiment Score snapshot produced by
``scripts/run_sentiment_engine.py`` (data/metrics/sentiment_monitor_state.json):

  ┌──────────── GSS GAUGE + REGIME + RECOMMENDATION ────────────┐
  │ COMPONENT BREAKDOWN (fundamental/technical/.../news)        │
  │ MARKET STRUCTURE          ·  MACRO CONTEXT                  │
  │ RISK FLAGS                ·  FEED STATUS                    │
  │ KEY 2026 PRICE LEVELS (with live-price marker)             │
  └────────────────────────────────────────────────────────────┘

This window is DISPLAY ONLY — it never trades. GSS is advisory until it passes
backtest.md. Components with no feed are shown as MISSING (neutral), never faked.

Run the engine in one terminal and this window in another:
    python scripts/run_sentiment_engine.py --loop 900
    python scripts/sentiment_monitor.py

Dependencies: Python stdlib only (tkinter is built-in).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, Optional

# ── palette (shared with live_monitor) ───────────────────────────────────────
BG, BG_PANEL, BG_PANEL_2, BORDER = "#0B0E1A", "#141826", "#1B2030", "#2A2F40"
TEXT, TEXT_DIM, TEXT_FAINT = "#E6E8EF", "#8A93A6", "#606673"
GREEN, RED, GOLD, YELLOW, BLUE = "#00D68F", "#FF4757", "#FFB800", "#FFD166", "#38BDF8"
PURPLE, CYAN, ORANGE = "#A78BFA", "#22D3EE", "#FB923C"

COMPONENT_MAX = {"fundamental": 30, "technical": 25, "institutional": 20,
                 "retail": 15, "news": 10}
COMPONENT_ORDER = ["fundamental", "technical", "institutional", "retail", "news"]


def _gss_color(total: float) -> str:
    if total >= 65:
        return GREEN
    if total >= 50:
        return CYAN
    if total >= 35:
        return YELLOW
    if total >= 20:
        return ORANGE
    return RED


def _fmt_num(v: Any, dec: int = 2) -> str:
    try:
        return f"{float(v):,.{dec}f}"
    except Exception:
        return "—"


class SentimentMonitorApp:
    def __init__(self, state_file: str, refresh_ms: int = 2000, topmost: bool = True):
        self.state_file = Path(state_file)
        self.refresh_ms = max(500, int(refresh_ms))
        self._missed = 0

        self.root = tk.Tk()
        self.root.title("XAUUSD — Market Sentiment (GSS)")
        self.root.geometry("1040x960")
        self.root.minsize(900, 800)
        self.root.configure(bg=BG)
        try:
            self.root.attributes("-topmost", bool(topmost))
        except Exception:
            pass

        self._style()
        self._build()
        self.root.after(50, self._tick)

    def _style(self) -> None:
        st = ttk.Style(self.root)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Mono.Treeview", background=BG_PANEL_2, fieldbackground=BG_PANEL_2,
                     foreground=TEXT, rowheight=22, borderwidth=0, font=("Menlo", 10))
        st.configure("Mono.Treeview.Heading", background=BORDER, foreground=GOLD,
                     font=("Menlo", 10, "bold"))
        st.map("Mono.Treeview", background=[("selected", BORDER)],
               foreground=[("selected", TEXT)])

    # ── layout ──────────────────────────────────────────────────────────────
    def _build(self) -> None:
        self._build_banner()

        body = tk.Frame(self.root, bg=BG, padx=12, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        body.grid_columnconfigure(0, weight=1, uniform="c")
        body.grid_columnconfigure(1, weight=1, uniform="c")

        comp = self._panel(body, "GSS COMPONENT BREAKDOWN")
        comp.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, 6))
        self._build_components(comp)

        ms = self._panel(body, "MARKET STRUCTURE")
        ms.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(0, 6))
        self.lbl_structure = self._kv_block(ms)

        macro = self._panel(body, "MACRO CONTEXT")
        macro.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(0, 6))
        self.lbl_macro = self._kv_block(macro)

        flags = self._panel(body, "RISK FLAGS")
        flags.grid(row=2, column=0, sticky="nsew", padx=(0, 4), pady=(0, 6))
        self._build_flags(flags)

        feeds = self._panel(body, "FEED STATUS")
        feeds.grid(row=2, column=1, sticky="nsew", padx=(4, 0), pady=(0, 6))
        self.lbl_feeds = self._kv_block(feeds)

        levels = self._panel(body, "KEY 2026 PRICE LEVELS (market_sentiment.md §8)")
        levels.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self._build_levels(levels)

        footer = tk.Frame(self.root, bg=BG_PANEL_2, padx=10, pady=4)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.footer = tk.Label(footer, text="", bg=BG_PANEL_2, fg=TEXT_DIM,
                               font=("Menlo", 9), anchor="w")
        self.footer.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_banner(self) -> None:
        top = tk.Frame(self.root, bg=BG, pady=10, padx=14)
        top.pack(side=tk.TOP, fill=tk.X)

        left = tk.Frame(top, bg=BG)
        left.pack(side=tk.LEFT)
        tk.Label(left, text="GOLD SENTIMENT SCORE", bg=BG, fg=TEXT_DIM,
                 font=("Menlo", 10, "bold")).pack(anchor="w")
        self.gss_num = tk.Label(left, text="—", bg=BG, fg=TEXT,
                                font=("Menlo", 44, "bold"))
        self.gss_num.pack(anchor="w")
        self.gss_regime = tk.Label(left, text="waiting for engine…", bg=BG, fg=TEXT_DIM,
                                   font=("Menlo", 14, "bold"))
        self.gss_regime.pack(anchor="w")

        # 0-100 gauge bar
        gauge_wrap = tk.Frame(top, bg=BG)
        gauge_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=20)
        self.gauge = tk.Canvas(gauge_wrap, height=26, bg=BG_PANEL_2,
                               highlightthickness=1, highlightbackground=BORDER, bd=0)
        self.gauge.pack(fill=tk.X, pady=(28, 2))
        self._gauge_fill = self.gauge.create_rectangle(0, 0, 0, 26, fill=GREEN, width=0)
        self._gauge_mid = self.gauge.create_line(0, 0, 0, 26, fill=TEXT_FAINT, width=1)
        scale = tk.Frame(gauge_wrap, bg=BG)
        scale.pack(fill=tk.X)
        for t in ("0 bear", "35", "50", "65", "100 bull"):
            tk.Label(scale, text=t, bg=BG, fg=TEXT_FAINT,
                     font=("Menlo", 8)).pack(side=tk.LEFT, expand=True)

        right = tk.Frame(top, bg=BG)
        right.pack(side=tk.RIGHT)
        tk.Label(right, text="PRICE (XAUUSD)", bg=BG, fg=TEXT_DIM,
                 font=("Menlo", 9, "bold")).pack(anchor="e")
        self.price_lbl = tk.Label(right, text="—", bg=BG, fg=GOLD,
                                  font=("Menlo", 20, "bold"))
        self.price_lbl.pack(anchor="e")
        self.rec_lbl = tk.Label(right, text="—", bg=BG, fg=TEXT,
                                font=("Menlo", 13, "bold"))
        self.rec_lbl.pack(anchor="e")
        self.rec_note = tk.Label(right, text="", bg=BG, fg=YELLOW,
                                 font=("Menlo", 9, "italic"))
        self.rec_note.pack(anchor="e")

    def _panel(self, parent, title: str) -> tk.Frame:
        panel = tk.Frame(parent, bg=BG_PANEL, highlightbackground=BORDER,
                         highlightcolor=BORDER, highlightthickness=1, bd=0)
        tk.Label(panel, text=title, bg=BG_PANEL, fg=GOLD,
                 font=("Menlo", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Frame(panel, bg=BORDER, height=1).pack(fill=tk.X, padx=10, pady=(4, 6))
        return panel

    def _kv_block(self, panel) -> tk.Label:
        lbl = tk.Label(panel, text="—", bg=BG_PANEL, fg=TEXT, justify="left",
                       anchor="nw", font=("Menlo", 11))
        lbl.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        return lbl

    def _build_components(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=12, pady=4)
        body.pack(fill=tk.BOTH, expand=True)
        self.comp_bars: Dict[str, Dict[str, Any]] = {}
        for name in COMPONENT_ORDER:
            row = tk.Frame(body, bg=BG_PANEL)
            row.pack(fill=tk.X, pady=3)
            head = tk.Frame(row, bg=BG_PANEL)
            head.pack(fill=tk.X)
            tk.Label(head, text=f"{name.upper()}  /{COMPONENT_MAX[name]}", bg=BG_PANEL,
                     fg=TEXT_DIM, font=("Menlo", 9, "bold")).pack(side=tk.LEFT)
            badge = tk.Label(head, text=" — ", bg=BG_PANEL_2, fg=TEXT_DIM,
                             font=("Menlo", 8, "bold"), padx=4)
            badge.pack(side=tk.RIGHT)
            val = tk.Label(head, text="—", bg=BG_PANEL, fg=TEXT,
                           font=("Menlo", 10, "bold"))
            val.pack(side=tk.RIGHT, padx=(0, 8))
            canvas = tk.Canvas(row, height=12, bg=BG_PANEL_2, highlightthickness=0, bd=0)
            canvas.pack(fill=tk.X, pady=(2, 0))
            fill = canvas.create_rectangle(0, 0, 0, 12, fill=BLUE, width=0)
            detail = tk.Label(row, text="", bg=BG_PANEL, fg=TEXT_FAINT,
                              font=("Menlo", 9), anchor="w")
            detail.pack(fill=tk.X)
            self.comp_bars[name] = {"canvas": canvas, "fill": fill, "val": val,
                                    "badge": badge, "detail": detail}

    def _build_flags(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=12, pady=4)
        body.pack(fill=tk.BOTH, expand=True)
        self.flag_lbls: Dict[str, tk.Label] = {}
        for key in ("dxy_surging", "real_yields_spiking", "retail_extreme_long",
                    "geopolitical_shock", "weekend_gap_risk"):
            lbl = tk.Label(body, text=f"○ {key}", bg=BG_PANEL, fg=TEXT_DIM,
                           font=("Menlo", 11, "bold"), anchor="w")
            lbl.pack(fill=tk.X, pady=1)
            self.flag_lbls[key] = lbl

    def _build_levels(self, panel) -> None:
        body = tk.Frame(panel, bg=BG_PANEL, padx=8, pady=4)
        body.pack(fill=tk.BOTH, expand=True)
        cols = ("level", "kind", "rel")
        self.tree_levels = ttk.Treeview(body, columns=cols, show="headings",
                                        height=6, style="Mono.Treeview")
        for c, h, w in zip(cols, ("Level", "Type", "Price is"), (420, 120, 120)):
            self.tree_levels.heading(c, text=h)
            self.tree_levels.column(c, width=w, anchor="w")
        self.tree_levels.pack(fill=tk.BOTH, expand=True)
        self.tree_levels.tag_configure("critical", foreground=RED)
        self.tree_levels.tag_configure("resistance", foreground=ORANGE)
        self.tree_levels.tag_configure("support", foreground=GREEN)
        self.tree_levels.tag_configure("at", foreground=GOLD, background=BG_PANEL_2)

    # ── polling ───────────────────────────────────────────────────────────────
    def _load(self) -> Optional[Dict[str, Any]]:
        try:
            if not self.state_file.exists():
                return None
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _tick(self) -> None:
        d = self._load()
        if d is not None:
            self._missed = 0
            try:
                self._render(d)
            except Exception as e:
                self.gss_regime.config(text=f"render error: {e}", fg=YELLOW)
        else:
            self._missed += 1
            if self._missed == 3:
                self.gss_regime.config(
                    text=f"no data — run scripts/run_sentiment_engine.py", fg=YELLOW)
        self.root.after(self.refresh_ms, self._tick)

    def _render(self, d: Dict[str, Any]) -> None:
        gss = d.get("gss", {}) or {}
        total = float(gss.get("total_score", 0) or 0)
        color = _gss_color(total)
        self.gss_num.config(text=f"{total:.0f}", fg=color)
        self.gss_regime.config(text=(gss.get("regime") or "—").upper(), fg=color)

        w = self.gauge.winfo_width() or 1
        self.gauge.coords(self._gauge_fill, 0, 0, int(w * total / 100), 26)
        self.gauge.itemconfigure(self._gauge_fill, fill=color)
        self.gauge.coords(self._gauge_mid, int(w * 0.5), 0, int(w * 0.5), 26)

        price = d.get("price")
        self.price_lbl.config(text=_fmt_num(price) if price else "—")
        rec = d.get("recommendation", {}) or {}
        act = rec.get("action", "—")
        rec_color = GREEN if "LONG" in act else RED if "SHORT" in act else YELLOW
        self.rec_lbl.config(text=act, fg=rec_color)
        self.rec_note.config(text=rec.get("note", "") or "")

        # components
        comps = d.get("components", {}) or {}
        for name in COMPONENT_ORDER:
            c = comps.get(name, {}) or {}
            bar = self.comp_bars[name]
            score = float(c.get("score", 0) or 0)
            maxv = float(c.get("max", COMPONENT_MAX[name]))
            pct = max(0.0, min(1.0, score / maxv if maxv else 0))
            cw = bar["canvas"].winfo_width() or 1
            live = bool(c.get("live"))
            bcolor = (GREEN if pct >= 0.66 else YELLOW if pct >= 0.4 else RED) if live else TEXT_FAINT
            bar["canvas"].coords(bar["fill"], 0, 0, int(cw * pct), 12)
            bar["canvas"].itemconfigure(bar["fill"], fill=bcolor)
            bar["val"].config(text=f"{score:.1f}", fg=bcolor)
            if live:
                bar["badge"].config(text=" LIVE ", bg=GREEN, fg=BG)
            else:
                bar["badge"].config(text=" MISSING ", bg=BG_PANEL_2, fg=TEXT_DIM)
            bar["detail"].config(text=(c.get("details") or "")[:90])

        # market structure
        ms = d.get("market_structure", {}) or {}
        self.lbl_structure.config(text=(
            f"Trend         {ms.get('trend','—')}\n"
            f"Price vs 50e  {ms.get('price_vs_50ema','—')}   (EMA50 {_fmt_num(ms.get('ema_50'))})\n"
            f"Price vs 200e {ms.get('price_vs_200ema','—')}   (EMA200 {_fmt_num(ms.get('ema_200'))})\n"
            f"RSI-14        {ms.get('rsi_14','—')}\n"
            f"MACD          {ms.get('macd_signal','—')}\n"
            f"Bollinger     {ms.get('bb_state','—')}\n"
            f"ATR-14        {_fmt_num(ms.get('atr_14'))}  ({ms.get('atr_pct','—')}%)\n"
            f"Support       {_fmt_num(ms.get('nearest_support'))}\n"
            f"Resistance    {_fmt_num(ms.get('nearest_resistance'))}\n"
            f"Session       {ms.get('session','—')}"
        ))

        # macro
        mc = d.get("macro_context", {}) or {}
        self.lbl_macro.config(text=(
            f"Fed policy        {mc.get('fed_policy','—')}\n"
            f"10Y real yield    {mc.get('real_yield_10y','—')}  "
            f"(falling={mc.get('real_yield_falling','—')})\n"
            f"Dollar falling    {mc.get('dxy_falling','—')}\n"
            f"CPI YoY           {mc.get('cpi_yoy','—')}%\n\n"
            f"missing: {', '.join(d.get('missing_components', []) or []) or 'none'}"
        ))

        # risk flags
        flags = d.get("risk_flags", {}) or {}
        for key, lbl in self.flag_lbls.items():
            on = bool(flags.get(key))
            lbl.config(text=f"{'●' if on else '○'} {key}",
                       fg=RED if on else TEXT_DIM)

        # feeds
        feeds = d.get("feeds", {}) or {}
        self.lbl_feeds.config(text="\n".join(
            f"{k:14} {v}" for k, v in feeds.items()))

        # levels
        for iid in self.tree_levels.get_children():
            self.tree_levels.delete(iid)
        for lv in d.get("price_levels", []) or []:
            rel = lv.get("rel", "—")
            tag = "at" if rel == "AT" else lv.get("kind", "support")
            self.tree_levels.insert("", "end", values=(
                lv.get("label", "—"), lv.get("kind", "—"), rel,
            ), tags=(tag,))

        # footer
        gen = d.get("generated_at", "")
        self.footer.config(
            text=f"asset={d.get('asset','?')}  ·  price_src={d.get('price_source','?')}  "
                 f"·  generated {gen[11:19] if len(gen) >= 19 else gen}  ·  Ctrl-Q to quit")

    def run(self) -> None:
        self.root.bind("<Control-q>", lambda _e: self.root.destroy())
        self.root.bind("<Command-q>", lambda _e: self.root.destroy())
        self.root.mainloop()


def main() -> int:
    p = argparse.ArgumentParser(description="Market Sentiment (GSS) monitor pop-up.")
    p.add_argument("--state-file", default=None,
                   help="Path to sentiment JSON (default: data/metrics/sentiment_monitor_state.json)")
    p.add_argument("--refresh", type=int, default=2000, help="Refresh ms (default 2000)")
    p.add_argument("--no-topmost", action="store_true")
    args = p.parse_args()

    root = Path(__file__).resolve().parent.parent
    state = args.state_file or str(root / "data" / "metrics" / "sentiment_monitor_state.json")
    if not os.path.isabs(state):
        state = str(root / state)
    print(f"[sentiment_monitor] reading {state}")
    SentimentMonitorApp(state_file=state, refresh_ms=args.refresh,
                        topmost=not args.no_topmost).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
