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
