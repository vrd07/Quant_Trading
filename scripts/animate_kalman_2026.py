#!/usr/bin/env python3
"""
Animated bar-by-bar replay of the Kalman 2026 fixed-parameter backtest.

Two synced panels, advancing one TRADING DAY per frame:
  TOP    a scrolling ~10-day window of XAUUSD 15m price + the Kalman line, with
         trade entries (^/v), exits (o), and live SL/TP brackets as they fire.
  BOTTOM the full-2026 equity curve drawing in progressively (green above /
         red below $5k), with a live stats readout.

Outputs reports/figs/kalman_replay.mp4 (smooth) + .gif (portable) and three
snapshot PNGs (early / mid / late) so the progression can be shown inline.

  python scripts/animate_kalman_2026.py            # mp4 + gif + snapshots
  python scripts/animate_kalman_2026.py --fast     # gif + snapshots only (quick)
"""
import sys
import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.animation import FuncAnimation, PillowWriter, FFMpegWriter

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src.data.indicators import Indicators

spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

FIGDIR = ROOT / "reports/figs"
FIGDIR.mkdir(parents=True, exist_ok=True)
CAP = 5000.0
GREEN, RED, BLUE, GREY = "#1a9850", "#d73027", "#2c7fb8", "#666666"
WIN_DAYS = 10

plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.25,
                     "axes.axisbelow": True})

# --- data ------------------------------------------------------------------
print("loading ...")
bars = bt.load_15m_2026()
bars.index = bars.index.tz_localize(None)
kal = Indicators.kalman_filter(bars["close"], q=1e-5, r=0.01)
kal.index = bars.index
t = pd.read_csv(ROOT / "data/backtests/kalman_2026_fixed_trades.csv",
                parse_dates=["entry_ts", "exit_ts"]).sort_values("exit_ts").reset_index(drop=True)
t["entry_ts"] = t["entry_ts"].dt.tz_localize(None)
t["exit_ts"] = t["exit_ts"].dt.tz_localize(None)

# equity step series (anchored at start)
eq = pd.concat([pd.Series([CAP], index=[bars.index[0]]),
                CAP + t.set_index("exit_ts").pnl.cumsum()])
EQ_LO, EQ_HI = eq.min() - 150, eq.max() + 150
PX_LO, PX_HI = bars["close"].min(), bars["close"].max()

# frames = end-of-day timestamp for each trading day
day_ends = bars.groupby(bars.index.normalize()).apply(lambda d: d.index.max())
FRAMES = list(day_ends.values)
print(f"{len(FRAMES)} daily frames")

fig, (axp, axe) = plt.subplots(2, 1, figsize=(14, 9), dpi=90,
                               gridspec_kw={"height_ratios": [3, 2]})


