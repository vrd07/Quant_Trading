"""Prediction-cache contract shared by kronos_forecast (writer) and kronos_ic (reader).
The .npz cache is the ONLY interface between the torch half and the analysis half.
This module MUST NOT import torch."""
import numpy as np

ARRAY_FIELDS = [
    "timestamp",  # int64 unix seconds; window decision time (last observed bar close)
    "year",       # int16
    "pred_ret_h1", "pred_ret_h2", "pred_ret_h3", "pred_ret_h4",
    "pred_disp_h4",  # std across sampled paths at H=4 (may be nan if unavailable)
    "real_ret_h1", "real_ret_h2", "real_ret_h3", "real_ret_h4",
    "last_close",
]
META_KEYS = ["symbol", "model_id", "stride", "n_paths", "horizon",
             "temperature", "top_p", "top_k", "git_commit", "created_at"]


def save_cache(path, arrays, meta):
    missing = [f for f in ARRAY_FIELDS if f not in arrays]
    if missing:
        raise ValueError(f"cache missing fields: {missing}")
    n = len(arrays[ARRAY_FIELDS[0]])
    for f in ARRAY_FIELDS:
        if len(arrays[f]) != n:
            raise ValueError(f"field {f} length {len(arrays[f])} != {n}")
    payload = {f: np.asarray(arrays[f]) for f in ARRAY_FIELDS}
    payload["_meta"] = np.array([f"{k}={meta.get(k, '')}" for k in META_KEYS])
    np.savez(path, **payload)


def load_cache(path):
    z = np.load(path, allow_pickle=False)
    arrays = {f: z[f] for f in ARRAY_FIELDS}
    meta = {}
    for item in z["_meta"]:
        k, _, v = str(item).partition("=")
        meta[k] = v
    return arrays, meta
