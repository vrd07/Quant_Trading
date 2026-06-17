#!/usr/bin/env python3
"""
Animated Kalman 2026 replay WITH THE SETUP shown — so the patterns are visible.

Four synced panels, one trading day per frame:
  1 price + Kalman line + REGIME shading (green=trend, orange=range) + trades
  2 ADX with the 17 trend-gate line     (TREND fires only when ADX>17)
  3 OU z-score with +/-2 bands           (RANGE fades z extremes)
  4 equity building up + live stats

The colored background + the two indicator panels reveal WHY each trade fires:
trend entries cluster where price leaves the Kalman line in green (ADX>17);
range fades cluster where z-score pierces the +/-2 bands in orange.

Outputs reports/figs/kalman_setup_replay.mp4 + .gif + 3 snapshot PNGs.
  python scripts/animate_kalman_setup_2026.py [--fast]
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
from matplotlib.patches import Rectangle, Patch
from matplotlib.lines import Line2D
from matplotlib.animation import FuncAnimation, PillowWriter, FFMpegWriter

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src.data.indicators import Indicators

spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)

FIGDIR = ROOT / "reports/figs"; FIGDIR.mkdir(parents=True, exist_ok=True)
CAP = 5000.0
GREEN, RED, BLUE, ORANGE, PURPLE, GREY = "#1a9850", "#d73027", "#2c7fb8", "#f0a500", "#6a3d9a", "#666"
WIN_DAYS = 10
plt.rcParams.update({"font.size": 9, "axes.grid": True, "grid.alpha": 0.25, "axes.axisbelow": True})

print("loading + indicators ...")
bars = bt.load_15m_2026(); bars.index = bars.index.tz_localize(None)
close = bars["close"]
kal = Indicators.kalman_filter(close, q=1e-5, r=0.01)
adx = Indicators.adx(bars, period=14)
zsc = Indicators.ou_zscore(close, kal, window=20)
reg = Indicators.rv_regime(close, rv_window=20, rv_ma_window=100)
for s in (kal, adx, zsc, reg):
    s.index = bars.index

t = pd.read_csv(ROOT / "data/backtests/kalman_2026_fixed_trades.csv",
                parse_dates=["entry_ts", "exit_ts"]).sort_values("exit_ts").reset_index(drop=True)
t["entry_ts"] = t["entry_ts"].dt.tz_localize(None)
t["exit_ts"] = t["exit_ts"].dt.tz_localize(None)
eq = pd.concat([pd.Series([CAP], index=[bars.index[0]]),
                CAP + t.set_index("exit_ts").pnl.cumsum()])
EQ_LO, EQ_HI = eq.min() - 150, eq.max() + 150
day_ends = bars.groupby(bars.index.normalize()).apply(lambda d: d.index.max())
FRAMES = list(day_ends.values)
print(f"{len(FRAMES)} daily frames")

fig, (axp, axa, axz, axe) = plt.subplots(
    4, 1, figsize=(14, 12), dpi=84, gridspec_kw={"height_ratios": [3, 1, 1.1, 1.8]})


def shade_regime(ax, sl):
    v = reg[sl].values; ix = reg[sl].index
    if len(v) == 0:
        return
    s = 0
    for i in range(1, len(v) + 1):
        if i == len(v) or v[i] != v[s]:
            ax.axvspan(ix[s], ix[i - 1], color=(GREEN if v[s] == 1 else ORANGE), alpha=0.07)
            s = i


def draw(end_t):
    end_t = pd.Timestamp(end_t)
    for ax in (axp, axa, axz, axe):
        ax.clear()
    w0 = end_t - pd.Timedelta(days=WIN_DAYS)
    sl = (bars.index >= w0) & (bars.index <= end_t)
    wb = bars[sl]
    vis = t[(t.entry_ts <= end_t) & (t.exit_ts >= w0)]
    cur_reg = "TREND" if reg.asof(end_t) == 1 else "RANGE"

    # ---- 1 price + kalman + regime + trades ----
    shade_regime(axp, sl)
    axp.plot(wb.index, wb["close"].values, color="#222", lw=1.2, zorder=4)
    axp.plot(kal[sl].index, kal[sl].values, color=BLUE, lw=1.9, zorder=3)
    for _, r in vis.iterrows():
        col = GREEN if r.pnl > 0 else RED
        e_t, x_t = max(r.entry_ts, w0), min(r["exit_ts"], end_t)
        lo_y, hi_y = min(r.sl0, r.tp), max(r.sl0, r.tp)
        axp.add_patch(Rectangle((mdates.date2num(e_t), lo_y),
                                mdates.date2num(x_t) - mdates.date2num(e_t), hi_y - lo_y,
                                facecolor=col, alpha=0.10, edgecolor=col, lw=0.8, ls="--", zorder=1))
        if r.entry_ts >= w0:
            axp.scatter(r.entry_ts, r.entry, marker=("^" if r.side == "buy" else "v"),
                        s=120, color=col, edgecolor="k", lw=0.5, zorder=5)
        if r["exit_ts"] <= end_t:
            axp.scatter(r["exit_ts"], r["exit"], marker="o", s=45, color=col,
                        edgecolor="k", lw=0.5, zorder=5)
    if len(wb):
        pad = (wb["close"].max() - wb["close"].min()) * 0.12 + 5
        axp.set_ylim(wb["close"].min() - pad, wb["close"].max() + pad)
    axp.set_xlim(w0, end_t); axp.set_ylabel("Gold ($)")
    axp.set_title(f"Kalman 2026 replay + SETUP  —  {end_t.strftime('%Y-%m-%d')}   "
                  f"current regime: {cur_reg}", fontsize=12)
    axp.legend(handles=[
        Line2D([0], [0], color="#222", lw=1.2, label="price"),
        Line2D([0], [0], color=BLUE, lw=1.9, label="Kalman"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=9, label="▲BUY ▼SELL"),
        Patch(facecolor=GREEN, alpha=0.18, label="TREND"),
        Patch(facecolor=ORANGE, alpha=0.18, label="RANGE"),
    ], loc="upper left", ncol=5, fontsize=8, framealpha=0.9)

    # ---- 2 ADX (trend gate) ----
    shade_regime(axa, sl)
    axa.plot(adx[sl].index, adx[sl].values, color=PURPLE, lw=1.4)
    axa.axhline(17, color=RED, lw=1.1, ls="--")
    axa.text(w0, 17, " ADX>17 → TREND ok", color=RED, fontsize=8, va="bottom")
    for _, r in vis[vis.entry_ts >= w0].iterrows():
        axa.scatter(r.entry_ts, adx.asof(r.entry_ts), s=22,
                    color=(GREEN if r.pnl > 0 else RED), zorder=5)
    axa.set_xlim(w0, end_t); axa.set_ylabel("ADX")

    # ---- 3 OU z-score (range gate) ----
    shade_regime(axz, sl)
    axz.plot(zsc[sl].index, zsc[sl].values, color="#0c7", lw=1.4)
    for y in (2, -2):
        axz.axhline(y, color=ORANGE, lw=1.1, ls="--")
    axz.axhline(0, color=GREY, lw=0.7)
    axz.text(w0, 2, " z>+2 → SELL fade", color=ORANGE, fontsize=8, va="bottom")
    axz.text(w0, -2, " z<−2 → BUY fade", color=ORANGE, fontsize=8, va="top")
    for _, r in vis[vis.entry_ts >= w0].iterrows():
        axz.scatter(r.entry_ts, zsc.asof(r.entry_ts), s=22,
                    color=(GREEN if r.pnl > 0 else RED), zorder=5)
    axz.set_xlim(w0, end_t); axz.set_ylabel("OU z")

    # ---- 4 equity ----
    e = eq[eq.index <= end_t]
    axe.plot(e.index, e.values, color=BLUE, lw=1.5)
    axe.fill_between(e.index, CAP, e.values, where=e.values >= CAP, color=GREEN, alpha=0.15)
    axe.fill_between(e.index, CAP, e.values, where=e.values < CAP, color=RED, alpha=0.15)
    axe.axhline(CAP, color=GREY, lw=1, ls="--")
    cur = e.iloc[-1]; axe.scatter([e.index[-1]], [cur], color=BLUE, s=40, zorder=5)
    axe.set_xlim(bars.index[0], bars.index[-1]); axe.set_ylim(EQ_LO, EQ_HI)
    axe.xaxis.set_major_formatter(mdates.DateFormatter("%b")); axe.set_ylabel("Equity ($)")
    closed = t[t.exit_ts <= end_t]; n = len(closed)
    wr = 100 * (closed.pnl > 0).mean() if n else 0
    dd = cur - e.cummax().iloc[-1]
    axe.text(0.012, 0.95,
             f"date {end_t.strftime('%Y-%m-%d')}\nequity ${cur:,.0f} ({100*(cur-CAP)/CAP:+.1f}%)\n"
             f"trades {n}  win {wr:.0f}%\ndrawdown ${dd:,.0f}",
             transform=axe.transAxes, va="top", fontsize=9, family="monospace",
             bbox=dict(boxstyle="round", fc="#f5f5f5", ec=GREY, alpha=0.95))
    for ax in (axp, axa, axz):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b-%d"))
    fig.tight_layout()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--fast", action="store_true")
    args = ap.parse_args()
    for tag, frac in [("early", 0.22), ("mid", 0.55), ("late", 0.999)]:
        draw(FRAMES[int((len(FRAMES) - 1) * frac)])
        fig.savefig(FIGDIR / f"kalman_setup_replay_{tag}.png", dpi=92)
    print("snapshots saved")
    anim = FuncAnimation(fig, draw, frames=FRAMES, interval=120)
    gif = FIGDIR / "kalman_setup_replay.gif"
    anim.save(gif, writer=PillowWriter(fps=10))
    print(f"saved {gif} ({gif.stat().st_size/1e6:.1f} MB)")
    if not args.fast:
        try:
            mp4 = FIGDIR / "kalman_setup_replay.mp4"
            anim.save(mp4, writer=FFMpegWriter(fps=15, bitrate=2600))
            print(f"saved {mp4} ({mp4.stat().st_size/1e6:.1f} MB)")
        except Exception as e:
            print(f"mp4 skipped: {e}")


if __name__ == "__main__":
    main()
