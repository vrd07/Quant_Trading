#!/usr/bin/env python3
"""
Kalman SETUP explainer — dissect representative trades so the entry logic is
visible. For each example: price+Kalman+regime shading, the ADX trend gate, and
the OU z-score range gate, plus a checklist of every condition at the signal bar.

Kalman v2 decision logic (from kalman_regime_strategy.py + config_live_5000):
  REGIME = realized-vol: RV>MA(RV) -> TREND, else RANGE
  TREND BUY : close>Kalman (2 bars) & Kalman slope up & ADX>17 & strength>=0.50
  TREND SELL: close<Kalman (2 bars) & Kalman slope down & ADX>17 & strength>=0.75
              & HTF 1H close < EMA50   (bullish-drift guard)
  RANGE BUY : OU z < -2.0 & RSI < 42
  RANGE SELL: OU z > +2.0 & RSI > 65

Outputs reports/figs/kalman_setup_{trend_buy,trend_sell,range}.png
"""
import sys
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src.data.indicators import Indicators

spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(spec); spec.loader.exec_module(bt)

FIGDIR = ROOT / "reports/figs"; FIGDIR.mkdir(parents=True, exist_ok=True)
GREEN, RED, BLUE, ORANGE, GREY = "#1a9850", "#d73027", "#2c7fb8", "#f0a500", "#555"
plt.rcParams.update({"font.size": 10, "axes.grid": True, "grid.alpha": 0.25, "axes.axisbelow": True})

print("loading + recomputing indicators over full 2026 series ...")
bars = bt.load_15m_2026(); bars.index = bars.index.tz_localize(None)
close = bars["close"]
kal = Indicators.kalman_filter(close, q=1e-5, r=0.01)
adx = Indicators.adx(bars, period=14)
rsi = Indicators.rsi(bars, period=14)
zsc = Indicators.ou_zscore(close, kal, window=20)
reg = Indicators.rv_regime(close, rv_window=20, rv_ma_window=100)  # 1=trend 0=range
# HTF 1H EMA(50) for the SELL gate
h1 = close.resample("1h").last().dropna()
ema50_1h = h1.ewm(span=50, adjust=False).mean()
for s in (kal, adx, rsi, zsc, reg):
    s.index = bars.index

t = pd.read_csv(ROOT / "data/backtests/kalman_2026_fixed_trades.csv",
                parse_dates=["entry_ts", "exit_ts"])
t["entry_ts"] = t["entry_ts"].dt.tz_localize(None)
t["exit_ts"] = t["exit_ts"].dt.tz_localize(None)
pos = pd.Series(np.arange(len(bars)), index=bars.index)


