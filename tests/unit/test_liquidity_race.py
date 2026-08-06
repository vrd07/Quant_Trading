"""Unit tests for src/microstructure/liquidity_race.py — synthetic choice data."""
import numpy as np
import pytest

from src.microstructure import liquidity_race as lr


def make_synthetic(beta_true, n_snap=4000, n_feat=3, set_size=4, seed=7):
    """Generate choices from a known beta so the fitter can be checked for recovery."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n_snap * set_size, n_feat))
    snap_id = np.repeat(np.arange(n_snap), set_size)
    chosen = np.empty(n_snap, dtype=np.int64)
    v = X @ beta_true
    for s in range(n_snap):
        rows = np.arange(s * set_size, (s + 1) * set_size)
        ev = np.exp(v[rows])
        probs = np.concatenate([[1.0], ev]) / (1.0 + ev.sum())
        pick = rng.choice(len(probs), p=probs)
        chosen[s] = -1 if pick == 0 else rows[pick - 1]
    day_id = snap_id[::set_size] // 20
    return lr.ChoiceData(X=X, snap_id=snap_id, chosen=chosen, day_id=day_id)


class TestLogLikelihood:
    def test_zero_beta_gives_uniform_probabilities(self):
        data = make_synthetic(np.zeros(3), n_snap=10, set_size=4)
        p_rows, p_none = lr.predict_probs(np.zeros(3), data)
        assert p_rows == pytest.approx(np.full(len(p_rows), 1.0 / 5.0))
        assert p_none == pytest.approx(np.full(len(p_none), 1.0 / 5.0))

    def test_probabilities_sum_to_one_per_snapshot(self):
        data = make_synthetic(np.array([0.7, -0.4, 0.2]), n_snap=50)
        beta = np.array([0.3, -0.2, 0.1])
        p_rows, p_none = lr.predict_probs(beta, data)
        totals = np.bincount(data.snap_id, weights=p_rows) + p_none
        assert totals == pytest.approx(np.ones(len(p_none)))

    def test_gradient_matches_finite_differences(self):
        data = make_synthetic(np.array([0.5, -0.3, 0.8]), n_snap=200)
        beta = np.array([0.2, 0.1, -0.4])
        _, grad = lr.neg_log_lik(beta, data, lam=0.01)
        eps = 1e-6
        for j in range(len(beta)):
            up, dn = beta.copy(), beta.copy()
            up[j] += eps
            dn[j] -= eps
            fd = (lr.neg_log_lik(up, data, 0.01)[0] - lr.neg_log_lik(dn, data, 0.01)[0]) / (2 * eps)
            assert grad[j] == pytest.approx(fd, rel=1e-4, abs=1e-4)

    def test_l2_penalty_shrinks_the_solution(self):
        beta_true = np.array([1.2, -0.9, 0.5])
        data = make_synthetic(beta_true, n_snap=3000)
        loose = lr.fit_conditional_logit(data, lam=0.0)
        tight = lr.fit_conditional_logit(data, lam=500.0)
        assert np.linalg.norm(tight) < np.linalg.norm(loose)

    # ------------------------------------------------------------- hardening

    def _tiny(self, chosen_row):
        """One snapshot, two rows, an identity-ish X so v == beta component-wise."""
        return lr.ChoiceData(
            X=np.array([[1.0, 0.0], [0.0, 1.0]]),
            snap_id=np.array([0, 0], dtype=np.int64),
            chosen=np.array([chosen_row], dtype=np.int64),
            day_id=np.array([0], dtype=np.int64),
        )

    def test_neg_log_lik_matches_a_hand_computed_value_and_gradient(self):
        """Pins the exact formula, including the -sum(v[chosen]) term.

        The plan's own tests never pin an absolute likelihood: the zero-beta test
        has v == 0 (so the chosen term vanishes) and the FD test only checks that
        the gradient is consistent with whatever the value happens to be.
        """
        data = self._tiny(chosen_row=0)
        beta = np.array([0.5, -0.25])
        # v = [0.5, -0.25]; denom = 1 + e^0.5 + e^-0.25
        denom = 1.0 + np.exp(0.5) + np.exp(-0.25)
        expected_nll = np.log(denom) - 0.5
        nll, grad = lr.neg_log_lik(beta, data, lam=0.0)
        assert nll == pytest.approx(expected_nll)
        # grad = X.T @ p - X[chosen] = [p0 - 1, p1]
        p0, p1 = np.exp(0.5) / denom, np.exp(-0.25) / denom
        assert grad == pytest.approx(np.array([p0 - 1.0, p1]))

    def test_choosing_the_other_row_changes_the_likelihood(self):
        """A no-op regression on the chosen term would give the same answer here."""
        beta = np.array([0.5, -0.25])
        nll_a, grad_a = lr.neg_log_lik(beta, self._tiny(0), lam=0.0)
        nll_b, grad_b = lr.neg_log_lik(beta, self._tiny(1), lam=0.0)
        assert nll_a != pytest.approx(nll_b)
        assert nll_b - nll_a == pytest.approx(0.75)   # -v[1] - (-v[0]) = 0.5+0.25
        assert grad_a != pytest.approx(grad_b)

    def test_l2_penalty_enters_the_value_not_only_the_gradient(self):
        data = self._tiny(chosen_row=0)
        beta = np.array([0.5, -0.25])
        base, base_g = lr.neg_log_lik(beta, data, lam=0.0)
        pen, pen_g = lr.neg_log_lik(beta, data, lam=2.0)
        assert pen - base == pytest.approx(2.0 * float(beta @ beta))
        assert pen_g - base_g == pytest.approx(2.0 * 2.0 * beta)

    def test_utilities_are_clipped_at_exactly_v_clip(self):
        """Pins the clip's VALUE, not merely that nothing overflows.

        A single-row snapshot with a runaway utility makes p_none == 1/(1+e^V_CLIP).
        At the documented 30.0 that is ~9.4e-14; at, say, 300.0 it would be ~5e-131,
        so the two are separated by ~117 orders of magnitude and a rel comparison
        pins the constant sharply.
        """
        assert lr.V_CLIP == 30.0
        data = lr.ChoiceData(X=np.array([[1.0]]), snap_id=np.array([0], dtype=np.int64),
                             chosen=np.array([0], dtype=np.int64),
                             day_id=np.array([0], dtype=np.int64))
        p_rows, p_none = lr.predict_probs(np.array([1000.0]), data)
        # Without the clip, exp(1000) is inf: the denominator overflows, p_none
        # collapses to exactly 0.0 and p_rows becomes inf/inf == nan.
        assert np.isfinite(p_rows).all() and np.isfinite(p_none).all()
        assert not np.isnan(p_rows).any()
        assert p_rows[0] == pytest.approx(np.exp(30.0) / (1.0 + np.exp(30.0)), rel=1e-9)
        # abs=0.0 is REQUIRED: the expected value ~9.4e-14 sits below pytest.approx's
        # default 1e-12 absolute floor, under which p_none == 0.0 compares equal and
        # the assertion would pass against an unclipped implementation.
        assert p_none[0] == pytest.approx(1.0 / (1.0 + np.exp(30.0)), rel=1e-9, abs=0.0)
        assert p_none[0] > 0.0

    def test_clip_is_symmetric_on_the_negative_side(self):
        data = lr.ChoiceData(X=np.array([[1.0]]), snap_id=np.array([0], dtype=np.int64),
                             chosen=np.array([-1], dtype=np.int64),
                             day_id=np.array([0], dtype=np.int64))
        p_rows, p_none = lr.predict_probs(np.array([-1000.0]), data)
        assert np.isfinite(p_rows).all() and np.isfinite(p_none).all()
        # Unclipped, exp(-1000) underflows to 0.0 and this row's probability
        # vanishes entirely; abs=0.0 again for the sub-1e-12 expected value.
        assert p_rows[0] == pytest.approx(np.exp(-30.0) / (1.0 + np.exp(-30.0)),
                                          rel=1e-9, abs=0.0)
        assert p_rows[0] > 0.0

    def test_a_trailing_snapshot_with_no_rows_gets_p_none_of_one(self):
        """Pins the `minlength=n_snap` in the bincount.

        Every synthetic fixture in the plan gives each snapshot at least one row,
        so without this the missing minlength returns a SHORT denom and goes
        unnoticed. A real snapshot can legitimately have an empty choice set.
        """
        data = lr.ChoiceData(
            X=np.array([[0.5], [0.25]]),
            snap_id=np.array([0, 1], dtype=np.int64),
            chosen=np.array([-1, -1, -1], dtype=np.int64),   # snapshot 2 has no rows
            day_id=np.array([0, 0, 0], dtype=np.int64),
        )
        p_rows, p_none = lr.predict_probs(np.array([1.0]), data)
        assert len(p_none) == 3
        assert p_none[2] == pytest.approx(1.0)
        totals = np.bincount(data.snap_id, weights=p_rows, minlength=3) + p_none
        assert totals == pytest.approx(np.ones(3))

    def test_all_none_dataset_fits_and_pushes_probability_to_the_outside_option(self):
        """Exercises the `if rows.size:` branch with an empty chosen set."""
        rng = np.random.default_rng(5)
        n_snap, set_size = 500, 3
        data = lr.ChoiceData(
            X=np.abs(rng.normal(size=(n_snap * set_size, 2))) + 0.5,
            snap_id=np.repeat(np.arange(n_snap), set_size).astype(np.int64),
            chosen=np.full(n_snap, -1, dtype=np.int64),
            day_id=(np.arange(n_snap) // 20).astype(np.int64),
        )
        nll, grad = lr.neg_log_lik(np.zeros(2), data, lam=0.0)
        assert np.isfinite(nll) and np.isfinite(grad).all()
        beta = lr.fit_conditional_logit(data, lam=0.0)
        assert np.isfinite(beta).all()
        _, p_none = lr.predict_probs(beta, data)
        assert p_none.mean() > 0.9


class TestFitterRecovery:
    def test_recovers_a_known_beta(self):
        beta_true = np.array([1.0, -0.75, 0.4])
        data = make_synthetic(beta_true, n_snap=20000, seed=11)
        beta_hat = lr.fit_conditional_logit(data, lam=0.0)
        assert beta_hat == pytest.approx(beta_true, abs=0.10)

    def test_recovery_survives_variable_set_sizes(self):
        rng = np.random.default_rng(3)
        beta_true = np.array([0.8, -0.5])
        rows, snap_id, chosen = [], [], []
        cursor = 0
        for s in range(15000):
            k = int(rng.integers(1, 7))
            Xs = rng.normal(size=(k, 2))
            v = Xs @ beta_true
            ev = np.exp(v)
            probs = np.concatenate([[1.0], ev]) / (1.0 + ev.sum())
            pick = rng.choice(len(probs), p=probs)
            rows.append(Xs)
            snap_id.extend([s] * k)
            chosen.append(-1 if pick == 0 else cursor + pick - 1)
            cursor += k
        data = lr.ChoiceData(X=np.vstack(rows),
                             snap_id=np.array(snap_id, dtype=np.int64),
                             chosen=np.array(chosen, dtype=np.int64),
                             day_id=np.arange(15000) // 20)
        beta_hat = lr.fit_conditional_logit(data, lam=0.0)
        assert beta_hat == pytest.approx(beta_true, abs=0.12)

    def test_x0_seeds_the_optimiser_without_changing_the_optimum(self):
        beta_true = np.array([0.9, -0.6, 0.3])
        data = make_synthetic(beta_true, n_snap=4000, seed=17)
        cold = lr.fit_conditional_logit(data, lam=0.0)
        warm = lr.fit_conditional_logit(data, lam=0.0, x0=np.array([5.0, -5.0, 5.0]))
        assert warm == pytest.approx(cold, abs=1e-3)


class TestZScore:
    def test_apply_inverts_to_zero_mean_unit_std(self):
        X = np.random.default_rng(0).normal(loc=5.0, scale=3.0, size=(500, 4))
        mean, std = lr.zscore_fit(X)
        Z = lr.zscore_apply(X, mean, std)
        assert Z.mean(axis=0) == pytest.approx(np.zeros(4), abs=1e-9)
        assert Z.std(axis=0) == pytest.approx(np.ones(4), abs=1e-9)

    def test_constant_column_does_not_divide_by_zero(self):
        X = np.ones((10, 2))
        X[:, 1] = np.arange(10)
        mean, std = lr.zscore_fit(X)
        Z = lr.zscore_apply(X, mean, std)
        assert np.isfinite(Z).all()
        assert std[0] == 1.0        # degenerate column falls back to 1.0

    # ------------------------------------------------------------- hardening

    def test_apply_uses_the_passed_stats_not_recomputed_ones(self):
        """The IS -> OOS path. This is the leak the plan's own test cannot see.

        `test_apply_inverts_to_zero_mean_unit_std` applies the stats back to the
        very matrix they came from, so an implementation that ignored `mean`/`std`
        and re-derived them internally would pass it. Here the two matrices have
        deliberately different moments, so a recompute is visible.
        """
        rng = np.random.default_rng(1)
        X_train = rng.normal(loc=5.0, scale=3.0, size=(400, 3))
        X_test = rng.normal(loc=11.0, scale=6.0, size=(200, 3))
        mean, std = lr.zscore_fit(X_train)
        Z = lr.zscore_apply(X_test, mean, std)
        assert Z == pytest.approx((X_test - mean) / std)
        # a recompute-internally implementation would centre this on zero
        assert np.all(np.abs(Z.mean(axis=0)) > 1.0)
        assert np.all(Z.std(axis=0) > 1.5)

    def test_only_a_genuinely_degenerate_column_is_clamped(self):
        """Pins the 1e-12 threshold from above: a tiny but real std survives."""
        rng = np.random.default_rng(2)
        X = np.empty((300, 3))
        X[:, 0] = 4.0                                   # exactly constant -> clamped
        X[:, 1] = rng.normal(scale=1e-6, size=300)      # tiny but real -> preserved
        X[:, 2] = rng.normal(scale=2.0, size=300)
        mean, std = lr.zscore_fit(X)
        assert std[0] == 1.0
        assert std[1] == pytest.approx(X[:, 1].std())
        assert std[1] > 1e-12
        Z = lr.zscore_apply(X, mean, std)
        assert np.isfinite(Z).all()
        assert Z[:, 0] == pytest.approx(np.zeros(300))  # constant column -> all zeros
        assert Z[:, 1].std() == pytest.approx(1.0)      # NOT flattened by the clamp

    def test_zscore_fit_is_population_std_not_sample_std(self):
        """ddof=0 vs ddof=1 differ by sqrt(n/(n-1)); pin which one is meant."""
        X = np.arange(10, dtype=float).reshape(10, 1)
        _, std = lr.zscore_fit(X)
        assert std[0] == pytest.approx(X[:, 0].std())          # ddof=0
        assert std[0] != pytest.approx(X[:, 0].std(ddof=1))


class TestSubset:
    def test_subset_reindexes_snapshots_and_rows(self):
        data = make_synthetic(np.array([0.5, 0.5, 0.5]), n_snap=10, set_size=3)
        mask = np.zeros(10, dtype=bool)
        mask[[2, 5, 7]] = True
        sub = lr.subset(data, mask)
        assert len(sub.chosen) == 3
        assert sub.snap_id.max() == 2
        assert sub.X.shape[0] == 9
        # a snapshot whose original choice was "none" stays "none"
        for k, orig in enumerate([2, 5, 7]):
            assert (sub.chosen[k] == -1) == (data.chosen[orig] == -1)

    # ------------------------------------------------------------- hardening

    def test_subset_chosen_points_at_the_same_level_after_remapping(self):
        """The crux of `subset`, and the plan's test does not check it.

        Its assertions only compare -1-ness; an implementation that sliced
        `chosen` without remapping row indices would still satisfy them (the
        stale indices stay in range for these fixtures). Compare the feature rows.
        """
        data = make_synthetic(np.array([0.8, -0.5, 0.3]), n_snap=40, set_size=3, seed=23)
        keep = [4, 9, 17, 22, 31]
        mask = np.zeros(40, dtype=bool)
        mask[keep] = True
        sub = lr.subset(data, mask)
        picked = [(k, o) for k, o in enumerate(keep) if data.chosen[o] >= 0]
        assert picked, "fixture must contain at least one non-none snapshot"
        for k, orig in picked:
            assert sub.chosen[k] >= 0
            assert sub.X[sub.chosen[k]] == pytest.approx(data.X[data.chosen[orig]])
            # the remapped index must also sit inside its own snapshot
            assert sub.snap_id[sub.chosen[k]] == k

    def test_subset_carries_the_day_id_of_the_kept_snapshots(self):
        """make_synthetic's own day_id is all-zero at these sizes, which would make
        this assertion vacuous, so the fixture is given distinct days first."""
        data = make_synthetic(np.array([0.5, 0.5, 0.5]), n_snap=10, set_size=3)
        data.day_id = np.arange(10, dtype=np.int64) * 3
        mask = np.zeros(10, dtype=bool)
        mask[[2, 5, 7]] = True
        sub = lr.subset(data, mask)
        assert list(sub.day_id) == [6, 15, 21]

    def test_subset_rows_are_the_kept_snapshots_rows_in_order(self):
        data = make_synthetic(np.array([0.4, 0.4, 0.4]), n_snap=8, set_size=3, seed=31)
        mask = np.zeros(8, dtype=bool)
        mask[[1, 6]] = True
        sub = lr.subset(data, mask)
        expected = np.vstack([data.X[3:6], data.X[18:21]])
        assert sub.X == pytest.approx(expected)
        assert list(sub.snap_id) == [0, 0, 0, 1, 1, 1]

    def test_subset_of_everything_is_an_identity(self):
        data = make_synthetic(np.array([0.6, -0.2, 0.1]), n_snap=12, set_size=3, seed=41)
        sub = lr.subset(data, np.ones(12, dtype=bool))
        assert sub.X == pytest.approx(data.X)
        assert list(sub.snap_id) == list(data.snap_id)
        assert list(sub.chosen) == list(data.chosen)
        assert list(sub.day_id) == list(data.day_id)

    def test_subset_preserves_the_likelihood_of_the_kept_snapshots(self):
        """End-to-end: the sliced data must score exactly what those snapshots
        contributed to the full-sample negative log-likelihood."""
        data = make_synthetic(np.array([0.7, -0.3, 0.5]), n_snap=60, set_size=4, seed=13)
        beta = np.array([0.35, -0.15, 0.2])
        keep = np.zeros(60, dtype=bool)
        keep[[3, 11, 28, 44]] = True
        sub = lr.subset(data, keep)
        nll_sub, _ = lr.neg_log_lik(beta, sub, lam=0.0)

        _, p_none_full = lr.predict_probs(beta, data)
        v_full = np.clip(data.X @ beta, -lr.V_CLIP, lr.V_CLIP)
        expected = 0.0
        for s in np.flatnonzero(keep):
            expected += -np.log(p_none_full[s])           # == log(denom_s)
            if data.chosen[s] >= 0:
                expected -= v_full[data.chosen[s]]
        assert nll_sub == pytest.approx(expected)


class TestChoiceData:
    def test_n_snap_and_n_feat_report_the_shapes(self):
        data = make_synthetic(np.array([0.5, 0.5, 0.5]), n_snap=7, set_size=3)
        assert data.n_snap == 7
        assert data.n_feat == 3
        assert data.X.shape == (21, 3)
