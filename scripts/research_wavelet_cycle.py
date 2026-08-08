#!/usr/bin/env python3
"""Wavelet-Fourier hybrid cycle strategy -- walk-forward research (spec §8.2).

Expanding-window protocol, strict fills, cost-charged both sides:

    train    2022-01-01 -> 2024-01-01   parameter discovery
    validate 2024-01-01 -> 2024-07-01   keep the top 3 robust cells
    test     2024-07-01 -> 2025-01-01   final OOS, parameters frozen

A surrogate control runs alongside every cell. If the shuffled-returns arm scores
anywhere near the real arm, the cell is fitted noise regardless of how good its
PF looks -- which is precisely how the last Fourier pass on this repo produced a
"hot cell" that evaporated (reports/fourier_research.md).

Writes: reports/wavelet_cycle_research.md
"""

import argparse
import logging
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

logging.disable(logging.INFO)
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.backtest_kalman_2026_fixed as harness                 # noqa: E402
from src.core.types import Symbol                                    # noqa: E402
from src.cycles.validation import surrogate_series                   # noqa: E402
from src.strategies.wavelet_cycle_strategy import WaveletCycleStrategy  # noqa: E402
from scripts.backtest_kalman_2026_fixed import max_drawdown, simulate, stats  # noqa: E402

REPORT = PROJECT_ROOT / "reports/wavelet_cycle_research.md"
LOT, COST, CAP, CAPITAL = 0.04, 0.20, 295.0, 50_000.0

ASSETS = {
    # proxy_invert: EUR is ~57.6% of the DXY basket, so -EURUSD is the dollar
    # proxy. The repo has no DXY price series (only a sentiment scalar in gss.py).
    "XAUUSD": dict(csv="XAUUSD_5m_real.csv", tf="15min", preset="gold", tf_min=15,
                   proxy_csv="EURUSD_5m_real.csv", proxy_invert=True,
                   value_per_lot=100.0),
    # BTC value_per_lot is a PLACEHOLDER (1 lot == 1 BTC). VERIFY against the
    # broker's MT5 symbol spec before quoting any BTC dollar figure -- the shared
    # harness hardcodes gold's 100.0, which would inflate BTC PnL 100x.
    "BTCUSD": dict(csv="BTCUSD_5m_real.csv", tf="60min", preset="btc", tf_min=60,
                   proxy_csv="NAS100_5m_real.csv", proxy_invert=False,
                   value_per_lot=1.0),
}
SPLITS = {
    "train":    ("2022-01-01", "2024-01-01"),
    "validate": ("2024-01-01", "2024-07-01"),
    "test":     ("2024-07-01", "2025-01-01"),
}
GRID = dict(
    entry_dev_atr=[0.5, 1.0],
    rr=[1.5, 2.0, 3.0],
    # NOT the plan's [2.0, 3.0]. Both of those fail the §8.1 hallucination gate
    # -- at 2.0 pink noise reads "tradeable" on 61% of bars against a 30% ceiling
    # (see the table in cycle_tracker's docstring). Researching a setting the
    # gate rejects would just be measuring how profitable hallucination looks.
    min_prominence=[4.0, 5.0],
)


def load(asset: str) -> pd.DataFrame:
    cfg = ASSETS[asset]
    df = pd.read_csv(PROJECT_ROOT / "data/historical" / cfg["csv"],
                     parse_dates=["timestamp"], index_col="timestamp")
    return (df.resample(cfg["tf"], label="left", closed="left")
              .agg({"open": "first", "high": "max", "low": "min",
                    "close": "last", "volume": "sum"})
              .dropna(subset=["open", "high", "low", "close"]))


def load_proxy(asset: str, index: pd.DatetimeIndex) -> "pd.Series | None":
    """Macro proxy aligned onto the primary bar index. Missing file => None,
    which the filter treats as pass-through rather than a silent veto."""
    cfg = ASSETS[asset]
    path = PROJECT_ROOT / "data/historical" / cfg["proxy_csv"]
    if not path.exists():
        print(f"  [warn] proxy {cfg['proxy_csv']} missing -- correlation filter off")
        return None
    df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
    s = df["close"].resample(cfg["tf"], label="left", closed="left").last()
    s = s.reindex(index).ffill()
    return -s if cfg["proxy_invert"] else s


