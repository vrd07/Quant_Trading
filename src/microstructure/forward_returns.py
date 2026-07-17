"""
Forward-return labeling for order-flow marks (Stage-2 front half).

Pure: no I/O, no ML, no global state. R = risk-multiple (1R = sl_atr x ATR in
price). Every quantity here is a PROXY measurement over proxy signals — this
tool decides whether a mark is worth trading, not whether the marks are "real
order flow". A sweep of `dead`/`thin` verdicts is a valid, money-saving result.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_LONG = {"bullish_divergence", "sweep_low", "absorption_of_selling", "imbalance_buy"}
_SHORT = {"bearish_divergence", "sweep_high", "absorption_of_buying", "imbalance_sell"}


def event_direction(kind: str) -> str | None:
    """Implied trade side of a FlowEvent kind; None = directionless."""
    if kind in _LONG:
        return "long"
    if kind in _SHORT:
        return "short"
    return None


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Rolling-mean true range over open/high/low/close bars."""
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


@dataclass(frozen=True)
class LabelConfig:
    sl_atr: float = 1.0
    tp_atr: float = 2.0
    max_hold_bars: int = 16
    cost_pts: float = 0.4
    timeframe: str = "15min"


def label_event(mids: pd.Series, direction: str, atr_val: float,
                cfg: LabelConfig) -> dict | None:
    """Triple-barrier outcome over the tick path `mids` (entry = mids[0]).

    Walks ticks chronologically so an intrabar stop-then-target counts as the
    STOP. R_net is net of round-trip cost (2 x cost_pts, converted to R).
    """
    if atr_val <= 0 or len(mids) == 0:
        return None
    risk = cfg.sl_atr * atr_val
    entry = float(mids.iloc[0])
    entry_ts = mids.index[0]
    deadline = entry_ts + cfg.max_hold_bars * pd.Timedelta(cfg.timeframe)
    sign = 1.0 if direction == "long" else -1.0
    stop = entry - sign * risk
    target = entry + sign * cfg.tp_atr * atr_val
    cost_R = 2.0 * cfg.cost_pts / risk

    mfe = mae = 0.0
    outcome, gross_R, exit_i = "time", 0.0, len(mids) - 1
    for i in range(len(mids)):
        px = float(mids.iloc[i])
        excursion = sign * (px - entry) / risk
        mfe, mae = max(mfe, excursion), min(mae, excursion)
        hit_stop = (px <= stop) if direction == "long" else (px >= stop)
        hit_tgt = (px >= target) if direction == "long" else (px <= target)
        if hit_stop:                       # checked first: ties resolve to stop
            outcome, gross_R, exit_i = "stop", -1.0, i
            break
        if hit_tgt:
            outcome, gross_R, exit_i = "target", cfg.tp_atr / cfg.sl_atr, i
            break
        if mids.index[i] >= deadline:
            outcome, gross_R, exit_i = "time", sign * (px - entry) / risk, i
            break
    else:
        gross_R = sign * (float(mids.iloc[-1]) - entry) / risk
    return {"direction": direction, "outcome": outcome,
            "R_net": gross_R - cost_R, "bars_held": exit_i,
            "mae": mae, "mfe": mfe}


MIN_CELL_N = 30


def _cell_stats(kind: str, direction: str, rows: list[dict],
                boundary_ts) -> dict:
    r = np.array([e["R_net"] for e in rows], dtype=float)
    is_r = np.array([e["R_net"] for e in rows if e["ts"] <= boundary_ts])
    oos_r = np.array([e["R_net"] for e in rows if e["ts"] > boundary_ts])
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    t_stat = float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 else 0.0
    exp_is = float(is_r.mean()) if len(is_r) else 0.0
    exp_oos = float(oos_r.mean()) if len(oos_r) else 0.0
    n_is, n_oos = len(is_r), len(oos_r)
    if n_is < MIN_CELL_N or n_oos < MIN_CELL_N:
        verdict = "thin"
    elif exp_is > 0 and exp_oos > 0 and t_stat > 2:
        verdict = "CANDIDATE"
    elif exp_is > 0 or exp_oos > 0:
        verdict = "one-sided"
    else:
        verdict = "dead"
    return {"kind": kind, "direction": direction, "n": len(r),
            "n_is": n_is, "n_oos": n_oos, "expectancy": float(r.mean()),
            "exp_is": exp_is, "exp_oos": exp_oos,
            "win_rate": float((r > 0).mean()), "profit_factor": pf,
            "total_R": float(r.sum()), "t_stat": t_stat,
            "median_ticks": float(np.median([e["bars_held"] for e in rows])),
            "mean_mae": float(np.mean([e.get("mae", 0.0) for e in rows])),
            "mean_mfe": float(np.mean([e.get("mfe", 0.0) for e in rows])),
            "verdict": verdict}


def summarize(events: list[dict], split_frac: float = 0.7) -> dict:
    """Per (kind, direction) triple-barrier stats with a global time IS/OOS
    split and a CANDIDATE/one-sided/thin/dead verdict per cell."""
    if not events:
        return {"boundary_ts": None, "cells": []}
    ts_sorted = sorted(e["ts"] for e in events)
    boundary_ts = ts_sorted[min(int(len(ts_sorted) * split_frac),
                                len(ts_sorted) - 1)]
    groups: dict[tuple, list[dict]] = {}
    for e in events:
        groups.setdefault((e["kind"], e["direction"]), []).append(e)
    cells = [_cell_stats(k, d, rows, boundary_ts) for (k, d), rows in groups.items()]
    cells.sort(key=lambda c: c["total_R"], reverse=True)
    return {"boundary_ts": boundary_ts, "cells": cells}
