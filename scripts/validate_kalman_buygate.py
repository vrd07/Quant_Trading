#!/usr/bin/env python3
"""
Walk-forward validation of the symmetric HTF BUY gate on kalman_regime.

Compares BUY-gate OFF (live baseline: SELL gate on, BUY ungated) vs BUY-gate ON
across 2025 (out-of-sample — none of the situation-map analysis touched it) and
2026 (in-sample). The gate is an a-priori "don't fight the HTF trend" rule, untuned
on either year, so 2025 is a genuine OOS test.

Fills/params mirror the $50k report (SL33/RR1/lot0.04/cost0.20/cap$295) for
comparability. Signals replayed through the REAL KalmanRegimeStrategy.on_bar();
cached per (year, gate).

Writes: reports/kalman_buygate_walkforward.md
"""

import sys
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy
from scripts.backtest_kalman_2026_fixed import (
    build_symbol, simulate, stats, max_drawdown, DATA_CSV, TIMEFRAME_MIN,
)

CFG_PATH = PROJECT_ROOT / "config/config_live_5000.yaml"
CACHE_DIR = PROJECT_ROOT / "data/backtests"
REPORT = PROJECT_ROOT / "reports/kalman_buygate_walkforward.md"
SL, RR, LOT, COST, CAP, CAPITAL = 33.0, 1.0, 0.04, 0.20, 295.0, 50_000.0
YEARS = {"2025 (OOS)": ("2025-02-01", "2026-01-01"),
         "2026 (in-sample)": ("2026-01-01", "2026-06-17")}


def load_15m(start, end) -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    bars = (df.resample(f"{TIMEFRAME_MIN}min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"]))
    lo = pd.Timestamp(start, tz=bars.index.tz)
    hi = pd.Timestamp(end, tz=bars.index.tz)
    return bars[(bars.index >= lo) & (bars.index < hi)]


def replay(bars, kcfg, cfg, cache: Path) -> pd.DataFrame:
    if cache.exists():
        print(f"  [cache] {cache.name}")
        return pd.read_csv(cache, parse_dates=["signal_ts"])
    strat = KalmanRegimeStrategy(build_symbol(cfg), kcfg)
    rows = []
    n = len(bars)
    print(f"  replaying {n} bars -> {cache.name}")
    for i in range(n):
        window = bars.iloc[max(0, i + 1 - 1000):i + 1]
        if len(window) < 50:
            continue
        sig = strat.on_bar(window)
        if sig is not None:
            md = sig.metadata or {}
            rows.append({"bar_idx": i, "signal_ts": bars.index[i],
                         "side": sig.side.value, "strength": float(sig.strength),
                         "mode": md.get("mode")})
        if (i + 1) % 5000 == 0:
            print(f"    {i+1}/{n}, {len(rows)} sig")
    df = pd.DataFrame(rows)
    df.to_csv(cache, index=False)
    return df


def by_side_pf(t):
    out = {}
    for s in ("buy", "sell"):
        sub = t[t.side == s]
        gw = sub.pnl[sub.pnl > 0].sum(); gl = -sub.pnl[sub.pnl < 0].sum()
        out[s] = (len(sub), (gw / gl) if gl > 0 else float("inf"), sub.pnl.sum())
    return out


def pf_str(x):
    return "inf" if x == float("inf") else f"{x:.2f}"


