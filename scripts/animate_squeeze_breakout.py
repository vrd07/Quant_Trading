#!/usr/bin/env python3
"""
Animated bar-by-bar replay of the squeeze_breakout XAUUSD 15m backtest, rendered
to a VIDEO (mp4 + gif) so it always plays and the candles always show — no
browser/Plotly animation quirks.

Two synced panels, advancing one 15m bar per frame (default):
  TOP    a scrolling ~3-day candlestick window of gold, with each trade's entry
         (^/v), exit (o), and live SL (red) / TP (green) brackets drawn from
         entry to the bar they fire on. A grey "now" line marks the latest bar.
  BOTTOM the equity curve building up + a live stats readout
         (trades / win% / realized RR / P&L / drawdown).

Exit timestamps aren't in the trades CSV, so they're reconstructed by walking
the 15m bars forward from each entry to the first bar that touches the recorded
exit price (backtest_end trades exit on the last bar).

  python scripts/animate_squeeze_breakout.py                       # mp4 + gif, Jun 8-15
  python scripts/animate_squeeze_breakout.py --start 2026-05-01 --end 2026-06-19
  python scripts/animate_squeeze_breakout.py --step 2 --fast       # every 30m, gif only
  python scripts/animate_squeeze_breakout.py --trades <path.csv>

Output: reports/figs/squeeze_breakout_replay.mp4 (+ .gif) — auto-opens the mp4.
"""
import sys
import argparse
import subprocess
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
BARS_CSV = ROOT / "data/historical/XAUUSD_5m_real.csv"
TRADES_CSV = ROOT / "data/backtests/backtest_result_squeeze_breakout_trades.csv"
FIGDIR = ROOT / "reports/figs"

GREEN, RED, BLUE, GREY = "#1a9850", "#d73027", "#2c7fb8", "#666666"
UP, DN = "#26a69a", "#ef5350"
CAP = 25_000.0  # the run was on the 25k config (matches uniform -$99.06 / 0.03 lot)

plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.22,
                     "axes.axisbelow": True})


def load_15m(start, end):
    df = pd.read_csv(BARS_CSV, parse_dates=["timestamp"]).set_index("timestamp")
    df.index = df.index.tz_localize(None)
    df = df.loc[(df.index >= start) & (df.index <= end)]
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    bars = df.resample("15min", label="left", closed="left").agg(agg).dropna()
    return bars