def slice_split(bars: pd.DataFrame, split: str) -> pd.DataFrame:
    lo, hi = SPLITS[split]
    lo = pd.Timestamp(lo, tz=bars.index.tz)
    hi = pd.Timestamp(hi, tz=bars.index.tz)
    return bars[(bars.index >= lo) & (bars.index < hi)]


def replay(bars: pd.DataFrame, asset: str, params: dict) -> pd.DataFrame:
    """Bar-by-bar replay over a sliding window -- the same call shape the live
    loop uses, so a signal here is a signal there."""
    cfg = ASSETS[asset]
    strat = WaveletCycleStrategy(
        Symbol(ticker=asset),
        {"enabled": True, "timeframe": "15m" if cfg["tf_min"] == 15 else "1h",
         "asset_preset": cfg["preset"], "allowed_symbols": [asset],
         "entry_dev_atr": params["entry_dev_atr"], "rr": params["rr"],
         "min_prominence": params["min_prominence"]},
    )
    strat.set_proxy_series(load_proxy(asset, bars.index))
    lookback = 400 if cfg["preset"] == "gold" else 600
    rows = []
    for i in range(lookback, len(bars)):
        sig = strat.on_bar(bars.iloc[i - lookback:i + 1])
        if sig is None:
            continue
        md = sig.metadata or {}
        rows.append({
            "bar_idx": i, "signal_ts": bars.index[i], "side": sig.side.value,
            "entry": float(sig.entry_price), "stop": float(sig.stop_loss),
            "target": float(sig.take_profit), "regime": md["regime"],
            "cycle_period": md["cycle_period"],
            "time_stop_bars": int(np.ceil(1.5 * md["cycle_period"])),
        })
    return pd.DataFrame(rows)


_EMPTY_CELL = dict(trades=0, pf=0.0, net=0.0, sharpe=0.0, maxdd=0.0, win=0.0)


def daily_sharpe(trades: pd.DataFrame) -> float:
    """Annualised Sharpe on daily realised PnL. `stats()` does not provide one,
    and gate G4 in backtest.md is stated in exactly these terms."""
    if trades.empty:
        return 0.0
    daily = trades.set_index("exit_ts")["pnl"].resample("1D").sum()
    daily = daily[daily != 0.0]
    if len(daily) < 5 or daily.std() == 0:
        return 0.0
    return float(daily.mean() / daily.std() * np.sqrt(252))


