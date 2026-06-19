#!/usr/bin/env python3
"""
Visualize the RISK ENGINE's expected Kalman SL/TP geometry.

Unlike backtest_kalman_2026_fixed.py / viz_kalman_2026.py (which use a FIXED
+/-33pt stop for the in-sample sim), this renders the ACTUAL stop-loss /
take-profit the LIVE RiskProcessor would set for every Kalman signal -- the
ATR-dynamic brackets the risk engine actually "needs":

    SL = entry -/+ sl_atr_multiplier x ATR           (3.0 x ATR)
    TP = entry +/- max(tp_atr_multiplier x ATR,       (4.0 x ATR -- the larger
                       sl_dist x kalman_min_tp_rr)      leg vs 3.0 x ATR x 1.0)
    => realized R:R ~ 1.33   (TP_dist / SL_dist = 4/3)

SL/TP are computed by the REAL src.risk.risk_processor.RiskProcessor with the
active config, so the same RR-floor rejection and broker-min-stop expansion the
live engine applies are applied here too. Signals come from the cached real
KalmanRegimeStrategy.on_bar() replay (backtest_kalman_2026_fixed.generate_signals).

The $ risk panel shows what the engine can actually deliver after the min-lot
floor: the per-trade $ cap (risk.strategy_risk_overrides.kalman_regime) can only
be honored in low volatility -- once 3 x ATR is wide enough that the implied lot
falls below the broker min lot, the floor forces a larger real risk.

Outputs (reports/figs/):
  1. kalman_risk_engine_brackets.png  -- SL/TP brackets on a real trade window
  2. kalman_risk_engine_rule.png      -- the rule schematic + ATR-scaling table
  3. kalman_risk_engine_dist.png      -- SL/TP distance + $risk distributions
"""
import sys
import argparse
import importlib.util
from pathlib import Path
from decimal import Decimal

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from src.core.types import Signal
from src.core.constants import OrderSide
from src.risk.risk_processor import RiskProcessor

