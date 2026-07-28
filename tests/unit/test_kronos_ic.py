import sys, pathlib
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "kronos"))
from kronos_ic import spearman_ic, sign_hit_rate, ic_by_year


def test_spearman_recovers_positive_signal():
    rng = np.random.default_rng(1)
    real = rng.standard_normal(2000)
    pred = 0.6 * real + 0.4 * rng.standard_normal(2000)  # correlated
    assert spearman_ic(pred, real) > 0.3


def test_spearman_random_is_near_zero():
    rng = np.random.default_rng(2)
    assert abs(spearman_ic(rng.standard_normal(2000), rng.standard_normal(2000))) < 0.08


def test_sign_hit_rate_perfect():
    real = np.array([1.0, -2.0, 3.0, -4.0])
    assert sign_hit_rate(real, real) == 1.0


def test_ic_by_year_splits_correctly():
    n = 1000
    years = np.where(np.arange(n) < 500, 2025, 2026).astype(np.int64)
    real = np.random.default_rng(3).standard_normal(n)
    # signal only in 2026 half
    pred = np.where(years == 2026, 0.7 * real, np.random.default_rng(4).standard_normal(n))
    arrays = {"year": years, "pred_ret_h1": pred, "real_ret_h1": real}
    out = ic_by_year(arrays, horizon=1)
    assert out[2026] > 0.3
    assert abs(out[2025]) < 0.12


from kronos_ic import strict_fill_sim


def test_strict_fill_profitable_signal():
    rng = np.random.default_rng(10)
    real = rng.standard_normal(3000) * 0.002          # ~0.2% moves
    pred = 0.8 * real + 0.2 * rng.standard_normal(3000) * 0.002
    res = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)
    assert res["pf"] > 1.2 and res["n"] == 3000


def test_strict_fill_random_signal_breakeven():
    rng = np.random.default_rng(11)
    real = rng.standard_normal(3000) * 0.002
    pred = rng.standard_normal(3000) * 0.002           # uncorrelated
    res = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)
    assert 0.8 < res["pf"] < 1.25


def test_cost_reduces_pf():
    rng = np.random.default_rng(12)
    real = rng.standard_normal(3000) * 0.002
    pred = 0.8 * real + 0.2 * rng.standard_normal(3000) * 0.002
    free = strict_fill_sim(pred, real, cost_bps=0.0, threshold=0.0)["pf"]
    costed = strict_fill_sim(pred, real, cost_bps=5.0, threshold=0.0)["pf"]
    assert costed < free
