import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_forecast import build_eval_indices


def test_future_strictly_after_observed():
    ctx, horizon, n = 512, 4, 5000
    idxs = build_eval_indices(n, ctx, horizon, stride=10)
    assert idxs, "expected some windows"
    for t in idxs:
        assert t - ctx + 1 >= 0          # enough history
        assert t + horizon <= n - 1      # enough future
        # future bars (t+1..t+horizon) are strictly AFTER the last observed bar t
        assert (t + 1) > t


def test_stride_spacing():
    idxs = build_eval_indices(3000, 512, 4, stride=12)
    assert all(b - a == 12 for a, b in zip(idxs, idxs[1:]))