def reconstruct_exits(trades, bars):
    out = []
    for _, r in trades.iterrows():
        fwd = bars[bars.index > r.entry_ts]
        if r.exit_reason == "backtest_end" or fwd.empty:
            out.append(bars.index[-1]); continue
        px = r.exit_price
        if r.side == "BUY":
            hit = fwd[fwd["high"] >= px - 1e-6] if px > r.entry_price else fwd[fwd["low"] <= px + 1e-6]
        else:
            hit = fwd[fwd["low"] <= px + 1e-6] if px < r.entry_price else fwd[fwd["high"] >= px - 1e-6]
        out.append(hit.index[0] if len(hit) else fwd.index[-1])
    trades = trades.copy(); trades["exit_ts"] = out
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-08")
    ap.add_argument("--end", default="2026-06-15")
    ap.add_argument("--trades", default=str(TRADES_CSV))
    ap.add_argument("--step", type=int, default=1, help="bars advanced per frame (1 = bar-by-bar)")
    ap.add_argument("--win-days", type=float, default=3.0, help="scrolling candle window width (days)")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--fast", action="store_true", help="gif + snapshots only (skip mp4)")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    bars = load_15m(start, end)
    bars["xnum"] = mdates.date2num(bars.index.to_pydatetime())
    print(f"loaded {len(bars)} 15m bars  {bars.index[0]} -> {bars.index[-1]}")

    t = pd.read_csv(args.trades, parse_dates=["timestamp"]).rename(columns={"timestamp": "entry_ts"})
    t["entry_ts"] = t["entry_ts"].dt.tz_localize(None)
    t = t[(t.entry_ts >= bars.index[0]) & (t.entry_ts <= bars.index[-1])].reset_index(drop=True)
    t = reconstruct_exits(t, bars).sort_values("exit_ts").reset_index(drop=True)
    print(f"{len(t)} trades in window")

    eq = pd.concat([pd.Series([CAP], index=[bars.index[0]]),
                    CAP + t.set_index("exit_ts").pnl.cumsum()])
    EQ_LO, EQ_HI = eq.min() - 200, eq.max() + 200

    BARW = (15 / 1440) * 0.7   # candle body width in date-units (15m bar)
    WIN = pd.Timedelta(days=args.win_days)

    idx = list(range(0, len(bars), max(1, args.step)))
    if idx[-1] != len(bars) - 1:
        idx.append(len(bars) - 1)
    FRAMES = [bars.index[i] for i in idx]
    print(f"{len(FRAMES)} frames (step={args.step})")

    fig, (axp, axe) = plt.subplots(2, 1, figsize=(14, 9), dpi=90,
                                   gridspec_kw={"height_ratios": [3, 2]})

    def draw(now):
        now = pd.Timestamp(now)
        axp.clear(); axe.clear()
        w0 = now - WIN
        wb = bars[(bars.index >= w0) & (bars.index <= now)]

        # ---- candles ----
        if len(wb):
            up = wb[wb.close >= wb.open]; dn = wb[wb.close < wb.open]
            axp.vlines(up.xnum, up.low, up.high, color=UP, lw=0.7, zorder=2)
            axp.vlines(dn.xnum, dn.low, dn.high, color=DN, lw=0.7, zorder=2)
            for sub, col in ((up, UP), (dn, DN)):
                for x, o, c in zip(sub.xnum, sub.open, sub.close):
                    axp.add_patch(Rectangle((x - BARW / 2, min(o, c)), BARW,
                                            max(abs(c - o), 0.05), facecolor=col,
                                            edgecolor=col, lw=0.5, zorder=3))

        # ---- trades visible in window ----
        vis = t[(t.entry_ts <= now) & (t.exit_ts >= w0)]
        for _, r in vis.iterrows():
            col = GREEN if r.pnl > 0 else (RED if r.pnl < 0 else GREY)
            e_x = mdates.date2num(max(r.entry_ts, w0))
            x_now = mdates.date2num(min(r.exit_ts, now))
            axp.hlines(r.stop_loss, e_x, x_now, color=RED, lw=1, ls=":", zorder=4)
            axp.hlines(r.take_profit, e_x, x_now, color=GREEN, lw=1, ls=":", zorder=4)
            if r.entry_ts >= w0:
                axp.scatter(mdates.date2num(r.entry_ts), r.entry_price,
                            marker="^" if r.side == "BUY" else "v", s=150, color=col,
                            edgecolor="k", lw=0.7, zorder=6)
            if r.exit_ts <= now:
                axp.scatter(mdates.date2num(r.exit_ts), r.exit_price, marker="o", s=60,
                            color=col, edgecolor="k", lw=0.7, zorder=6)

        axp.axvline(mdates.date2num(now), color="#444", lw=1.2, ls="--", zorder=5)
        if len(wb):
            pad = (wb.high.max() - wb.low.min()) * 0.10 + 1
            axp.set_ylim(wb.low.min() - pad, wb.high.max() + pad)
        axp.set_xlim(mdates.date2num(w0), mdates.date2num(now + pd.Timedelta(hours=2)))
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%b-%d %H:%M"))
        axp.set_ylabel("Gold ($)")
        axp.set_title(f"squeeze_breakout XAUUSD 15m  —  {now.strftime('%Y-%m-%d %H:%M')}   "
                      f"(scrolling {args.win_days:.0f}-day window, bar-by-bar)", fontsize=12)
        axp.legend(handles=[
            Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=11, label="BUY"),
            Line2D([0], [0], marker="v", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=11, label="SELL"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor=GREEN, markeredgecolor="k", ms=9, label="win exit"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor=RED, markeredgecolor="k", ms=9, label="loss exit"),
            Line2D([0], [0], color=RED, ls=":", label="SL"),
            Line2D([0], [0], color=GREEN, ls=":", label="TP"),
        ], loc="upper left", ncol=6, fontsize=8, framealpha=0.9)

        # ---- equity + stats ----
        e = eq[eq.index <= now]
        axe.plot(e.index, e.values, color=BLUE, lw=1.7, zorder=3)
        axe.fill_between(e.index, CAP, e.values, where=e.values >= CAP, color=GREEN, alpha=0.15)
        axe.fill_between(e.index, CAP, e.values, where=e.values < CAP, color=RED, alpha=0.15)
        axe.axhline(CAP, color=GREY, lw=1, ls="--")
        axe.scatter([e.index[-1]], [e.iloc[-1]], color=BLUE, s=40, zorder=5)
        axe.set_xlim(bars.index[0], bars.index[-1])
        axe.set_ylim(EQ_LO, EQ_HI)
        axe.xaxis.set_major_formatter(mdates.DateFormatter("%b-%d"))
        axe.set_ylabel("Equity ($)")

        closed = t[t.exit_ts <= now]
        n = len(closed); wins = int((closed.pnl > 0).sum())
        wr = 100 * wins / n if n else 0
        avgw = closed[closed.pnl > 0].pnl.mean() if wins else 0
        avgl = closed[closed.pnl < 0].pnl.mean() if (n - wins) else 0
        rr = abs(avgw / avgl) if avgl else 0
        cur = e.iloc[-1]; dd = cur - e.cummax().iloc[-1]
        txt = (f"{now.strftime('%Y-%m-%d %H:%M')}\n"
               f"equity ${cur:,.0f}  ({100*(cur-CAP)/CAP:+.2f}%)\n"
               f"trades {n}   win {wr:.0f}%   RR {rr:.2f}\n"
               f"avgW ${avgw:,.0f}   avgL ${avgl:,.0f}\n"
               f"drawdown ${dd:,.0f}")
        axe.text(0.012, 0.95, txt, transform=axe.transAxes, va="top", fontsize=10,
                 family="monospace", parse_math=False,
                 bbox=dict(boxstyle="round", fc="#f5f5f5", ec=GREY, alpha=0.95))
        fig.tight_layout()

    FIGDIR.mkdir(parents=True, exist_ok=True)

    # snapshot PNGs (proof it renders) at ~25% / 60% / 100%
    for tag, frac in [("early", 0.25), ("mid", 0.60), ("late", 0.999)]:
        draw(FRAMES[int((len(FRAMES) - 1) * frac)])
        fig.savefig(FIGDIR / f"squeeze_replay_{tag}.png", dpi=95)
    print("snapshots saved")

    anim = FuncAnimation(fig, draw, frames=FRAMES, interval=1000 / args.fps)

    gif = FIGDIR / "squeeze_breakout_replay.gif"
    anim.save(gif, writer=PillowWriter(fps=args.fps))
    print(f"saved {gif}  ({gif.stat().st_size/1e6:.1f} MB)")

    out = gif
    if not args.fast:
        try:
            mp4 = FIGDIR / "squeeze_breakout_replay.mp4"
            anim.save(mp4, writer=FFMpegWriter(fps=args.fps, bitrate=2600))
            print(f"saved {mp4}  ({mp4.stat().st_size/1e6:.1f} MB)")
            out = mp4
        except Exception as e:
            print(f"mp4 skipped: {e}")

    if not args.no_open:
        subprocess.run(["open", str(out)])


if __name__ == "__main__":
    main()
