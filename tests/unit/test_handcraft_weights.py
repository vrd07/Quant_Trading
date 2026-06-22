"""Unit tests for Carver handcrafting (scripts/handcraft_weights.py).

Pure-function tests on the allocation algorithm — no tapes, no MT5. They pin the
defining properties of handcrafting: weights are a valid distribution, a
diversifier is rewarded, equal correlation gives equal weight, and the Sharpe
tilt behaves (off by default, collapses negative edges).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from handcraft_weights import (  # noqa: E402
    apply_sharpe_tilt,
    diversification_multiplier,
    handcraft_weights,
)


def _corr(names, mat):
    return pd.DataFrame(mat, index=names, columns=names)


def test_weights_form_a_distribution():
    c = _corr(["a", "b", "c"], [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    w = handcraft_weights(c)
    assert set(w) == {"a", "b", "c"}
    assert sum(w.values()) == pytest.approx(1.0)
    assert all(v >= 0 for v in w.values())


def test_single_and_pair():
    assert handcraft_weights(_corr(["a"], [[1]])) == {"a": 1.0}
    pair = handcraft_weights(_corr(["a", "b"], [[1, 0.5], [0.5, 1]]))
    assert pair == {"a": 0.5, "b": 0.5}


def test_diversifier_is_rewarded():
    # a,b nearly identical; c independent. c should branch off at the root and
    # take ~half, while a,b split the other half.
    c = _corr(["a", "b", "c"], [[1.0, 0.95, 0.0],
                                [0.95, 1.0, 0.0],
                                [0.0, 0.0, 1.0]])
    w = handcraft_weights(c)
    assert w["c"] == pytest.approx(0.5, abs=1e-9)
    assert w["a"] == pytest.approx(0.25, abs=1e-9)
    assert w["b"] == pytest.approx(0.25, abs=1e-9)
    assert w["c"] > w["a"]


def test_equal_correlation_is_balanced():
    rho = 0.3
    c = _corr(list("abcd"), [[1 if i == j else rho for j in range(4)]
                             for i in range(4)])
    w = handcraft_weights(c)
    # symmetric inputs -> no strategy starved; spread stays tight around 1/N.
    assert min(w.values()) >= 0.15
    assert max(w.values()) <= 0.35


def test_sharpe_tilt_off_by_default():
    w = {"a": 0.5, "b": 0.3, "c": 0.2}
    assert apply_sharpe_tilt(w, {"a": 2.0, "b": 1.0, "c": 0.5}, lam=0.0) == w


def test_sharpe_tilt_collapses_negative_edge():
    w = {"a": 0.5, "b": 0.5}
    out = apply_sharpe_tilt(w, {"a": 1.5, "b": -0.8}, lam=1.0)
    assert out["a"] == pytest.approx(1.0)
    assert out["b"] == pytest.approx(0.0)


def test_sharpe_tilt_all_negative_falls_back():
    w = {"a": 0.6, "b": 0.4}
    out = apply_sharpe_tilt(w, {"a": -0.2, "b": -0.5}, lam=1.0)
    assert out == w  # nothing to tilt toward -> untouched


def test_idm_at_least_one_and_higher_when_uncorrelated():
    names = ["a", "b", "c"]
    eq = {n: 1 / 3 for n in names}
    indep = _corr(names, np.eye(3))
    corr = _corr(names, [[1, 0.8, 0.8], [0.8, 1, 0.8], [0.8, 0.8, 1]])
    idm_indep = diversification_multiplier(eq, indep)
    idm_corr = diversification_multiplier(eq, corr)
    assert idm_indep >= idm_corr >= 1.0
    assert idm_indep == pytest.approx(np.sqrt(3), abs=1e-9)
