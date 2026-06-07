#!/usr/bin/env python3
"""Daily Instagram content from the Gold Sentiment snapshot.

Turns the engine's latest snapshot (data/metrics/sentiment_monitor_state.json)
into three post-ready artifacts, written date-stamped to data/sentiment/content/:

  • <date>_caption.txt  — Instagram caption + hashtags
  • <date>_reel.txt     — ~20s Reel/Story voiceover script
  • <date>_card.png      — branded 1080×1350 portrait card

DESIGN STANCE (matters): this describes the CURRENT sentiment / balance of
forces — a thermometer, not a forecast. The backtest proved the GSS does NOT
predict next-week price (scripts/backtest_sentiment.py), so every artifact is
framed as "today's read / current bias", never "price will go X". That's honest,
and it protects the poster's credibility. A disclaimer is baked into each one.

Run:  python scripts/sentiment_content.py            # all three for today
      python scripts/sentiment_content.py --only caption
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SNAP = _ROOT / "data" / "metrics" / "sentiment_monitor_state.json"
_FALLBACK_SNAP = _ROOT / "data" / "sentiment" / "gss_XAUUSD.json"
_OUTDIR = _ROOT / "data" / "sentiment" / "content"

# palette (shared with the monitor)
BG, PANEL, BORDER = "#0B0E1A", "#141826", "#2A2F40"
TEXT, DIM, FAINT = "#E6E8EF", "#8A93A6", "#606673"
GREEN, RED, GOLD, CYAN, ORANGE, YELLOW = (
    "#00D68F", "#FF4757", "#FFB800", "#22D3EE", "#FB923C", "#FFD166")
COMP_ORDER = ["fundamental", "technical", "institutional", "retail", "news"]
COMP_LABEL = {"fundamental": "MACRO", "technical": "TECHNICALS",
              "institutional": "SMART MONEY", "retail": "RETAIL CROWD", "news": "NEWS"}
HASHTAGS = ("#gold #xauusd #goldprice #forex #trading #marketsentiment "
            "#technicalanalysis #daytrading #investing #commodities #fed #macro")


# ── snapshot + interpretation ────────────────────────────────────────────────
def load_snapshot(path: Optional[str]) -> Dict[str, Any]:
    for p in (path, _DEFAULT_SNAP, _FALLBACK_SNAP):
        if not p:
            continue
        try:
            return json.loads(Path(p).read_text())
        except Exception:
            continue
    raise SystemExit("No sentiment snapshot found — run scripts/run_sentiment_engine.py first.")


def _gss(snap) -> Tuple[float, str]:
    g = snap.get("gss") or {}
    return float(g.get("total_score", snap.get("gss_total", 50)) or 50), \
        g.get("regime", snap.get("regime", "—"))


def band_color(s: float) -> str:
    return (GREEN if s >= 65 else CYAN if s >= 50 else YELLOW if s >= 35
            else ORANGE if s >= 20 else RED)


def net_read(s: float) -> str:
    if s >= 65:
        return "Bulls are firmly in control today."
    if s >= 55:
        return "Buyers have a clear edge today."
    if s >= 50:
        return "A slight bullish tilt — but it's a tug-of-war."
    if s >= 45:
        return "A slight bearish tilt — sellers are nudging ahead."
    if s >= 35:
        return "Bears have the edge today."
    return "Bears are firmly in control today."


def lean_word(s: float) -> str:
    return ("Strongly Bullish" if s >= 65 else "Bullish" if s >= 55 else
            "Mildly Bullish" if s >= 50 else "Mildly Bearish" if s >= 45 else
            "Bearish" if s >= 35 else "Strongly Bearish")


def emoji_for(s: float) -> str:
    return "🟢" if s >= 55 else "🟡" if s >= 45 else "🔴"


def drivers(snap) -> Tuple[List[str], List[str]]:
    """(supporting, capping) plain-English forces, from structured fields."""
    macro = snap.get("macro_context", {}) or {}
    ms = snap.get("market_structure", {}) or {}
    inst_det = (snap.get("components", {}).get("institutional", {}) or {}).get("details", "")
    bull: List[str] = []
    bear: List[str] = []

    fed = (macro.get("fed_policy") or "").lower()
    if fed == "dovish":
        bull.append("Fed leaning dovish")
    elif fed == "hawkish":
        bear.append("Fed leaning hawkish")
    if macro.get("dxy_falling") is True:
        bull.append("dollar weakening")
    elif macro.get("dxy_falling") is False:
        bear.append("dollar strengthening")
    ry = macro.get("real_yield_10y")
    if ry is not None:
        if ry >= 2.0:
            bear.append(f"real yields elevated ({ry:.1f}%)")
        elif ry < 1.0:
            bull.append("real yields low")
    cpi = macro.get("cpi_yoy")
    if cpi is not None and cpi > 3:
        bull.append(f"inflation running ~{cpi:.1f}%")

    trend = ms.get("trend")
    if trend == "bull_aligned":
        bull.append("uptrend intact")
    elif trend == "bear_aligned":
        bear.append("downtrend on the chart")
    if ms.get("macd_signal") == "bullish":
        bull.append("MACD turning up")
    elif ms.get("macd_signal") == "bearish":
        bear.append("MACD bearish")
    rsi = ms.get("rsi_14")
    if rsi is not None:
        if rsi < 35:
            bear.append(f"momentum weak (RSI {rsi:.0f})")
        elif rsi > 70:
            bear.append(f"overbought (RSI {rsi:.0f})")

    if "etf_flow=outflow" in inst_det:
        bear.append("ETF investors pulling gold out")
    elif "etf_flow=inflow" in inst_det:
        bull.append("ETF investors adding gold")
    m = re.search(r"cot_wow=(-?[\d.]+)", inst_det)
    if m:
        w = float(m.group(1))
        if w > 5:
            bull.append(f"futures longs building (COT +{w:.0f}% w/w)")
        elif w < -5:
            bear.append(f"futures longs unwinding (COT {w:.0f}% w/w)")

    if snap.get("risk_flags", {}).get("geopolitical_shock"):
        bull.append("geopolitical risk bid")
    return bull or ["mixed macro backdrop"], bear or ["few clear headwinds"]


def levels(snap) -> Tuple[Optional[float], Optional[float]]:
    ms = snap.get("market_structure", {}) or {}
    return ms.get("nearest_support"), ms.get("nearest_resistance")


def date_str(snap) -> str:
    try:
        d = dt.datetime.fromisoformat(str(snap.get("generated_at")).replace("Z", "+00:00"))
    except Exception:
        d = dt.datetime.now(dt.timezone.utc)
    return d.strftime("%b %-d, %Y")


# ── text artifacts ───────────────────────────────────────────────────────────
def build_caption(snap, handle: str) -> str:
    s, regime = _gss(snap)
    price = snap.get("price")
    bull, bear = drivers(snap)
    sup, res = levels(snap)
    lv = []
    if sup:
        lv.append(f"support ${sup:,.0f}")
    if res:
        lv.append(f"resistance ${res:,.0f}")
    price_s = f"${price:,.0f}" if isinstance(price, (int, float)) else "—"
    lines = [
        f"{emoji_for(s)} GOLD (XAUUSD) SENTIMENT — {s:.0f}/100 · {lean_word(s)}",
        f"💲 {price_s}   📅 {date_str(snap)}",
        "",
        "Today's read — a snapshot of what's pushing gold right now:",
        f"🟢 Supporting: {', '.join(bull[:4])}",
        f"🔴 Capping it: {', '.join(bear[:4])}",
        "",
        f"📊 {net_read(s)}",
    ]
    if lv:
        lines.append(f"🎯 Watching — {' · '.join(lv)}")
    lines += [
        "",
        "Which way are you leaning on gold today? 👇",
        "",
        "⚠️ This is a data-driven read of CURRENT sentiment, not a price prediction "
        "or financial advice. Always do your own research.",
        "",
        f"Daily gold sentiment → {handle}",
        "",
        HASHTAGS,
    ]
    return "\n".join(lines)


def build_reel(snap, handle: str) -> str:
    s, regime = _gss(snap)
    bull, bear = drivers(snap)
    sup, res = levels(snap)
    watch = []
    if sup:
        watch.append(f"support at {sup:,.0f}")
    if res:
        watch.append(f"resistance at {res:,.0f}")
    out = [
        f"🎬 GOLD SENTIMENT REEL — {date_str(snap)}   (~20s, talk to camera)",
        "",
        f"[HOOK]  \"Here's where gold sentiment stands today — {s:.0f} out of 100, "
        f"{lean_word(s).lower()}.\"",
        "",
        f"[SUPPORT]  \"What's backing gold right now: {_say(bull[:3])}.\"",
        "",
        f"[HEADWINDS]  \"But it's a tug-of-war — {_say(bear[:3])}.\"",
        "",
        f"[TAKEAWAY]  \"{net_read(s)}"
        + (f" Keep an eye on {_say(watch)}.\"" if watch else "\""),
        "",
        f"[SIGN-OFF]  \"Follow {handle} for a daily gold sentiment read. "
        "Not financial advice.\"",
        "",
        "— On-screen text idea: big '" + f"{s:.0f}/100 {lean_word(s)}" + "' over a gold chart.",
    ]
    return "\n".join(out)


def _say(items: List[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return "mixed signals"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── image card ───────────────────────────────────────────────────────────────
def render_card(snap, out_path: Path, handle: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    s, regime = _gss(snap)
    price = snap.get("price")
    col = band_color(s)
    comps = snap.get("components", {}) or {}
    sup, res = levels(snap)
    bull, bear = drivers(snap)

    fig = plt.figure(figsize=(10.8, 13.5), dpi=100)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)            # y increases downward (screen-like)
    ax.axis("off")

    def text(x, y, t, size, color=TEXT, weight="normal", ha="left", family="sans-serif"):
        ax.text(x, y, t, fontsize=size, color=color, weight=weight, ha=ha,
                va="center", family=family, parse_math=False)

    # header
    text(0.06, 0.05, "GOLD SENTIMENT", 30, GOLD, "bold")
    text(0.06, 0.085, "XAUUSD · daily read", 15, DIM)
    text(0.94, 0.05, date_str(snap), 15, DIM, ha="right")
    if isinstance(price, (int, float)):
        text(0.94, 0.082, f"${price:,.0f}", 22, GOLD, "bold", ha="right")
    ax.add_patch(Rectangle((0.06, 0.11), 0.88, 0.004, color=BORDER))

    # big score + gauge
    text(0.06, 0.20, f"{s:.0f}", 96, col, "bold")
    text(0.30, 0.165, "/100", 26, DIM)
    text(0.30, 0.215, lean_word(s).upper(), 26, col, "bold")
    text(0.30, 0.245, regime, 15, DIM)
    gx, gy, gw, gh = 0.06, 0.30, 0.88, 0.022
    ax.add_patch(FancyBboxPatch((gx, gy), gw, gh, boxstyle="round,pad=0.002",
                                facecolor=PANEL, edgecolor=BORDER))
    ax.add_patch(Rectangle((gx, gy), gw * s / 100.0, gh, color=col))
    ax.add_patch(Rectangle((gx + gw * 0.5, gy - 0.004), 0.002, gh + 0.008,
                           color=FAINT))
    for frac, lab in ((0.0, "0 bear"), (0.5, "50"), (1.0, "100 bull")):
        text(gx + gw * frac, gy + gh + 0.022, lab, 11, FAINT,
             ha="left" if frac == 0 else "right" if frac == 1 else "center")

    # component bars
    text(0.06, 0.375, "WHAT'S DRIVING IT", 15, GOLD, "bold")
    y = 0.41
    for name in COMP_ORDER:
        c = comps.get(name, {}) or {}
        score = float(c.get("score", 0) or 0)
        maxv = float(c.get("max", 1) or 1)
        live = bool(c.get("live"))
        pct = max(0.0, min(1.0, score / maxv if maxv else 0))
        bc = (GREEN if pct >= 0.66 else YELLOW if pct >= 0.4 else RED) if live else FAINT
        text(0.06, y, COMP_LABEL[name], 13, DIM, "bold")
        text(0.94, y, f"{score:.0f}/{maxv:.0f}", 13, bc, "bold", ha="right")
        by = y + 0.018
        ax.add_patch(Rectangle((0.06, by), 0.88, 0.014, color=PANEL))
        ax.add_patch(Rectangle((0.06, by), 0.88 * pct, 0.014, color=bc))
        if not live:
            text(0.50, y, "no live feed", 10, FAINT, ha="center")
        y += 0.052

    # supporting / capping
    text(0.06, y + 0.005, "▲ SUPPORTING", 13, GREEN, "bold")
    text(0.06, y + 0.035, _wrap(", ".join(bull[:4]), 52), 12, TEXT)
    text(0.06, y + 0.095, "▼ CAPPING IT", 13, RED, "bold")
    text(0.06, y + 0.125, _wrap(", ".join(bear[:4]), 52), 12, TEXT)

    # net read + levels + footer (fixed band so nothing overlaps)
    ax.add_patch(Rectangle((0.06, 0.862), 0.88, 0.003, color=BORDER))
    text(0.06, 0.892, net_read(s), 15, col, "bold")
    lvl = []
    if sup:
        lvl.append(f"Support ${sup:,.0f}")
    if res:
        lvl.append(f"Resistance ${res:,.0f}")
    if lvl:
        text(0.06, 0.922, "   ·   ".join(lvl), 13, DIM)
    text(0.06, 0.952, handle, 14, GOLD, "bold")
    text(0.06, 0.978, "Current sentiment, not a prediction or financial advice. DYOR.",
         10, FAINT)

    fig.savefig(out_path, facecolor=BG, bbox_inches=None)
    plt.close(fig)


def _wrap(t: str, width: int) -> str:
    out, line = [], ""
    for word in t.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        out.append(line)
    return "\n".join(out[:3])


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Daily gold-sentiment Instagram content.")
    ap.add_argument("--snapshot", default=None, help="Path to sentiment snapshot JSON")
    ap.add_argument("--outdir", default=str(_OUTDIR))
    ap.add_argument("--handle", default=os.environ.get("CONTENT_HANDLE", "@yourhandle"))
    ap.add_argument("--only", choices=("caption", "reel", "card"), default=None,
                    help="Generate just one artifact (default: all three)")
    args = ap.parse_args()

    snap = load_snapshot(args.snapshot)
    s, regime = _gss(snap)
    today = dt.date.today().isoformat()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"gold_sentiment_{today}"
    want = {args.only} if args.only else {"caption", "reel", "card"}
    made = []

    if "caption" in want:
        cap = build_caption(snap, args.handle)
        (outdir / f"{stem}_caption.txt").write_text(cap, encoding="utf-8")
        made.append(f"{stem}_caption.txt")
        print("\n" + "=" * 60 + "\nCAPTION\n" + "=" * 60 + f"\n{cap}\n")
    if "reel" in want:
        reel = build_reel(snap, args.handle)
        (outdir / f"{stem}_reel.txt").write_text(reel, encoding="utf-8")
        made.append(f"{stem}_reel.txt")
    if "card" in want:
        try:
            render_card(snap, outdir / f"{stem}_card.png", args.handle)
            made.append(f"{stem}_card.png")
        except Exception as e:
            print(f"[warn] card render failed: {e}", file=sys.stderr)

    print(f"[content] GSS {s:.0f} ({regime}) → {len(made)} file(s) in {outdir}:")
    for m in made:
        print(f"   • {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