# Reuse the backtest module's loader + signal cache so bars/signals are identical.
spec = importlib.util.spec_from_file_location("bt", ROOT / "scripts/backtest_kalman_2026_fixed.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

FIGDIR = ROOT / "reports/figs"
FIGDIR.mkdir(parents=True, exist_ok=True)
GREEN, RED, BLUE, GREY, AMBER = "#1a9850", "#d73027", "#2c7fb8", "#888888", "#b8860b"
plt.rcParams.update({"figure.dpi": 110, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.axisbelow": True,
                     # '$' in titles/labels is literal money, not LaTeX math.
                     "text.parse_math": False})


def active_config_path() -> Path:
    """Single source of truth: config/ACTIVE_CONFIG holds the live config path."""
    marker = (ROOT / "config/ACTIVE_CONFIG").read_text().strip().splitlines()[0].strip()
    p = Path(marker)
    return p if p.is_absolute() else ROOT / p


def implied_lot(sl_dist, risk_usd, value_per_lot, min_lot, lot_step, max_lot):
    """What the sizer ends up trading at the per-trade $ cap, after the min-lot
    floor. Returns (lot, realized_$risk). Matches the live floor behaviour: a
    sub-min raw lot is clamped UP to min_lot, inflating the real risk."""
    if risk_usd <= 0 or sl_dist <= 0:
        return min_lot, sl_dist * value_per_lot * min_lot
    raw = risk_usd / (sl_dist * value_per_lot)
    lot = np.floor(raw / lot_step) * lot_step
    lot = max(min_lot, min(max_lot, lot))
    return lot, sl_dist * value_per_lot * lot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="config yaml (default: whatever config/ACTIVE_CONFIG points at)")
    ap.add_argument("--refresh-signals", action="store_true",
                    help="re-run the slow on_bar() replay instead of using the cache")
    ap.add_argument("--window-days", type=float, default=5.0,
                    help="span of the FIG-1 detail window")
    args = ap.parse_args()

    cfg_path = Path(args.config) if args.config else active_config_path()
    if not cfg_path.is_absolute():
        cfg_path = ROOT / cfg_path
    cfg = yaml.safe_load(cfg_path.read_text())

    kcfg = cfg["strategies"]["kalman_regime"]
    risk_cfg = cfg.get("risk", {}) or {}
    sl_mult = float(kcfg.get("sl_atr_multiplier", 2.5))
    tp_mult = float(kcfg.get("tp_atr_multiplier", 4.0))
    min_tp_rr = float(risk_cfg.get("kalman_min_tp_rr", 2.0))
    risk_usd = float((risk_cfg.get("strategy_risk_overrides", {}) or {})
                     .get("kalman_regime", {}).get("risk_per_trade_usd", 0) or 0)

    sym = bt.build_symbol(cfg)
    value_per_lot = float(sym.value_per_lot)
    min_lot = float(sym.min_lot)
    lot_step = float(sym.lot_step)
    max_lot = float(sym.max_lot)

    print("=" * 74)
    print("KALMAN -- RISK-ENGINE SL/TP GEOMETRY")
    print("=" * 74)
    print(f"  config         : {cfg_path.name}")
    print(f"  SL             : {sl_mult} x ATR")
    print(f"  TP             : max({tp_mult} x ATR, SL_dist x {min_tp_rr})")
    print(f"  per-trade $cap : ${risk_usd:.0f}  (value/lot ${value_per_lot:.0f}, "
          f"min_lot {min_lot}, step {lot_step}, max_lot {max_lot})")

    bars = bt.load_15m_2026()
    sig_df = bt.generate_signals(bars, cfg, args.refresh_signals)
    if bars.index.tz is not None:
        bars.index = bars.index.tz_localize(None)
    closes = bars["close"].to_numpy(float)

    # ---- Run every signal through the REAL RiskProcessor -----------------
    rp = RiskProcessor(cfg)
    rows = []
    rejected = 0
    for _, s in sig_df.iterrows():
        bi = int(s["bar_idx"])
        atr = float(s["atr"])
        entry = float(closes[bi])
        side = OrderSide.BUY if str(s["side"]).upper() == "BUY" else OrderSide.SELL
        sig = Signal(
            strategy_name="kalman_regime", symbol=sym, side=side,
            strength=float(s["strength"]), entry_price=Decimal(str(entry)),
            metadata={"strategy": "kalman_regime", "atr": atr},
        )
        rp.calculate_stops(sig)
        if sig.stop_loss is None or sig.take_profit is None:
            rejected += 1
            continue
        sl, tp = float(sig.stop_loss), float(sig.take_profit)
        sl_dist, tp_dist = abs(entry - sl), abs(entry - tp)
        lot, risk_at_lot = implied_lot(sl_dist, risk_usd, value_per_lot,
                                       min_lot, lot_step, max_lot)
        rows.append({
            "bar_idx": bi, "ts": pd.Timestamp(s["signal_ts"]),
            "side": str(s["side"]).lower(), "strength": float(s["strength"]),
            "atr": atr, "entry": entry, "sl": sl, "tp": tp,
            "sl_dist": sl_dist, "tp_dist": tp_dist,
            "rr": (tp_dist / sl_dist) if sl_dist else np.nan,
            "lot": lot, "risk_usd": risk_at_lot,
        })

    g = pd.DataFrame(rows)
    ts = pd.to_datetime(g["ts"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    g["ts"] = ts
    atr_med = float(g.atr.median())
    print(f"  signals        : {len(sig_df)}  (RR-floor rejected: {rejected})")
    print(f"  median ATR     : {atr_med:.2f} pts  -> SL {sl_mult*atr_med:.1f}pt / "
          f"TP {tp_mult*atr_med:.1f}pt")
    print(f"  realized R:R   : {g.rr.median():.3f} (constant -- both legs scale with ATR)")
    cap_breach = 100 * (g.risk_usd > risk_usd + 0.01).mean() if risk_usd else 0.0
    print(f"  $risk/trade    : ${g.risk_usd.min():.0f}..${g.risk_usd.max():.0f} "
          f"(median ${g.risk_usd.median():.0f}); ${risk_usd:.0f} cap breached on "
          f"{cap_breach:.0f}% of signals (min-lot floor)")

    # ======================================================================
    # FIG 1 -- risk-engine SL/TP brackets on a readable signal window
    # ======================================================================
    def pick_window(span_days):
        best, score = None, -1e9
        for d in pd.to_datetime(pd.Series(g.ts.dt.date.unique())):
            lo, hi = d, d + pd.Timedelta(days=span_days)
            sub = g[(g.ts >= lo) & (g.ts < hi)]
            if not (4 <= len(sub) <= 8):
                continue
            mix = (sub.side == "buy").any() and (sub.side == "sell").any()
            sc = -abs(len(sub) - 6) + (4 if mix else 0)
            if sc > score:
                best, score = (lo, hi), sc
        if best is None:
            lo = pd.to_datetime(g.ts.dt.date.iloc[len(g) // 3])
            best = (lo, lo + pd.Timedelta(days=span_days))
        return best

    lo, hi = pick_window(args.window_days)
    wg = g[(g.ts >= lo) & (g.ts < hi)].copy()
    pad = pd.Timedelta(hours=6)
    wb = bars[(bars.index >= wg.ts.min() - pad) & (bars.index <= wg.ts.max() + pad)]
    xs = np.arange(len(wb))
    ipos = pd.Series(xs, index=wb.index)

    def xpos(t):
        return int(ipos.index.get_indexer([t], method="nearest")[0])

    fig, ax = plt.subplots(figsize=(16, 8))
    ax.plot(xs, wb["close"].values, color="#222", lw=1.3, zorder=4, label="XAUUSD 15m close")
    fwd = 10  # bars to extend each bracket forward (purely visual)
    for j, (_, r) in enumerate(wg.iterrows()):
        ep = xpos(r.ts)
        xr = min(ep + fwd, len(wb) - 1)
        col = BLUE if r.side == "buy" else AMBER
        # SL zone (red) + TP zone (green) between entry and each level
        ax.add_patch(Rectangle((ep, min(r.entry, r.sl)), xr - ep, abs(r.entry - r.sl),
                               facecolor=RED, alpha=0.13, edgecolor="none", zorder=1))
        ax.add_patch(Rectangle((ep, min(r.entry, r.tp)), xr - ep, abs(r.entry - r.tp),
                               facecolor=GREEN, alpha=0.13, edgecolor="none", zorder=1))
        ax.hlines(r.sl, ep, xr, color=RED, lw=1.5, ls="--", zorder=3)
        ax.hlines(r.tp, ep, xr, color=GREEN, lw=1.5, ls="--", zorder=3)
        ax.hlines(r.entry, ep, xr, color=col, lw=1.0, ls=":", zorder=3)
        mk = "^" if r.side == "buy" else "v"
        ax.scatter(ep, r.entry, marker=mk, s=150, color=col, edgecolor="k", lw=0.7, zorder=6)
        if j < 3:  # label the first few so it isn't cluttered
            ax.text(ep + 0.2, r.tp, f"+TP {r.tp_dist:.0f}pt", color=GREEN,
                    fontsize=7.5, va="bottom", fontweight="bold")
            ax.text(ep + 0.2, r.sl, f"-SL {r.sl_dist:.0f}pt", color=RED,
                    fontsize=7.5, va="top", fontweight="bold")

    r0 = wg.iloc[0]
    e0 = xpos(r0.ts)
    ax.annotate(
        f"{r0.side.upper()} signal @ {r0.entry:.0f}  (ATR {r0.atr:.1f}pt)\n"
        f"risk engine sets:\n"
        f"  SL = {sl_mult:g} x ATR = {r0.sl_dist:.0f}pt -> {r0.sl:.0f}\n"
        f"  TP = {tp_mult:g} x ATR = {r0.tp_dist:.0f}pt -> {r0.tp:.0f}\n"
        f"  R:R = {r0.rr:.2f}   lot {r0.lot:g} -> risk ${r0.risk_usd:.0f}",
        xy=(e0, r0.entry), xytext=(24, 60), textcoords="offset points",
        fontsize=9, family="monospace",
        bbox=dict(boxstyle="round", fc="#fffbe6", ec=GREY),
        arrowprops=dict(arrowstyle="->", color="k"))

    legend = [
        Line2D([0], [0], color="#222", lw=1.3, label="XAUUSD 15m close"),
        Line2D([0], [0], color=GREEN, lw=1.5, ls="--", label=f"TP = {tp_mult:g} x ATR (reward)"),
        Line2D([0], [0], color=RED, lw=1.5, ls="--", label=f"SL = {sl_mult:g} x ATR (risk)"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY,
               markeredgecolor="k", ms=11, label="entry (^BUY  vSELL)"),
    ]
    ax.legend(handles=legend, loc="best", framealpha=0.92, fontsize=9)
    ticks = np.linspace(0, len(wb) - 1, 8, dtype=int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([wb.index[i].strftime("%b-%d %H:%M") for i in ticks],
                       rotation=15, fontsize=8)
    ax.set_title("1 - Risk-engine SL/TP brackets (the ATR-dynamic levels the live engine sets)\n"
                 f"red dashed = SL ({sl_mult:g}xATR)  ·  green dashed = TP ({tp_mult:g}xATR)  ·  "
                 "bands breathe with ATR per signal (weekend gaps removed)",
                 fontsize=12)
    ax.set_ylabel("Gold price ($)")
    ax.margins(x=0.01)
    fig.tight_layout()
    fig.savefig(FIGDIR / "kalman_risk_engine_brackets.png")
    plt.close(fig)

    # ======================================================================
    # FIG 2 -- the rule schematic + ATR-scaling table
    # ======================================================================
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(15.5, 7.8),
                                   gridspec_kw={"width_ratios": [1.0, 1.05]})
    sl_pts, tp_pts = sl_mult * atr_med, tp_mult * atr_med
    axL.fill_between([0, 1], 0, tp_pts, color=GREEN, alpha=0.16)
    axL.fill_between([0, 1], 0, -sl_pts, color=RED, alpha=0.16)
    axL.axhline(0, color="k", lw=2.0)
    axL.axhline(tp_pts, color=GREEN, lw=2.2, ls="--")
    axL.axhline(-sl_pts, color=RED, lw=2.2, ls="--")
    axL.annotate("", xy=(0.5, tp_pts), xytext=(0.5, 0),
                 arrowprops=dict(arrowstyle="<->", color=GREEN, lw=1.6))
    axL.annotate("", xy=(0.5, -sl_pts), xytext=(0.5, 0),
                 arrowprops=dict(arrowstyle="<->", color=RED, lw=1.6))
    axL.text(0.06, 0, "ENTRY", va="center", fontweight="bold", fontsize=11)
    axL.text(0.55, tp_pts / 2, f"reward = {tp_mult:g} x ATR\n= {tp_pts:.0f}pt",
             color=GREEN, va="center", fontweight="bold")
    axL.text(0.55, -sl_pts / 2, f"risk = {sl_mult:g} x ATR\n= {sl_pts:.0f}pt",
             color=RED, va="center", fontweight="bold")
    axL.text(0.06, tp_pts, "TAKE PROFIT", color=GREEN, va="bottom", fontweight="bold")
    axL.text(0.06, -sl_pts, "STOP LOSS", color=RED, va="top", fontweight="bold")
    axL.set_xlim(0, 1)
    axL.set_ylim(-sl_pts * 1.45, tp_pts * 1.35)
    axL.set_xticks([])
    axL.set_ylabel("price distance from entry (points)")
    axL.set_title(f"2 - What the risk engine 'needs' for a BUY  (median ATR {atr_med:.1f}pt)\n"
                  f"R:R = {tp_mult:g}/{sl_mult:g} = {tp_mult/sl_mult:.2f}   "
                  "(SELL is the mirror: SL above, TP below)", fontsize=11.5)
    axL.text(0.5, -sl_pts * 1.33,
             "SL = entry -/+ sl_atr_multiplier x ATR\n"
             "TP = entry +/- max(tp_atr_multiplier x ATR, SL_dist x kalman_min_tp_rr)\n"
             f"     = max({tp_mult:g} x ATR, {sl_mult:g} x ATR x {min_tp_rr:g})  ->  {tp_mult:g} x ATR wins\n"
             "then: RR-floor reject if R:R < tier floor; broker-min expand if too tight",
             ha="center", va="top", fontsize=8.4, family="monospace",
             bbox=dict(boxstyle="round", fc="#f3f6ff", ec=GREY))

    # right: scaling table
    axR.axis("off")
    atrs = sorted({6, 8, 11, 14, 18, 22, round(atr_med)})
    cell, rowcols = [], []
    for a in atrs:
        sd = sl_mult * a
        td = tp_mult * a
        lot, rk = implied_lot(sd, risk_usd, value_per_lot, min_lot, lot_step, max_lot)
        cap_ok = "ok" if (risk_usd and rk <= risk_usd + 0.01) else "OVER"
        cell.append([f"{a:g}", f"{sd:.0f}", f"{td:.0f}", f"{tp_mult/sl_mult:.2f}",
                     f"{lot:g}", f"${rk:.0f}", cap_ok])
        rowcols.append("#fff3cd" if a == round(atr_med) else "white")
    col_lbl = ["ATR\npts", f"SL\n{sl_mult:g}xATR", f"TP\n{tp_mult:g}xATR", "R:R",
               f"lot\n@${risk_usd:.0f}", "real\n$risk", f"<=${risk_usd:.0f}?"]
    tbl = axR.table(cellText=cell, colLabels=col_lbl, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1.0, 1.7)
    for (rr_i, cc), cellobj in tbl.get_celld().items():
        if rr_i == 0:
            cellobj.set_facecolor("#34495e")
            cellobj.set_text_props(color="white", fontweight="bold")
        else:
            cellobj.set_facecolor(rowcols[rr_i - 1])
            if cc == 6:  # cap ok/OVER column
                txt = cellobj.get_text().get_text()
                cellobj.set_text_props(color=RED if txt == "OVER" else GREEN,
                                       fontweight="bold")
    axR.set_title("How SL/TP (and real $risk) scale with ATR\n"
                  f"highlighted row = median ATR; '${risk_usd:.0f} cap' only honored when "
                  "3xATR is tight enough\nto keep the implied lot above the broker min "
                  f"({min_lot:g}) -- otherwise the floor forces more risk",
                  fontsize=10.5)
    fig.tight_layout()
    fig.savefig(FIGDIR / "kalman_risk_engine_rule.png")
    plt.close(fig)

    # ======================================================================
    # FIG 3 -- distributions across ALL 2026 signals
    # ======================================================================
    fig, axs = plt.subplots(2, 2, figsize=(15, 9.5))
    a00, a01, a10, a11 = axs.ravel()

    a00.hist(g.sl_dist, bins=30, color=RED, alpha=0.8, edgecolor="k", lw=0.4)
    a00.axvline(g.sl_dist.median(), color="k", ls="--", lw=1.5,
                label=f"median {g.sl_dist.median():.0f}pt")
    a00.set_title(f"SL distance = {sl_mult:g} x ATR  (n={len(g)} signals)")
    a00.set_xlabel("SL distance from entry (points)")
    a00.legend(fontsize=9)

    a01.hist(g.tp_dist, bins=30, color=GREEN, alpha=0.8, edgecolor="k", lw=0.4)
    a01.axvline(g.tp_dist.median(), color="k", ls="--", lw=1.5,
                label=f"median {g.tp_dist.median():.0f}pt")
    a01.set_title(f"TP distance = {tp_mult:g} x ATR")
    a01.set_xlabel("TP distance from entry (points)")
    a01.legend(fontsize=9)

    # SL & TP vs ATR -- shows the linear 3x / 4x relationship the engine enforces
    a10.scatter(g.atr, g.sl_dist, s=10, color=RED, alpha=0.5, label="SL dist")
    a10.scatter(g.atr, g.tp_dist, s=10, color=GREEN, alpha=0.5, label="TP dist")
    xa = np.linspace(g.atr.min(), g.atr.max(), 50)
    a10.plot(xa, sl_mult * xa, color=RED, lw=1.5)
    a10.plot(xa, tp_mult * xa, color=GREEN, lw=1.5)
    a10.set_title(f"SL/TP scale linearly with ATR  (R:R fixed at {g.rr.median():.2f})")
    a10.set_xlabel("ATR at signal (points)")
    a10.set_ylabel("stop distance (points)")
    a10.legend(fontsize=9)

    if risk_usd > 0:
        a11.hist(g.risk_usd, bins=30, color=BLUE, alpha=0.8, edgecolor="k", lw=0.4)
        a11.axvline(risk_usd, color=GREEN, ls="--", lw=2,
                    label=f"intended cap ${risk_usd:.0f}")
        a11.axvline(g.risk_usd.median(), color=RED, ls="--", lw=2,
                    label=f"median real ${g.risk_usd.median():.0f}")
        a11.set_title(f"Real $risk/trade after min-lot floor  "
                      f"(${risk_usd:.0f} cap breached on {cap_breach:.0f}% of signals)")
        a11.set_xlabel("$ risk per trade at floored lot")
        a11.legend(fontsize=9)
    else:
        a11.axis("off")

    fig.suptitle("3 - Risk-engine SL/TP across all 2026 Kalman signals", fontsize=13, y=1.0)
    fig.tight_layout()
    fig.savefig(FIGDIR / "kalman_risk_engine_dist.png")
    plt.close(fig)

    print("\nsaved 3 figures to", FIGDIR)
    for p in ["kalman_risk_engine_brackets", "kalman_risk_engine_rule",
              "kalman_risk_engine_dist"]:
        print("  ", FIGDIR / (p + ".png"))


if __name__ == "__main__":
    main()
