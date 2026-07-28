import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_cache import ARRAY_FIELDS, save_cache, load_cache


def _synthetic(n=20):
    rng = np.random.default_rng(0)
    return {f: (rng.standard_normal(n) if f not in ("timestamp", "year")
                else np.arange(n, dtype=np.int64)) for f in ARRAY_FIELDS}


def test_round_trip_preserves_all_fields(tmp_path):
    arrays = _synthetic()
    p = tmp_path / "c.npz"
    save_cache(str(p), arrays, {"symbol": "XAUUSD", "n_paths": 20})
    got, meta = load_cache(str(p))
    for f in ARRAY_FIELDS:
        np.testing.assert_allclose(got[f], arrays[f])
    assert meta["symbol"] == "XAUUSD"
    assert meta["n_paths"] == "20"


def test_missing_field_raises(tmp_path):
    arrays = _synthetic()
    del arrays["pred_ret_h1"]
    import pytest
    with pytest.raises(ValueError):
        save_cache(str(tmp_path / "c.npz"), arrays, {})
