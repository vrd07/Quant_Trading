#!/usr/bin/env python3
"""
LIVE risk-engine SL/TP overlay for Kalman (and any strategy).

Unlike viz_kalman_risk_engine.py (offline, draws the *backtest* 3xATR/4xATR
geometry), this reads the LIVE bot's monitor-state file and draws the SL/TP the
risk engine ACTUALLY placed -- the same numbers shown in live_monitor's sl/tp
columns -- as shaded brackets, refreshing as new signals fire.

Source of truth: data/metrics/live_monitor_state_<config_stem>.json, emitted
~every second by src/monitoring/live_monitor_emitter.py while the bot runs.
We read, never write -- 100% passive, safe to run alongside the live bot.

  * signals[]   -> recent fired/rejected signals (ts, side, price=entry, sl, tp, status)
  * positions[] -> open trades (entry, current, sl, tp, side, pnl, opened_at)
  * symbols[]   -> live bid/ask (current price line) + atr_pct (optional reference)

Each item is drawn as: entry marker (^BUY vSELL), green TP band (entry->tp),
red SL band (entry->sl), annotated with the real SL/TP distances + R:R. A live
current-price line is drawn across the width. Optional faint dotted "backtest
geometry" reference brackets (3xATR / 4xATR from atr_pct) show how the placed
stops compare to what the offline chart assumes.

Usage:
  venv/bin/python scripts/live_kalman_risk_overlay.py                 # one snapshot
  venv/bin/python scripts/live_kalman_risk_overlay.py --watch 5       # refresh every 5s
  venv/bin/python scripts/live_kalman_risk_overlay.py --strategy all --symbol XAUUSD
Then open reports/figs/live_kalman_risk_overlay.png (macOS Preview auto-reloads).
"""
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D

