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


# ------------------------------------------------------------------ metrics

import pandas as pd  # noqa: E402  (kept beside the metrics that use it)


def per_snapshot_nll(beta: np.ndarray, data: ChoiceData) -> np.ndarray:
    """-log P(actual outcome) for each snapshot. Bootstrapping needs the terms."""
    v, denom = _utilities(beta, data)
    terms = np.log(denom)
    picked = data.chosen >= 0
    terms[picked] -= v[data.chosen[picked]]
    return terms


def log_loss(beta: np.ndarray, data: ChoiceData) -> float:
    if data.n_snap == 0:
        return float("nan")
    return float(per_snapshot_nll(beta, data).mean())


def top1_accuracy(beta: np.ndarray, data: ChoiceData) -> tuple[float, float]:
    """(overall, conditional). Overall includes the outside option as a candidate."""
    if data.n_snap == 0:
        return float("nan"), float("nan")
    p_rows, p_none = predict_probs(beta, data)
    order = np.lexsort((-p_rows, data.snap_id))
    starts = np.searchsorted(data.snap_id[order], np.arange(data.n_snap))
    best_row = order[starts]                       # highest-p row per snapshot
    best_p = p_rows[best_row]
    pred_none = best_p <= p_none
    correct = np.where(pred_none, data.chosen < 0, data.chosen == best_row)
    overall = float(correct.mean())
    hit = data.chosen >= 0
    conditional = float((data.chosen[hit] == best_row[hit]).mean()) if hit.any() else float("nan")
    return overall, conditional


def day_block_folds(day_id: np.ndarray, n_folds: int = 5,
                    seed: int = 0) -> list[np.ndarray]:
    """Boolean snapshot masks, assigned so a whole day lands in one fold."""
    days = np.unique(day_id)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(days)
    buckets = np.array_split(shuffled, n_folds)
    return [np.isin(day_id, b) for b in buckets]


def day_block_bootstrap_diff(beta_model: np.ndarray, beta_base: np.ndarray,
                             data_model: ChoiceData, data_base: ChoiceData,
                             n_reps: int = 2000, seed: int = 0
                             ) -> tuple[float, float, float]:
    """95% CI on mean(model NLL) - mean(baseline NLL), resampling WHOLE DAYS.

    The two ChoiceData objects describe the same snapshots in the same order — they
    differ only in their feature columns (full model vs distance-only baseline).
    """
    if data_model.n_snap != data_base.n_snap:
        raise ValueError("model and baseline must cover the same snapshots")
    nll_m = per_snapshot_nll(beta_model, data_model)
    nll_b = per_snapshot_nll(beta_base, data_base)
    diff = nll_m - nll_b

    days = np.unique(data_model.day_id)
    by_day = {d: np.flatnonzero(data_model.day_id == d) for d in days}
    day_sums = np.array([diff[by_day[d]].sum() for d in days])
    day_counts = np.array([len(by_day[d]) for d in days], dtype=float)

    rng = np.random.default_rng(seed)
    stats = np.empty(n_reps, dtype=float)
    n_days = len(days)
    for r in range(n_reps):
        pick = rng.integers(0, n_days, size=n_days)
        stats[r] = day_sums[pick].sum() / day_counts[pick].sum()
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(diff.mean()), float(lo), float(hi)


def reliability(p: np.ndarray, hit: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Predicted vs observed frequency, by equal-count bin of predicted probability."""
    p = np.asarray(p, dtype=float)
    hit = np.asarray(hit).astype(bool)
    if p.size == 0:
        return pd.DataFrame(columns=["bin", "n", "p_mean", "observed", "gap"])
    order = np.argsort(p)
    buckets = np.array_split(order, n_bins)
    rows = []
    for b, idx in enumerate(buckets):
        if len(idx) == 0:
            continue
        pm = float(p[idx].mean())
        ob = float(hit[idx].mean())
        rows.append({"bin": b, "n": int(len(idx)), "p_mean": pm,
                     "observed": ob, "gap": ob - pm})
    return pd.DataFrame(rows)


def expected_calibration_error(table: pd.DataFrame) -> float:
    if table.empty:
        return float("nan")
    w = table["n"].to_numpy(dtype=float)
    return float(np.sum(w * np.abs(table["gap"].to_numpy())) / w.sum())


def platt_scale(p: np.ndarray, hit: np.ndarray) -> tuple[float, float]:
    """Fit logit(p_cal) = a * logit(p) + b by 1-D logistic regression."""
    eps = 1e-9
    z = np.log(np.clip(p, eps, 1 - eps) / (1 - np.clip(p, eps, 1 - eps)))
    y = np.asarray(hit).astype(float)

    def obj(theta):
        a, b = theta
        lin = np.clip(a * z + b, -V_CLIP, V_CLIP)
        q = 1.0 / (1.0 + np.exp(-lin))
        nll = -float(np.sum(y * np.log(q + eps) + (1 - y) * np.log(1 - q + eps)))
        g = q - y
        return nll, np.array([float(np.sum(g * z)), float(np.sum(g))])

    res = minimize(obj, np.array([1.0, 0.0]), jac=True, method="L-BFGS-B")
    return float(res.x[0]), float(res.x[1])


def platt_apply(p: np.ndarray, a: float, b: float) -> np.ndarray:
    eps = 1e-9
    pc = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    z = np.log(pc / (1 - pc))
    return 1.0 / (1.0 + np.exp(-np.clip(a * z + b, -V_CLIP, V_CLIP)))