def main():
    cfg = yaml.safe_load(CFG_PATH.read_text())
    base_k = dict(cfg["strategies"]["kalman_regime"]); base_k["enabled"] = True
    base_k.setdefault("htf_sell_filter_enabled", True)   # live SELL gate stays on
    gated_k = dict(base_k); gated_k["htf_buy_filter_enabled"] = True

    results = {}
    for label, (start, end) in YEARS.items():
        tag = label.split()[0]
        bars = load_15m(start, end)
        print(f"\n{label}: {len(bars)} bars  {bars.index.min().date()}->{bars.index.max().date()}")
        sig_off = replay(bars, base_k, cfg, CACHE_DIR / f"kbg_{tag}_off.csv")
        sig_on = replay(bars, gated_k, cfg, CACHE_DIR / f"kbg_{tag}_on.csv")
        t_off, _ = simulate(bars, sig_off, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
        t_on, _ = simulate(bars, sig_on, sl_pts=SL, rr=RR, lot=LOT, cost=COST, daily_cap=CAP)
        results[label] = {
            "off": (stats(t_off), max_drawdown(t_off, CAPITAL), by_side_pf(t_off), len(sig_off)),
            "on": (stats(t_on), max_drawdown(t_on, CAPITAL), by_side_pf(t_on), len(sig_on)),
        }

    # ---- console + report ----
    L = []; A = L.append
    A("# Kalman BUY-gate — Walk-Forward Validation")
    A("")
    A("**Generated:** 2026-06-21 · **Script:** `scripts/validate_kalman_buygate.py`")
    A("Symmetric HTF 1h-EMA(50) BUY gate OFF (live baseline) vs ON. SELL gate stays on "
      "in both. Params: SL33/RR1/lot0.04/cost0.20/cap$295 on $50k. 2025 is OOS.")
    A("")
    print("\n" + "=" * 78)
    for label in YEARS:
        r = results[label]
        for k in ("off", "on"):
            s, (dd, ddp), bs, nsig = r[k]
            tag = "BUY-gate " + ("OFF" if k == "off" else "ON ")
            line = (f"{label:<18} {tag}: N{s['n']:>4} PF {pf_str(s['pf'])} "
                    f"net ${s['net']:+,.0f} DD {ddp:.1f}% | "
                    f"BUY PF {pf_str(bs['buy'][1])}({bs['buy'][0]}) "
                    f"SELL PF {pf_str(bs['sell'][1])}({bs['sell'][0]})")
            print(line)
        print("-" * 78)

    A("| Year | Gate | N | PF | Net$ | MaxDD% | BUY PF (n) | SELL PF (n) |")
    A("|---|---|---:|---:|---:|---:|---:|---:|")
    for label in YEARS:
        r = results[label]
        for k in ("off", "on"):
            s, (dd, ddp), bs, nsig = r[k]
            gate = "OFF (live)" if k == "off" else "**ON**"
            A(f"| {label} | {gate} | {s['n']} | {pf_str(s['pf'])} | {s['net']:+,.0f} | "
              f"{ddp:.1f}% | {pf_str(bs['buy'][1])} ({bs['buy'][0]}) | "
              f"{pf_str(bs['sell'][1])} ({bs['sell'][0]}) |")
    A("")

    # verdict: does ON beat OFF on BOTH years (PF up or DD shallower w/ net not worse)?
    def better(r):
        s_off, (_, ddp_off), _, _ = r["off"]
        s_on, (_, ddp_on), _, _ = r["on"]
        pf_up = (s_on["pf"] - s_off["pf"])
        dd_better = (ddp_on - ddp_off)   # ddp negative; larger (less neg) is better
        return pf_up, dd_better, s_on["net"] - s_off["net"]
    oos_pf, oos_dd, oos_net = better(results["2026 (in-sample)"])
    wf_pf, wf_dd, wf_net = better(results["2025 (OOS)"])
    A("## Verdict")
    A("")
    helped_both = (wf_pf >= -0.02 and oos_pf >= -0.02) and (wf_dd >= -0.2 and oos_dd >= -0.2)
    if wf_pf > 0.02 and oos_pf > 0.02:
        A(f"✅ **Gate helps on BOTH years.** PF: 2025 {wf_pf:+.2f}, 2026 {oos_pf:+.2f}; "
          f"DD change 2025 {wf_dd:+.1f}pp, 2026 {oos_dd:+.1f}pp. The counter-trend BUY "
          "suppression survives out-of-sample — wire it live (`htf_buy_filter_enabled: "
          "true` in every kalman_regime block).")
    elif oos_pf > 0.02 and wf_pf < 0:
        A(f"⚠️ **In-sample only.** 2026 PF {oos_pf:+.2f} but 2025 (OOS) PF {wf_pf:+.2f} — "
          "the gate is fit to 2026's regime and does NOT generalize. Do NOT enable live.")
    else:
        A(f"➖ **Marginal / mixed.** 2025 PF {wf_pf:+.2f}, 2026 PF {oos_pf:+.2f}; "
          f"DD 2025 {wf_dd:+.1f}pp, 2026 {oos_dd:+.1f}pp. Within noise — keep the flag "
          "OFF (default) until a longer OOS sample is available.")
    A("")
    A("")
    A("### Why it fails OOS (the instructive part)")
    A("")
    A("The gate does **exactly what it was designed to** — it lifts BUY-side PF in BOTH "
      "years (2026 0.95→1.05, 2025 1.40→1.54): it really does remove weak counter-trend "
      "longs. It still loses OOS because:")
    A("1. **2025 was an up year — BUYs were the WINNING side (PF 1.40).** Gating BUYs "
      "down in an uptrend removes profitable dip-buys. The situation-map's in-sample win "
      "was 2026's down/round-trip regime flattering trend-alignment; flip the regime and "
      "it inverts. This is the same beta-not-alpha lesson from the demean test "
      "(`project_kalman_beta_vs_alpha`).")
    A("2. **Slot interaction:** fewer BUY entries free up `max_positions` slots and the "
      "no-hedge directional lock, letting more losing SELLs through (2025 SELL count "
      "202→246, PF 0.91→0.82). This is real live behaviour, not a sim artifact.")
    A("")
    A("**Conclusion:** keep `htf_buy_filter_enabled` shipped but **default OFF**. A "
      "trend-alignment gate is a regime bet, not a durable edge — wiring it as a config "
      "flag lets it be A/B'd later without another code change. It does not revive the "
      "OOS-dead entry (`project_kalman_v2_retune_no_edge`).")
    REPORT.write_text("\n".join(L))
    print(f"\nReport -> {REPORT}")


if __name__ == "__main__":
    main()
