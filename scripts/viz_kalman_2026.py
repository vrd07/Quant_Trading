#!/usr/bin/env python3
"""
Visual demo of the Kalman 2026 fixed-parameter backtest.

Reads the trade log + signal cache produced by backtest_kalman_2026_fixed.py
and renders four PNGs under reports/figs/:
  1. kalman_trade_anatomy.png  -- price vs Kalman line, entries/exits, SL/TP brackets
  2. kalman_equity_dd.png      -- equity curve + underwater drawdown
  3. kalman_monthly.png        -- monthly net P&L (green/red)
  4. kalman_patterns.png       -- side / regime / exit-reason / hour / daily + per-trade P&L
"""
import sys
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src.data.indicators import Indicators

# Reuse the backtest module's loader so the bars are byte-identical.
spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

FIGDIR = ROOT / "reports/figs"
FIGDIR.mkdir(parents=True, exist_ok=True)
CAP = 5000.0
GREEN, RED, BLUE, GREY = "#1a9850", "#d73027", "#2c7fb8", "#888888"

plt.rcParams.update({"figure.dpi": 110, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.axisbelow": True})

# ---------------------------------------------------------------------------
print("loading bars, signals, trades ...")
bars = bt.load_15m_2026()
kal = Indicators.kalman_filter(bars["close"], q=1e-5, r=0.01)
t = pd.read_csv(ROOT / "data/backtests/kalman_2026_fixed_trades.csv",
                parse_dates=["entry_ts", "exit_ts"])
t = t.sort_values("exit_ts").reset_index(drop=True)
t["entry_ts"] = t["entry_ts"].dt.tz_localize(None)
t["exit_ts"] = t["exit_ts"].dt.tz_localize(None)
bars.index = bars.index.tz_localize(None)
kal.index = bars.index


# ===========================================================================
# FIG 1 — trade anatomy (auto-pick a readable ~10-trade window)
# ===========================================================================
def pick_window(trades, span_days=5, target=7):
    best, best_score = None, -1e9
    for d in pd.to_datetime(trades["entry_ts"].dt.date.unique()):
        lo, hi = d, d + pd.Timedelta(days=span_days)
        sub = trades[(trades.entry_ts >= lo) & (trades.entry_ts < hi)]
        if not (4 <= len(sub) <= 9):
            continue
        has_mix = sub.pnl.gt(0).any() and sub.pnl.lt(0).any() \
            and (sub.side == "buy").any() and (sub.side == "sell").any()
        score = -abs(len(sub) - target) + (5 if has_mix else 0)
        if score > best_score:
            best, best_score = (lo, hi), score
    return best

lo, hi = pick_window(t)
wt = t[(t.entry_ts >= lo) & (t.entry_ts < hi)].copy()
# Frame the bars from a little before the first entry to a little after the last
# exit, then plot on a GAP-FREE positional axis (weekends/overnights removed).
pad = pd.Timedelta(hours=6)
wb = bars[(bars.index >= wt.entry_ts.min() - pad) & (bars.index <= wt.exit_ts.max() + pad)]
wk = kal.reindex(wb.index)
xs = np.arange(len(wb))
ipos = pd.Series(xs, index=wb.index)
def xpos(ts):
    return int(ipos.index.get_indexer([ts], method="nearest")[0])
print(f"anatomy window: {wb.index[0]} -> {wb.index[-1]}, {len(wt)} trades")

fig, ax = plt.subplots(figsize=(16, 8))
ax.plot(xs, wb["close"].values, color="#222", lw=1.3, label="XAUUSD 15m close", zorder=3)
ax.plot(xs, wk.values, color=BLUE, lw=2.0, label="Kalman line", zorder=2)

for _, r in wt.iterrows():
    win = r.pnl > 0
    col = GREEN if win else RED
    ep, xp = xpos(r.entry_ts), xpos(r["exit_ts"])
    lo_y, hi_y = min(r.sl0, r.tp), max(r.sl0, r.tp)
    ax.add_patch(Rectangle((ep, lo_y), max(xp - ep, 0.6), hi_y - lo_y,
                           facecolor=col, alpha=0.12, edgecolor=col, lw=1.0, ls="--", zorder=1))
    ax.hlines(r.tp, ep, xp, color=GREEN, lw=1.1, alpha=0.7, zorder=2)
    ax.hlines(r.sl0, ep, xp, color=RED, lw=1.1, alpha=0.7, zorder=2)
    mk = "^" if r.side == "buy" else "v"
    ax.scatter(ep, r.entry, marker=mk, s=170, color=col, edgecolor="k", lw=0.7, zorder=6)
    ax.scatter(xp, r["exit"], marker="o", s=70, color=col, edgecolor="k", lw=0.7, zorder=6)
    ax.plot([ep, xp], [r.entry, r["exit"]], color=col, lw=1.0, alpha=0.8, zorder=5)
    ax.text((ep + xp) / 2, hi_y + 2, f"{'+' if r.pnl > 0 else ''}{r.pnl:.0f}$",
            ha="center", fontsize=8, color=col, fontweight="bold")

# Full teaching annotation on the first trade
r0 = wt.iloc[0]
ax.annotate(
    f"{r0.side.upper()} fills @ {r0.entry:.0f} (next-bar open)\n"
    f"SL {r0.sl0:.0f} (−33pt, red) · TP {r0.tp:.0f} (+33pt, green)\n"
    f"price hits the {'TP' if r0.pnl>0 else 'SL'} edge → {r0.exit_reason} "
    f"{'+' if r0.pnl>0 else ''}{r0.pnl:.0f}$",
    xy=(xpos(r0.entry_ts), r0.entry), xytext=(20, 70), textcoords="offset points",
    fontsize=9.5, bbox=dict(boxstyle="round", fc="#fffbe6", ec=GREY),
    arrowprops=dict(arrowstyle="->", color="k"))

from matplotlib.lines import Line2D
legend = [
    Line2D([0], [0], color="#222", lw=1.3, label="XAUUSD 15m close"),
    Line2D([0], [0], color=BLUE, lw=2.0, label="Kalman line (the model)"),
    Line2D([0], [0], color=GREEN, lw=1.1, label="TP +33pt"),
    Line2D([0], [0], color=RED, lw=1.1, label="SL −33pt"),
    Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=12, label="entry (▲BUY ▼SELL)"),
    Line2D([0], [0], marker="o", color="w", markerfacecolor=GREY, markeredgecolor="k", ms=9, label="exit"),
]
ax.legend(handles=legend, loc="best", framealpha=0.92, ncol=2, fontsize=9)
# Date tick labels on the compressed axis
ticks = np.linspace(0, len(wb) - 1, 8, dtype=int)
ax.set_xticks(ticks)
ax.set_xticklabels([wb.index[i].strftime("%b-%d %H:%M") for i in ticks], rotation=15, fontsize=8)
ax.set_title("1 · Trade anatomy — how each Kalman trade plays out (weekend gaps removed)\n"
             "green box edge = TP (+33pt), red box edge = SL (−33pt); exit = whichever the price touches first",
             fontsize=12)
ax.set_ylabel("Gold price ($)")
ax.margins(x=0.01)
fig.tight_layout()
fig.savefig(FIGDIR / "kalman_trade_anatomy.png")
plt.close(fig)


# ===========================================================================
# FIG 2 — equity curve + drawdown
# ===========================================================================
eq = CAP + t.pnl.cumsum()
eq.index = t.exit_ts
eq = pd.concat([pd.Series([CAP], index=[bars.index[0]]), eq])
peak = eq.cummax()
dd = eq - peak

fig, (a1, a2) = plt.subplots(2, 1, figsize=(16, 9), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
a1.plot(eq.index, eq.values, color=BLUE, lw=1.6)
a1.fill_between(eq.index, CAP, eq.values, where=eq.values >= CAP, color=GREEN, alpha=0.12)
a1.fill_between(eq.index, CAP, eq.values, where=eq.values < CAP, color=RED, alpha=0.12)
a1.axhline(CAP, color=GREY, lw=1, ls="--")
pk_t, tr_t = peak[dd.idxmin():].index[0] if False else eq.loc[:dd.idxmin()].idxmax(), dd.idxmin()
a1.scatter([pk_t], [eq.loc[pk_t]], color=GREEN, zorder=5, s=60)
a1.scatter([tr_t], [eq.loc[tr_t]], color=RED, zorder=5, s=60)
a1.annotate(f"peak ${eq.loc[pk_t]:,.0f}\n{pk_t.date()}", (pk_t, eq.loc[pk_t]),
            textcoords="offset points", xytext=(-10, 12), fontsize=9, color=GREEN)
a1.annotate(f"trough ${eq.loc[tr_t]:,.0f}\n{tr_t.date()}", (tr_t, eq.loc[tr_t]),
            textcoords="offset points", xytext=(-20, -32), fontsize=9, color=RED)
a1.set_title(f"2 · Equity curve — ${CAP:,.0f} → ${eq.iloc[-1]:,.0f}  "
             f"(+{100*(eq.iloc[-1]-CAP)/CAP:.1f}%, PF 1.09, 608 trades)", fontsize=12)
a1.set_ylabel("Equity ($)")

a2.fill_between(dd.index, dd.values, 0, color=RED, alpha=0.35)
a2.plot(dd.index, dd.values, color=RED, lw=0.8)
a2.set_ylabel("Drawdown ($)")
a2.set_title(f"Underwater — max DD ${dd.min():,.0f} ({100*(dd/peak).min():.1f}%), "
             f"slow {(tr_t - pk_t).days}-day bleed (Apr→Jun)", fontsize=10)
a2.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
fig.tight_layout()
fig.savefig(FIGDIR / "kalman_equity_dd.png")
plt.close(fig)


# ===========================================================================
# FIG 3 — monthly net P&L
# ===========================================================================
mp = t.groupby("month").pnl.sum()
fig, ax = plt.subplots(figsize=(11, 6))
cols = [GREEN if v > 0 else RED for v in mp.values]
b = ax.bar(mp.index, mp.values, color=cols, edgecolor="k", lw=0.5)
ax.axhline(0, color="k", lw=0.8)
for rect, v in zip(b, mp.values):
    ax.text(rect.get_x() + rect.get_width()/2, v + (20 if v > 0 else -45),
            f"${v:,.0f}", ha="center", fontsize=10, fontweight="bold")
ax.set_title("3 · Monthly net P&L  (Jan & Jun strong; Feb top & Apr war-whipsaw negative)", fontsize=12)
ax.set_ylabel("Net P&L ($)")
fig.tight_layout()
fig.savefig(FIGDIR / "kalman_monthly.png")
plt.close(fig)


# ===========================================================================
# FIG 4 — patterns dashboard
# ===========================================================================
def barstats(ax, key, order, title):
    rows = []
    for k in order:
        sub = t[t[key] == k]
        if len(sub):
            net = sub.pnl.sum()
            wr = 100 * (sub.pnl > 0).mean()
            rows.append((str(k), net, wr, len(sub)))
    labels = [r[0] for r in rows]
    nets = [r[1] for r in rows]
    cols = [GREEN if n > 0 else RED for n in nets]
    bb = ax.bar(labels, nets, color=cols, edgecolor="k", lw=0.4)
    for rect, r in zip(bb, rows):
        ax.text(rect.get_x()+rect.get_width()/2, r[1],
                f"{r[2]:.0f}%\nn={r[3]}", ha="center",
                va="bottom" if r[1] > 0 else "top", fontsize=8)
    ax.axhline(0, color="k", lw=0.7)
    ax.set_title(title, fontsize=10)
    ax.set_ylabel("Net $")

fig, axs = plt.subplots(2, 3, figsize=(17, 9.5))
barstats(axs[0, 0], "side", ["buy", "sell"], "By side — SELL carried the down-year")
barstats(axs[0, 1], "mode", ["trend", "range"], "By regime — TREND is the edge, RANGE bleeds")
barstats(axs[0, 2], "exit_reason", ["take_profit", "stop_loss"],
         "By exit — binary at RR1.0 (no breakeven exits)")
barstats(axs[1, 0], "hour", sorted(t.hour.unique()), "By UTC entry hour")
axs[1, 0].tick_params(axis="x", labelsize=7)

# daily P&L
d = t.groupby(t.exit_ts.dt.date).pnl.sum()
axs[1, 1].hist(d.values, bins=30, color=BLUE, edgecolor="k", alpha=0.8)
axs[1, 1].axvline(0, color="k", lw=1)
axs[1, 1].axvline(d.mean(), color=GREEN, lw=1.5, ls="--", label=f"mean +${d.mean():.0f}")
axs[1, 1].axvline(d.median(), color=RED, lw=1.5, ls="--", label=f"median ${d.median():.0f}")
axs[1, 1].set_title(f"Daily P&L — only {100*(d>0).mean():.0f}% green days, fat green tail")
axs[1, 1].set_xlabel("Daily P&L ($)"); axs[1, 1].legend(fontsize=8)

# per-trade P&L
axs[1, 2].hist(t.pnl.values, bins=40, color=GREY, edgecolor="k", alpha=0.85)
axs[1, 2].axvline(0, color="k", lw=1)
axs[1, 2].set_title("Per-trade P&L — bimodal: +$66 (TP) / −$66 (SL) + gap tail")
axs[1, 2].set_xlabel("Trade P&L ($)")

fig.suptitle("4 · Kalman 2026 pattern dashboard", fontsize=13, y=1.0)
fig.tight_layout()
fig.savefig(FIGDIR / "kalman_patterns.png")
plt.close(fig)

print("saved 4 figures to", FIGDIR)
for p in ["kalman_trade_anatomy", "kalman_equity_dd", "kalman_monthly", "kalman_patterns"]:
    print("  ", FIGDIR / (p + ".png"))
