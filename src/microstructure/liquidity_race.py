"""
Conditional-logit estimator for the liquidity race, plus its evaluation metrics.

The alternatives are the levels live at a snapshot; the outside option ("nothing
was touched inside 24h") is pinned at v_0 = 0, which is what makes the predicted
probabilities sum to one over an outcome set that genuinely includes "price went
nowhere". Everything here is pure numpy/scipy and file-I/O free so the research
script, the parity checker and the tests all share one implementation.

Fitting is done on flattened rows plus a snapshot id, rather than a ragged list of
per-snapshot matrices: choice sets vary from 1 to 12 members, and np.bincount over
a snapshot id is both simpler and an order of magnitude faster than looping.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

V_CLIP = 30.0    # exp(30) ~ 1e13: far inside float64, guards a diverging step


@dataclass
class ChoiceData:
    X: np.ndarray          # (n_rows, n_feat) — one row per (snapshot, level)
    snap_id: np.ndarray    # (n_rows,) int64 — which snapshot each row belongs to
    chosen: np.ndarray     # (n_snap,) int64 — row index first touched, or -1 for none
    day_id: np.ndarray     # (n_snap,) int64 — UTC day ordinal, for block resampling

    @property
    def n_snap(self) -> int:
        return len(self.chosen)

    @property
    def n_feat(self) -> int:
        return self.X.shape[1]


def _utilities(beta: np.ndarray, data: ChoiceData) -> tuple[np.ndarray, np.ndarray]:
    v = np.clip(data.X @ beta, -V_CLIP, V_CLIP)
    ev = np.exp(v)
    denom = 1.0 + np.bincount(data.snap_id, weights=ev, minlength=data.n_snap)
    return v, denom


def neg_log_lik(beta: np.ndarray, data: ChoiceData, lam: float = 0.0
                ) -> tuple[float, np.ndarray]:
    """Negative log-likelihood and its gradient, with an L2 penalty."""
    v, denom = _utilities(beta, data)
    nll = float(np.sum(np.log(denom)))
    picked = data.chosen >= 0
    rows = data.chosen[picked]
    nll -= float(np.sum(v[rows]))

    ev = np.exp(v)
    p = ev / denom[data.snap_id]
    grad = data.X.T @ p
    if rows.size:
        grad -= data.X[rows].sum(axis=0)

    nll += lam * float(beta @ beta)
    grad += 2.0 * lam * beta
    return nll, grad


def fit_conditional_logit(data: ChoiceData, lam: float = 0.0,
                          x0: np.ndarray | None = None) -> np.ndarray:
    start = np.zeros(data.n_feat) if x0 is None else np.asarray(x0, dtype=float)
    res = minimize(neg_log_lik, start, args=(data, lam), jac=True,
                   method="L-BFGS-B",
                   options={"maxiter": 2000, "ftol": 1e-12, "gtol": 1e-9})
    return np.asarray(res.x, dtype=float)


def predict_probs(beta: np.ndarray, data: ChoiceData) -> tuple[np.ndarray, np.ndarray]:
    """(P(level i is first) per row, P(nothing touched) per snapshot)."""
    v, denom = _utilities(beta, data)
    p_rows = np.exp(v) / denom[data.snap_id]
    p_none = 1.0 / denom
    return p_rows, p_none


def zscore_fit(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = X.mean(axis=0)
    std = X.std(axis=0)
    std = np.where(std < 1e-12, 1.0, std)     # constant column -> leave it alone
    return mean, std


def zscore_apply(X: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (X - mean) / std


def subset(data: ChoiceData, snap_mask: np.ndarray) -> ChoiceData:
    """Slice out a subset of snapshots, reindexing rows and choices."""
    keep_snaps = np.flatnonzero(snap_mask)
    row_mask = np.isin(data.snap_id, keep_snaps)
    row_idx = np.flatnonzero(row_mask)
    # old row index -> new row index
    remap = np.full(data.X.shape[0], -1, dtype=np.int64)
    remap[row_idx] = np.arange(len(row_idx))
    # old snapshot id -> new snapshot id
    snap_remap = np.full(data.n_snap, -1, dtype=np.int64)
    snap_remap[keep_snaps] = np.arange(len(keep_snaps))
    chosen = np.array([-1 if data.chosen[s] < 0 else remap[data.chosen[s]]
                       for s in keep_snaps], dtype=np.int64)
    return ChoiceData(X=data.X[row_idx],
                      snap_id=snap_remap[data.snap_id[row_idx]],
                      chosen=chosen,
                      day_id=data.day_id[keep_snaps])