def explain(trade, label, gate_lines, fname):
    e_ts, x_ts = trade["entry_ts"], trade["exit_ts"]
    w0, w1 = e_ts - pd.Timedelta(hours=18), x_ts + pd.Timedelta(hours=8)
    m = (bars.index >= w0) & (bars.index <= w1)
    wb = bars[m]
    xs = np.arange(len(wb))
    ip = pd.Series(xs, index=wb.index)
    xp = lambda ts: int(ip.index.get_indexer([ts], method="nearest")[0])
    ep, xpx = xp(e_ts), xp(x_ts)
    col = GREEN if trade["pnl"] > 0 else RED

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(15, 11), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1, 1.2]})

    # regime shading across all panels
    rv = reg[m].values
    for ax in (a1, a2, a3):
        start = 0
        for i in range(1, len(rv) + 1):
            if i == len(rv) or rv[i] != rv[start]:
                ax.axvspan(start, i - 1,
                           color=(GREEN if rv[start] == 1 else ORANGE), alpha=0.06)
                start = i

    # ---- P1: price + Kalman + entry/exit + SL/TP ----
    a1.plot(xs, wb["close"].values, color="#222", lw=1.4, label="price", zorder=4)
    a1.plot(xs, kal[m].values, color=BLUE, lw=2.2, label="Kalman line", zorder=3)
    a1.hlines(trade["tp"], ep, xpx, color=GREEN, lw=1.3, ls="--", label="TP +33pt")
    a1.hlines(trade["sl0"], ep, xpx, color=RED, lw=1.3, ls="--", label="SL −33pt")
    mk = "^" if trade["side"] == "buy" else "v"
    a1.scatter(ep, trade["entry"], marker=mk, s=240, color=col, edgecolor="k", lw=0.8, zorder=6)
    a1.scatter(xpx, trade["exit"], marker="o", s=90, color=col, edgecolor="k", lw=0.8, zorder=6)
    a1.axvline(ep - 1, color=GREY, ls=":", lw=1)   # signal bar (decision)
    a1.annotate("signal bar\n(decision)", xy=(ep - 1, kal[m].iloc[max(ep - 1, 0)]),
                xytext=(-95, 10), textcoords="offset points", fontsize=8, color=GREY,
                arrowprops=dict(arrowstyle="->", color=GREY))
    a1.legend(loc="upper left", ncol=4, fontsize=8, framealpha=0.9)
    a1.set_ylabel("Gold ($)")
    a1.set_title(f"{label}   ·   {e_ts.strftime('%Y-%m-%d %H:%M')}   ·   "
                 f"{trade['side'].upper()} {trade['mode'].upper()} → {trade['exit_reason']} "
                 f"{'+' if trade['pnl']>0 else ''}{trade['pnl']:.0f}$", fontsize=12)

    # gate checklist box
    a1.text(0.985, 0.04, "\n".join(gate_lines), transform=a1.transAxes, va="bottom",
            ha="right", fontsize=9.5, family="monospace",
            bbox=dict(boxstyle="round", fc="#fffef0", ec=GREY))

    # ---- P2: ADX (trend gate) ----
    a2.plot(xs, adx[m].values, color="#6a3d9a", lw=1.5)
    a2.axhline(17, color=RED, lw=1.2, ls="--")
    a2.text(0.5, 17, " ADX gate = 17 (TREND needs ADX>17)", color=RED, fontsize=8, va="bottom")
    a2.scatter(ep - 1, adx[m].iloc[max(ep - 1, 0)], color="#6a3d9a", s=60, zorder=5,
               edgecolor="k", lw=0.6)
    a2.set_ylabel("ADX")

    # ---- P3: OU z-score (range gate) ----
    a3.plot(xs, zsc[m].values, color="#0c7", lw=1.5)
    for y in (2.0, -2.0):
        a3.axhline(y, color=ORANGE, lw=1.2, ls="--")
    a3.axhline(0, color=GREY, lw=0.8)
    a3.text(0.5, 2.0, " RANGE SELL z>+2", color=ORANGE, fontsize=8, va="bottom")
    a3.text(0.5, -2.0, " RANGE BUY z<−2", color=ORANGE, fontsize=8, va="top")
    a3.scatter(ep - 1, zsc[m].iloc[max(ep - 1, 0)], color="#0c7", s=60, zorder=5,
               edgecolor="k", lw=0.6)
    a3.set_ylabel("OU z-score")

    ticks = np.linspace(0, len(wb) - 1, 7, dtype=int)
    a3.set_xticks(ticks)
    a3.set_xticklabels([wb.index[i].strftime("%b-%d %H:%M") for i in ticks], rotation=12, fontsize=8)
    # regime legend
    from matplotlib.patches import Patch
    a1.legend(handles=a1.get_legend().legend_handles + [
        Patch(facecolor=GREEN, alpha=0.15, label="TREND regime"),
        Patch(facecolor=ORANGE, alpha=0.15, label="RANGE regime")],
        loc="upper left", ncol=3, fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(FIGDIR / fname, dpi=95)
    plt.close(fig)
    print(f"  saved {fname}  ({trade['side']} {trade['mode']} {trade['pnl']:+.0f})")


def gates_at(trade):
    """Read indicator values at the signal bar (= bar before the fill)."""
    p = xp_full(trade["entry_ts"]) - 1
    sb = bars.index[p]
    return dict(
        ts=sb, regime=("TREND" if reg.iloc[p] == 1 else "RANGE"),
        close=close.iloc[p], kal=kal.iloc[p],
        slope=kal.iloc[p] - kal.iloc[p - 2], adx=adx.iloc[p],
        rsi=rsi.iloc[p], z=zsc.iloc[p],
        htf_close=h1.asof(sb), htf_ema=ema50_1h.asof(sb))

xp_full = lambda ts: int(pos.index.get_indexer([ts], method="nearest")[0])


def chk(ok):
    return "✓" if ok else "✗"

# ---- pick representative WINNING examples ----------------------------------
def pick(side, mode, after, win=True):
    sub = t[(t.side == side) & (t["mode"] == mode) & (t.entry_ts >= after)]
    if win:
        sub = sub[sub.pnl > 0]
    return sub.sort_values("entry_ts").iloc[0]

# TREND BUY (the bread-and-butter pattern) — from the March uptrend
tb = pick("buy", "trend", "2026-03-10")
g = gates_at(tb)
explain(tb, "SETUP A · TREND BUY  (ride price pulling above the Kalman line)", [
    f"REGIME    : {g['regime']:<5} {chk(g['regime']=='TREND')}  (RV>MA(RV))",
    f"close>Kalman: {g['close']:.1f} > {g['kal']:.1f}  {chk(g['close']>g['kal'])}",
    f"Kalman slope: {g['slope']:+.2f}  {chk(g['slope']>0)} (up)",
    f"ADX>17    : {g['adx']:.1f}  {chk(g['adx']>17)}",
    f"strength  : {tb['strength']:.2f}  {chk(tb['strength']>=0.50)} (>=0.50 BUY)",
    f"--> BUY @ {tb['entry']:.0f}, SL {tb['sl0']:.0f} / TP {tb['tp']:.0f}",
], "kalman_setup_trend_buy.png")

# TREND SELL (the 2026 winner) — from the May/Jun decline, needs HTF gate
ts_ = pick("sell", "trend", "2026-05-10")
g = gates_at(ts_)
explain(ts_, "SETUP B · TREND SELL  (short price below Kalman, HTF-bearish only)", [
    f"REGIME     : {g['regime']:<5} {chk(g['regime']=='TREND')}",
    f"close<Kalman: {g['close']:.1f} < {g['kal']:.1f}  {chk(g['close']<g['kal'])}",
    f"Kalman slope: {g['slope']:+.2f}  {chk(g['slope']<0)} (down)",
    f"ADX>17     : {g['adx']:.1f}  {chk(g['adx']>17)}",
    f"HTF 1H<EMA50: {g['htf_close']:.0f} < {g['htf_ema']:.0f}  {chk(g['htf_close']<g['htf_ema'])} (bearish)",
    f"strength   : {ts_['strength']:.2f}  {chk(ts_['strength']>=0.75)} (>=0.75 SELL)",
    f"--> SELL @ {ts_['entry']:.0f}, SL {ts_['sl0']:.0f} / TP {ts_['tp']:.0f}",
], "kalman_setup_trend_sell.png")

# RANGE fade — z-score extreme + RSI
try:
    rg = pick(t[t["mode"] == "range"].iloc[0]["side"], "range", "2026-01-01")
except Exception:
    rg = t[t["mode"] == "range"].sort_values("entry_ts").iloc[0]
g = gates_at(rg)
if rg["side"] == "buy":
    rng_lines = [
        f"REGIME   : {g['regime']:<5} {chk(g['regime']=='RANGE')}  (RV<=MA(RV))",
        f"OU z<−2.0: {g['z']:+.2f}  {chk(g['z']<-2.0)} (stretched low)",
        f"RSI<42   : {g['rsi']:.0f}  {chk(g['rsi']<42)}",
        f"--> BUY the dip @ {rg['entry']:.0f}",
    ]
else:
    rng_lines = [
        f"REGIME   : {g['regime']:<5} {chk(g['regime']=='RANGE')}",
        f"OU z>+2.0: {g['z']:+.2f}  {chk(g['z']>2.0)} (stretched high)",
        f"RSI>65   : {g['rsi']:.0f}  {chk(g['rsi']>65)}",
        f"--> SELL the rip @ {rg['entry']:.0f}",
    ]
explain(rg, "SETUP C · RANGE FADE  (mean-revert OU z-score extreme)", rng_lines,
        "kalman_setup_range.png")

print("done.")