def run_cell(bars: pd.DataFrame, asset: str, params: dict) -> dict:
    sig_df = replay(bars, asset, params)
    if sig_df.empty:
        return dict(_EMPTY_CELL)

    # The shared harness hardcodes gold's contract size as a module constant.
    # Set it per asset or every BTC dollar figure is 100x too large.
    harness.VALUE_PER_LOT = ASSETS[asset]["value_per_lot"]

    # KNOWN APPROXIMATION: `simulate` applies ONE sl_pts/rr to every trade, so the
    # per-trade ATR-scaled stop of §7.2 is collapsed to its median. That understates
    # the strategy in high-vol stretches and overstates it in quiet ones. It is
    # good enough to rank grid cells against each other and against the surrogate
    # control, which is all this script claims to do; a per-trade-stop simulator
    # is required before any figure here is treated as an expected live result.
    sl_pts = float(np.median(np.abs(sig_df["entry"] - sig_df["stop"])))
    rr = float(np.median(np.abs(sig_df["target"] - sig_df["entry"]) /
                         np.maximum(np.abs(sig_df["entry"] - sig_df["stop"]), 1e-9)))
    trades, _ = simulate(bars, sig_df, sl_pts=sl_pts, rr=rr, lot=LOT, cost=COST,
                         be_enabled=False, daily_cap=CAP)
    if trades.empty:
        return dict(_EMPTY_CELL)
    st = stats(trades)                       # keys: n, wr, pf, net, ... (no sharpe)
    _, dd_pct = max_drawdown(trades, CAPITAL)  # returns ($dd, %dd); %dd is negative
    return dict(trades=int(st["n"]), pf=float(st["pf"]), net=float(st["net"]),
                sharpe=daily_sharpe(trades), maxdd=abs(float(dd_pct)) / 100.0,
                win=float(st["wr"]) / 100.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", nargs="+", default=["XAUUSD", "BTCUSD"])
    ap.add_argument("--surrogates", type=int, default=5)
    args = ap.parse_args()

    lines = ["# Wavelet-Fourier Hybrid Cycle -- Walk-Forward Research", "",
             f"Generated {pd.Timestamp.now():%Y-%m-%d %H:%M}. Strict fills, "
             f"cost {COST}/side, lot {LOT}, cap ${CAP:.0f}, capital ${CAPITAL:,.0f}.",
             "", "Splits: train 2022-01→2024-01 | validate 2024-01→2024-07 | "
             "test 2024-07→2025-01 (frozen).", ""]

    for asset in args.assets:
        bars = load(asset)
        lines += [f"## {asset}", "",
                  "| params | split | trades | PF | net $ | Sharpe | maxDD | win% |",
                  "|---|---|---:|---:|---:|---:|---:|---:|"]
        scored = []
        for combo in product(*GRID.values()):
            params = dict(zip(GRID.keys(), combo))
            label = ",".join(f"{k}={v}" for k, v in params.items())
            res = {s: run_cell(slice_split(bars, s), asset, params) for s in ("train", "validate")}
            for split, r in res.items():
                lines.append(f"| {label} | {split} | {r['trades']} | {r['pf']:.2f} | "
                             f"{r['net']:.0f} | {r['sharpe']:.2f} | {r['maxdd']:.1%} | "
                             f"{r['win']:.0%} |")
            print(f"  {asset} {label}: train PF {res['train']['pf']:.2f} "
                  f"({res['train']['trades']} tr) | validate PF {res['validate']['pf']:.2f} "
                  f"({res['validate']['trades']} tr)")
            # Robust = positive on BOTH discovery slices, not best-of on either.
            if res["train"]["pf"] > 1.0 and res["validate"]["pf"] > 1.0:
                scored.append((min(res["train"]["pf"], res["validate"]["pf"]), params, label))

        scored.sort(reverse=True, key=lambda t: t[0])
        top3 = scored[:3]
        lines += ["", "### Frozen OOS test (parameters untouched)", ""]
        if not top3:
            lines += ["No cell was positive on both train and validate. "
                      "**Nothing is carried to the test slice.**", ""]
            continue

        lines += ["| params | trades | PF | net $ | Sharpe | maxDD | surrogate PF (mean) | verdict |",
                  "|---|---:|---:|---:|---:|---:|---:|---|"]
        test_bars = slice_split(bars, "test")
        for _, params, label in top3:
            real = run_cell(test_bars, asset, params)
            surr_pfs = []
            for seed in range(args.surrogates):
                shuffled = test_bars.copy()
                path = surrogate_series(test_bars["close"], seed)
                scale = path / test_bars["close"]
                for col in ("open", "high", "low", "close"):
                    shuffled[col] = test_bars[col] * scale
                surr_pfs.append(run_cell(shuffled, asset, params)["pf"])
            surr = float(np.mean(surr_pfs)) if surr_pfs else float("nan")
            # A cell only counts if it beats its own shuffled control by a margin.
            verdict = ("PASS" if real["pf"] >= 1.4 and real["trades"] >= 30
                       and real["pf"] > surr + 0.3 else "FAIL")
            lines.append(f"| {label} | {real['trades']} | {real['pf']:.2f} | "
                         f"{real['net']:.0f} | {real['sharpe']:.2f} | "
                         f"{real['maxdd']:.1%} | {surr:.2f} | **{verdict}** |")
        lines.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