def draw(end_t):
    end_t = pd.Timestamp(end_t)
    axp.clear(); axe.clear()
    w0 = end_t - pd.Timedelta(days=WIN_DAYS)

    # ---- TOP: scrolling price + kalman + trades ----
    wb = bars[(bars.index >= w0) & (bars.index <= end_t)]
    wk = kal[(kal.index >= w0) & (kal.index <= end_t)]
    axp.plot(wb.index, wb["close"].values, color="#222", lw=1.2, zorder=3)
    axp.plot(wk.index, wk.values, color=BLUE, lw=1.9, zorder=2)
    vis = t[(t.entry_ts <= end_t) & (t.exit_ts >= w0)]
    for _, r in vis.iterrows():
        col = GREEN if r.pnl > 0 else RED
        e_t = max(r.entry_ts, w0)
        x_t = min(r["exit_ts"], end_t)          # live bracket stops "now"
        closed = r["exit_ts"] <= end_t
        lo_y, hi_y = min(r.sl0, r.tp), max(r.sl0, r.tp)
        axp.add_patch(Rectangle((mdates.date2num(e_t), lo_y),
                                mdates.date2num(x_t) - mdates.date2num(e_t),
                                hi_y - lo_y, facecolor=col, alpha=0.12,
                                edgecolor=col, lw=0.9, ls="--", zorder=1))
        if r.entry_ts >= w0:
            mk = "^" if r.side == "buy" else "v"
            axp.scatter(r.entry_ts, r.entry, marker=mk, s=130, color=col,
                        edgecolor="k", lw=0.6, zorder=5)
        if closed:
            axp.scatter(r["exit_ts"], r["exit"], marker="o", s=55, color=col,
                        edgecolor="k", lw=0.6, zorder=5)
    if len(wb):
        pad = (wb["close"].max() - wb["close"].min()) * 0.12 + 5
        axp.set_ylim(wb["close"].min() - pad, wb["close"].max() + pad)
    axp.set_xlim(w0, end_t)
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%b-%d"))
    axp.set_ylabel("Gold ($)")
    axp.set_title(f"Kalman 2026 replay  —  {end_t.strftime('%Y-%m-%d')}   "
                  f"(scrolling {WIN_DAYS}-day window: price vs Kalman line, trades firing live)",
                  fontsize=12)
    axp.legend(handles=[
        Line2D([0], [0], color="#222", lw=1.2, label="price"),
        Line2D([0], [0], color=BLUE, lw=1.9, label="Kalman"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=10, label="▲ BUY"),
        Line2D([0], [0], marker="v", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=10, label="▼ SELL"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor=GREEN, markeredgecolor="k", ms=9, label="win"),
        Line2D([0], [0], marker="s", color="w", markerfacecolor=RED, markeredgecolor="k", ms=9, label="loss"),
    ], loc="upper left", ncol=6, fontsize=8, framealpha=0.9)

    # ---- BOTTOM: equity building up ----
    e = eq[eq.index <= end_t]
    axe.plot(e.index, e.values, color=BLUE, lw=1.6, zorder=3)
    axe.fill_between(e.index, CAP, e.values, where=e.values >= CAP, color=GREEN, alpha=0.15)
    axe.fill_between(e.index, CAP, e.values, where=e.values < CAP, color=RED, alpha=0.15)
    axe.axhline(CAP, color=GREY, lw=1, ls="--")
    cur = e.iloc[-1]
    axe.scatter([e.index[-1]], [cur], color=BLUE, s=45, zorder=5)
    axe.set_xlim(bars.index[0], bars.index[-1])
    axe.set_ylim(EQ_LO, EQ_HI)
    axe.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    axe.set_ylabel("Equity ($)")

    closed = t[t.exit_ts <= end_t]
    n = len(closed)
    wr = 100 * (closed.pnl > 0).mean() if n else 0
    peak = e.cummax().iloc[-1]
    dd = cur - peak
    txt = (f"date {end_t.strftime('%Y-%m-%d')}\n"
           f"equity ${cur:,.0f}  ({100*(cur-CAP)/CAP:+.1f}%)\n"
           f"trades {n}   win {wr:.0f}%\n"
           f"drawdown ${dd:,.0f}")
    axe.text(0.012, 0.95, txt, transform=axe.transAxes, va="top", fontsize=10,
             family="monospace",
             bbox=dict(boxstyle="round", fc="#f5f5f5", ec=GREY, alpha=0.95))
    fig.tight_layout()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="gif + snapshots only")
    args = ap.parse_args()

    # snapshot PNGs at ~20% / 55% / 100% for inline display
    for tag, frac in [("early", 0.22), ("mid", 0.55), ("late", 0.999)]:
        draw(FRAMES[int((len(FRAMES) - 1) * frac)])
        fig.savefig(FIGDIR / f"kalman_replay_{tag}.png", dpi=95)
    print("snapshots saved")

    anim = FuncAnimation(fig, draw, frames=FRAMES, interval=120)

    gif = FIGDIR / "kalman_replay.gif"
    anim.save(gif, writer=PillowWriter(fps=10))
    print(f"saved {gif}  ({gif.stat().st_size/1e6:.1f} MB)")

    if not args.fast:
        try:
            mp4 = FIGDIR / "kalman_replay.mp4"
            anim.save(mp4, writer=FFMpegWriter(fps=15, bitrate=2400))
            print(f"saved {mp4}  ({mp4.stat().st_size/1e6:.1f} MB)")
        except Exception as e:
            print(f"mp4 skipped: {e}")


if __name__ == "__main__":
    main()
