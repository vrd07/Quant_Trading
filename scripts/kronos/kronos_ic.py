"""Stage-1 IC + Stage-2 strict-fill analysis of a Kronos prediction cache.
Pure numpy/pandas/scipy — MUST NOT import torch (tests run under production venv)."""
import numpy as np
from scipy.stats import spearmanr


def spearman_ic(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real)
    if m.sum() < 10:
        return float("nan")
    return float(spearmanr(pred[m], real[m]).correlation)


def sign_hit_rate(pred, real):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (pred != 0)
    if m.sum() == 0:
        return float("nan")
    return float((np.sign(pred[m]) == np.sign(real[m])).mean())


def ic_by_year(arrays, horizon):
    years = np.asarray(arrays["year"]).astype(int)
    pred = np.asarray(arrays[f"pred_ret_h{horizon}"], float)
    real = np.asarray(arrays[f"real_ret_h{horizon}"], float)
    out = {}
    for y in sorted(set(years.tolist())):
        s = years == y
        out[int(y)] = spearman_ic(pred[s], real[s])
    return out


def strict_fill_sim(pred, real, cost_bps, threshold):
    pred = np.asarray(pred, float)
    real = np.asarray(real, float)
    m = np.isfinite(pred) & np.isfinite(real) & (np.abs(pred) >= threshold)
    if m.sum() == 0:
        return {"pf": float("nan"), "ret": 0.0, "dd": 0.0, "wr": float("nan"), "n": 0}
    net = np.sign(pred[m]) * real[m] - cost_bps / 1e4  # round-trip cost in return units
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    equity = np.cumsum(net)
    dd = float((np.maximum.accumulate(equity) - equity).max()) if equity.size else 0.0
    return {"pf": pf, "ret": float(net.sum()), "dd": dd,
            "wr": float((net > 0).mean()), "n": int(m.sum())}
