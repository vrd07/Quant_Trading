#!/usr/bin/env python3
"""Render a one-page 'How to read Gold Sentiment' cheat sheet (PNG).

Static reference — print it or keep it on screen while reading the daily card.
Run:  python scripts/sentiment_cheatsheet.py
Out:  data/sentiment/content/sentiment_cheatsheet.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HANDLE = os.environ.get("CONTENT_HANDLE", "@varad_fx")

# print-friendly light theme
BG, INK, DIM, FAINT = "#FBF8F2", "#1A1A2E", "#5B6472", "#9AA0AC"
GOLD, GREEN, LGREEN, ORANGE, RED = "#B8860B", "#1E9E5A", "#6FD08C", "#E0A458", "#D1495B"
PANEL = "#F0EADD"

INGREDIENTS = [
    ("MACRO", "/30", "Rates, the US dollar, inflation, Fed", "rates low, $ weak, inflation high", GREEN),
    ("TECHNICALS", "/25", "What the price chart is doing now", "uptrend + strong momentum", GREEN),
    ("SMART MONEY", "/20", "Big institutions: futures + ETFs", "the big players are buying", GREEN),
    ("RETAIL CROWD", "/15", "Everyday traders (contrarian!)", "crowd is fearful, NOT piled in", ORANGE),
    ("NEWS", "/10", "Tone of recent headlines", "positive news / a crisis bid", GREEN),
]


def render(out: Path) -> None:
    fig = plt.figure(figsize=(8.5, 11.4), dpi=132)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)
    ax.axis("off")

    def t(x, y, s, size, color=INK, weight="normal", ha="left"):
        ax.text(x, y, s, fontsize=size, color=color, weight=weight, ha=ha,
                va="center", parse_math=False)

    def divider(y):
        ax.add_patch(Rectangle((0.06, y), 0.88, 0.0016, color="#D8CFBE"))

    def header(y, n, title):
        ax.add_patch(Rectangle((0.06, y - 0.012), 0.025, 0.024, color=GOLD))
        t(0.105, y, f"{n}", 15, GOLD, "bold")
        t(0.145, y, title, 15, INK, "bold")

    # title
    t(0.06, 0.038, "GOLD SENTIMENT", 27, GOLD, "bold")
    t(0.06, 0.072, "how to read it in 30 seconds", 14, DIM)
    t(0.94, 0.045, "CHEAT SHEET", 13, FAINT, "bold", ha="right")
    divider(0.095)

    # 1 — the score
    header(0.128, "1", "THE BIG NUMBER  (0–100)")
    t(0.06, 0.158, "← BEARS WINNING", 11, RED, "bold")
    t(0.94, 0.158, "BULLS WINNING →", 11, GREEN, "bold", ha="right")
    gx, gy, gw, gh = 0.06, 0.172, 0.88, 0.026
    for frac0, frac1, c in ((0, .35, RED), (.35, .5, ORANGE), (.5, .65, LGREEN), (.65, 1, GREEN)):
        ax.add_patch(Rectangle((gx + gw * frac0, gy), gw * (frac1 - frac0), gh, color=c))
    ax.add_patch(Rectangle((gx + gw * 0.5 - 0.0015, gy - 0.006), 0.003, gh + 0.012, color=INK))
    for frac, lab in ((0, "0"), (.35, "35"), (.5, "50"), (.65, "65"), (1, "100")):
        t(gx + gw * frac, gy + gh + 0.02, lab, 11, DIM,
          ha="left" if frac == 0 else "right" if frac == 1 else "center")
    t(0.5, 0.236, "50 = balanced tug-of-war.  Above 50 = buyers ahead · below 50 = sellers ahead.",
      12, INK, ha="center")
    t(0.5, 0.258, "The further from 50, the stronger the mood.", 12, DIM, ha="center")

    # 2 — ingredients
    header(0.30, "2", "THE 5 INGREDIENTS  (the bars on the card)")
    t(0.275, 0.33, "WHAT IT IS", 11, FAINT, "bold")
    t(0.615, 0.33, "GOLD LIKES IT WHEN…", 11, FAINT, "bold")
    y = 0.362
    for name, pts, what, bull, _c in INGREDIENTS:
        ax.add_patch(Rectangle((0.06, y - 0.018), 0.88, 0.044, color=PANEL))
        t(0.075, y, name, 12, INK, "bold")
        t(0.075, y + 0.021, pts, 10.5, GOLD, "bold")
        t(0.275, y + 0.008, what, 11, INK)
        t(0.615, y + 0.008, bull, 11, GREEN)
        y += 0.052
    t(0.06, y + 0.004, "Long green bar = that reason favours gold.   Short red bar = it's working against gold.",
      11.5, DIM)

    # 3 — routine
    header(y + 0.05, "3", "YOUR 30-SECOND DAILY ROUTINE")
    ry = y + 0.085
    for i, (q, hint) in enumerate([
        ("Where's the number?", "above or below 50 → the overall mood"),
        ("Which bars are green vs red?", "the reasons behind the mood"),
        ("Do they agree or disagree?", "all same = strong conviction · mixed = tug-of-war, be patient"),
    ], 1):
        t(0.07, ry, f"{i}", 13, GOLD, "bold")
        t(0.11, ry, q, 12.5, INK, "bold")
        t(0.11, ry + 0.02, hint, 11, DIM)
        ry += 0.05

    # 4 — the rule
    header(ry + 0.02, "4", "THE ONE RULE")
    ax.add_patch(Rectangle((0.06, ry + 0.04), 0.88, 0.072, color="#F3E9CE"))
    t(0.09, ry + 0.062, "Post it as “today's MOOD, and why.”   Never “gold WILL go up.”",
      13, INK, "bold")
    t(0.09, ry + 0.090, "It describes today's conditions — it does not predict tomorrow's price.",
      11.5, DIM)

    t(0.06, 0.972, f"{HANDLE}  ·  daily gold sentiment", 12, GOLD, "bold")
    t(0.94, 0.972, "not financial advice", 10.5, FAINT, ha="right")

    fig.savefig(out, facecolor=BG)
    plt.close(fig)
    print(f"[cheatsheet] wrote {out}")


if __name__ == "__main__":
    out = ROOT / "data" / "sentiment" / "content" / "sentiment_cheatsheet.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    render(out)
    sys.exit(0)