ROOT = Path(__file__).parent.parent
GREEN, RED, BLUE, GREY, AMBER, INK = "#1a9850", "#d73027", "#2c7fb8", "#888888", "#b8860b", "#222222"
plt.rcParams.update({"figure.dpi": 110, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.axisbelow": True, "text.parse_math": False})

# Backtest geometry assumed by viz_kalman_risk_engine.py, for the reference brackets.
REF_SL_MULT, REF_TP_MULT = 3.0, 4.0


def active_config_stem() -> str:
    marker = (ROOT / "config/ACTIVE_CONFIG").read_text().strip().splitlines()[0].strip()
    return Path(marker).stem


def state_path(config_stem: str) -> Path:
    return ROOT / "data/metrics" / f"live_monitor_state_{config_stem}.json"


def parse_ts(s):
    if not s:
        return None
    try:
        t = pd.Timestamp(s)
        return t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
    except Exception:
        return None


def find_symbol(state, symbol):
    for s in state.get("symbols", []) or []:
        if str(s.get("ticker", "")).upper().startswith(symbol.upper()):
            return s
    return None


def collect(state, symbol, strategy):
    """Return (signals_df, positions_df) filtered to symbol/strategy."""
    def keep(rec_sym, rec_strat):
        sym_ok = symbol.lower() == "all" or str(rec_sym).upper().startswith(symbol.upper())
        strat_ok = strategy.lower() == "all" or str(rec_strat) == strategy
        return sym_ok and strat_ok

    sig_rows = []
    for s in state.get("signals", []) or []:
        if not keep(s.get("symbol"), s.get("strategy")):
            continue
        e, sl, tp = s.get("price"), s.get("sl"), s.get("tp")
        if not (e and sl and tp):
            continue
        sig_rows.append({
            "ts": parse_ts(s.get("ts")), "side": str(s.get("side", "")).upper(),
            "strategy": s.get("strategy", ""), "entry": float(e),
            "sl": float(sl), "tp": float(tp),
            "status": str(s.get("status", "")), "reason": str(s.get("reason", "")),
            "confidence": s.get("confidence"),
        })
    pos_rows = []
    for p in state.get("positions", []) or []:
        if not keep(p.get("symbol"), p.get("strategy")):
            continue
        e, sl, tp = p.get("entry"), p.get("sl"), p.get("tp")
        if not (e and sl and tp):
            continue
        pos_rows.append({
            "opened_at": parse_ts(p.get("opened_at")), "side": str(p.get("side", "")).upper(),
            "strategy": p.get("strategy", ""), "entry": float(e),
            "current": float(p.get("current", 0) or 0), "sl": float(sl), "tp": float(tp),
            "pnl": float(p.get("pnl", 0) or 0), "duration": p.get("duration", ""),
        })
    sdf = pd.DataFrame(sig_rows).dropna(subset=["ts"]).sort_values("ts") if sig_rows else pd.DataFrame()
    pdf = pd.DataFrame(pos_rows) if pos_rows else pd.DataFrame()
    return sdf, pdf


def geom(entry, sl, tp):
    sld, tpd = abs(entry - sl), abs(entry - tp)
    return sld, tpd, (tpd / sld if sld else float("nan"))


def render(state, symbol, strategy, max_signals, reference, out_path, src_name):
    now = datetime.now(timezone.utc)
    updated = parse_ts(state.get("updated_at"))
    age = (now - updated.to_pydatetime()).total_seconds() if updated is not None else None
    stale = age is None or age > 120

    sym = find_symbol(state, symbol) if symbol.lower() != "all" else None
    price = None
    atr_pts = None
    if sym:
        bid, ask = sym.get("bid") or 0, sym.get("ask") or 0
        if bid and ask:
            price = (float(bid) + float(ask)) / 2.0
        ap = sym.get("atr_pct")
        if ap and price:
            atr_pts = price * float(ap) / 100.0  # atr_pct is a percent

    sdf, pdf = collect(state, symbol, strategy)
    if len(sdf) > max_signals:
        sdf = sdf.tail(max_signals)

    fig, ax = plt.subplots(figsize=(15, 8))

    # ---- nothing to show yet ----------------------------------------------
    if sdf.empty and pdf.empty:
        msg = ("Waiting for the bot to emit a state file...\n"
               f"({src_name})\nRun scripts/start_live.sh, then signals appear here as they fire."
               if state.get("__missing__") else
               f"No recent {strategy} {symbol} signals or open positions in the live state.\n"
               f"(state updated {('%.0fs ago' % age) if age is not None else 'unknown'})")
        ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=12,
                bbox=dict(boxstyle="round", fc="#fff8e1", ec=GREY))
        if price:
            ax.axhline(price, color=INK, lw=1.4, ls="-")
            ax.text(0.99, price, f" live {symbol} {price:.2f}", color=INK, ha="right",
                    va="bottom", transform=ax.get_yaxis_transform(), fontsize=9)
        ax.set_xticks([])
        ax.set_title(f"LIVE risk-engine SL/TP overlay — {strategy} / {symbol}", fontsize=12)
        fig.tight_layout()
        fig.savefig(out_path)
        plt.close(fig)
        return

    # ---- time axis span ----------------------------------------------------
    t_all = list(sdf["ts"]) if not sdf.empty else []
    if not pdf.empty and pdf["opened_at"].notna().any():
        t_all += list(pdf["opened_at"].dropna())
    t_lo = min(t_all)
    t_hi = max(max(t_all), pd.Timestamp(now))
    span = max((t_hi - t_lo).total_seconds(), 1800)
    fwd = pd.Timedelta(seconds=min(span * 0.10, 20 * 60))  # bracket forward length

    def fwd_end(i, ts_series):
        nxt = ts_series[ts_series > ts_series.iloc[i]].min() if i < len(ts_series) else pd.NaT
        cand = ts_series.iloc[i] + fwd
        return min(cand, nxt) if pd.notna(nxt) else cand

    ys = []  # collect sl/tp/entry/price to set y-limits

    # ---- live price line ---------------------------------------------------
    if price:
        ax.axhline(price, color=INK, lw=1.5, zorder=5)
        ax.text(mdates.date2num(t_hi), price, f"  live {price:.2f}", color=INK,
                va="center", fontsize=9, fontweight="bold", zorder=6)
        ys.append(price)

    # ---- optional backtest-geometry reference brackets ---------------------
    ref_note = ""
    if reference and atr_pts:
        ref_sl, ref_tp = REF_SL_MULT * atr_pts, REF_TP_MULT * atr_pts
        ref_note = (f"   ·   backtest assumes {REF_SL_MULT:g}xATR={ref_sl:.0f}/"
                    f"{REF_TP_MULT:g}xATR={ref_tp:.0f} (RR {REF_TP_MULT/REF_SL_MULT:.2f})")

    # ---- signals -----------------------------------------------------------
    sig_ts = sdf["ts"].reset_index(drop=True) if not sdf.empty else pd.Series([], dtype="datetime64[ns, UTC]")
    for i, (_, r) in enumerate(sdf.reset_index(drop=True).iterrows()):
        x0 = mdates.date2num(r.ts)
        x1 = mdates.date2num(fwd_end(i, sig_ts))
        rejected = "REJECT" in r.status.upper() or "REJECT" in r.reason.upper()
        alpha = 0.07 if rejected else 0.16
        hatch = "//" if rejected else None
        ax.add_patch(Rectangle((x0, min(r.entry, r.tp)), x1 - x0, abs(r.entry - r.tp),
                               facecolor=GREEN, alpha=alpha, edgecolor="none", hatch=hatch, zorder=1))
        ax.add_patch(Rectangle((x0, min(r.entry, r.sl)), x1 - x0, abs(r.entry - r.sl),
                               facecolor=RED, alpha=alpha, edgecolor="none", hatch=hatch, zorder=1))
        ls = ":" if rejected else "--"
        ax.hlines(r.tp, x0, x1, color=GREEN, lw=1.4, ls=ls, zorder=3)
        ax.hlines(r.sl, x0, x1, color=RED, lw=1.4, ls=ls, zorder=3)
        col = BLUE if r.side == "BUY" else AMBER
        mk = "^" if r.side == "BUY" else "v"
        ax.scatter(x0, r.entry, marker=mk, s=140, color=col,
                   edgecolor="k", lw=0.7, zorder=6, alpha=0.55 if rejected else 1.0)
        sld, tpd, rr = geom(r.entry, r.sl, r.tp)
        tag = "rej" if rejected else r.status.lower()[:4]
        ax.text(x0, r.tp, f" {r.side} RR{rr:.2f} [{tag}]", color=col, fontsize=7.3,
                va="bottom", fontweight="bold", rotation=0)
        ys += [r.sl, r.tp, r.entry]

    # ---- open positions (bold, span to now) --------------------------------
    for _, r in pdf.iterrows():
        x0 = mdates.date2num(r.opened_at) if pd.notna(r.opened_at) else mdates.date2num(t_lo)
        x1 = mdates.date2num(now)
        ax.add_patch(Rectangle((x0, min(r.entry, r.tp)), x1 - x0, abs(r.entry - r.tp),
                               facecolor=GREEN, alpha=0.20, edgecolor=GREEN, lw=1.2, zorder=2))
        ax.add_patch(Rectangle((x0, min(r.entry, r.sl)), x1 - x0, abs(r.entry - r.sl),
                               facecolor=RED, alpha=0.20, edgecolor=RED, lw=1.2, zorder=2))
        ax.hlines(r.entry, x0, x1, color=INK, lw=1.6, zorder=4)
        col = BLUE if r.side in ("BUY", "LONG") else AMBER
        mk = "^" if r.side in ("BUY", "LONG") else "v"
        ax.scatter(x0, r.entry, marker=mk, s=220, color=col, edgecolor="k", lw=1.0, zorder=7)
        if r.current:
            ax.scatter(x1, r.current, marker="o", s=80, color=(GREEN if r.pnl >= 0 else RED),
                       edgecolor="k", lw=0.8, zorder=7)
        sld, tpd, rr = geom(r.entry, r.sl, r.tp)
        ax.text(x0, r.tp, f" OPEN {r.side} RR{rr:.2f}  pnl ${r.pnl:+.0f}", color=col,
                fontsize=8.5, va="bottom", fontweight="bold")
        ys += [r.sl, r.tp, r.entry, r.current or r.entry]

    # ---- axes / titles -----------------------------------------------------
    if ys:
        lo, hi = min(ys), max(ys)
        pad = (hi - lo) * 0.12 or 1.0
        ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(mdates.date2num(t_lo) - 0.01 * span / 86400,
                mdates.date2num(t_hi) + (fwd.total_seconds() / 86400) + 0.01 * span / 86400)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    ax.set_ylabel(f"{symbol} price")

    # live geometry readout across shown signals
    geo = ""
    _parts = [d[["entry", "sl", "tp"]] for d in (sdf, pdf) if not d.empty]
    base = pd.concat(_parts, ignore_index=True) if _parts else pd.DataFrame()
    if not base.empty:
        g = base.apply(lambda x: pd.Series(geom(x.entry, x.sl, x.tp), index=["sld", "tpd", "rr"]), axis=1)
        geo = (f"engine SL {g.sld.median():.1f} / TP {g.tpd.median():.1f} pts, "
               f"RR {g.rr.median():.2f}  (n={len(base)})")

    freshness = (f"updated {age:.0f}s ago" if age is not None else "no timestamp")
    banner = "  ⚠ STALE — is the bot running?" if stale else ""
    ax.set_title(
        f"LIVE risk-engine SL/TP — {strategy} / {symbol}  ·  {freshness}{banner}\n"
        f"red = placed SL · green = placed TP · ^BUY vSELL · hatched/faded = rejected{ref_note}",
        fontsize=11.5, color=(RED if stale else INK))
    sub = []
    if price:
        sub.append(f"live {price:.2f}")
    if sym:
        sub.append(f"{sym.get('regime','?')}/{sym.get('direction','?')}")
        if sym.get("atr_pct"):
            sub.append(f"ATR {sym['atr_pct']:.2f}% (~{atr_pts:.1f}pt)" if atr_pts else "")
    if geo:
        sub.append(geo)
    ax.text(0.005, 0.995, "   ·   ".join([s for s in sub if s]), transform=ax.transAxes,
            va="top", ha="left", fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", fc="#f3f6ff", ec=GREY))

    handles = [
        Line2D([0], [0], color=GREEN, lw=1.4, ls="--", label="placed TP"),
        Line2D([0], [0], color=RED, lw=1.4, ls="--", label="placed SL"),
        Line2D([0], [0], color=INK, lw=1.5, label="live price"),
        Line2D([0], [0], marker="^", color="w", markerfacecolor=GREY, markeredgecolor="k",
               ms=11, label="entry (^BUY vSELL)"),
    ]
    ax.legend(handles=handles, loc="lower left", framealpha=0.92, fontsize=8.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def load_state(path: Path):
    if not path.exists():
        return {"__missing__": True}
    try:
        return json.loads(path.read_text())
    except Exception as exc:
        return {"__error__": str(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="config yaml or stem (default: config/ACTIVE_CONFIG)")
    ap.add_argument("--symbol", default="XAUUSD", help="symbol filter, or 'all'")
    ap.add_argument("--strategy", default="kalman_regime", help="strategy filter, or 'all'")
    ap.add_argument("--max-signals", type=int, default=12)
    ap.add_argument("--watch", type=float, default=0.0, help="re-render every N seconds (0 = one snapshot)")
    ap.add_argument("--no-reference", action="store_true", help="hide the backtest-geometry reference note")
    ap.add_argument("--out", default=str(ROOT / "reports/figs/live_kalman_risk_overlay.png"))
    args = ap.parse_args()

    stem = Path(args.config).stem if args.config else active_config_stem()
    sp = state_path(stem)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def once():
        state = load_state(sp)
        render(state, args.symbol, args.strategy, args.max_signals,
               not args.no_reference, out, sp.name)
        ts = datetime.now().strftime("%H:%M:%S")
        flag = "MISSING" if state.get("__missing__") else ("ERROR" if state.get("__error__") else "ok")
        print(f"[{ts}] {flag}  {sp.name} -> {out}")

    print(f"state file: {sp}")
    once()
    if args.watch > 0:
        print(f"watching every {args.watch:g}s — Ctrl-C to stop "
              f"(open {out.name} in Preview; it auto-reloads)")
        try:
            while True:
                time.sleep(args.watch)
                once()
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
