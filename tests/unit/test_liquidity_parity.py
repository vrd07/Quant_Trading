"""Unit tests for scripts/check_liquidity_parity.py using synthesised export files."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.microstructure import liquidity_levels as ll   # noqa: E402
import importlib.util                                    # noqa: E402

spec = importlib.util.spec_from_file_location(
    "check_liquidity_parity", PROJECT_ROOT / "scripts/check_liquidity_parity.py")
clp = importlib.util.module_from_spec(spec)
# The module must be in sys.modules BEFORE exec: @dataclass resolves its own
# module via sys.modules.get(cls.__module__).__dict__, which is None for a
# module created by module_from_spec but never registered.
sys.modules[spec.name] = clp
spec.loader.exec_module(clp)


@pytest.fixture
def synthetic_export(tmp_path):
    """Build bars + a 'perfect' levels export straight from the Python definition.

    This is the harness testing itself: if Python-vs-Python does not pass, the
    harness cannot be trusted to judge Python-vs-MQL5.
    """
    n = 1400
    rng = np.random.default_rng(21)
    walk = 2000.0 + np.cumsum(rng.normal(0, 0.8, n))
    idx = pd.date_range("2026-01-05", periods=n, freq="15min", tz="UTC")
    bars = pd.DataFrame({"open": walk, "high": walk + rng.uniform(0.2, 1.5, n),
                         "low": walk - rng.uniform(0.2, 1.5, n), "close": walk,
                         "volume": 500.0}, index=idx)
    bars_csv = tmp_path / "GC_LQ_bars.csv"
    out = bars.reset_index().rename(columns={"index": "time_utc"})
    out["time_utc"] = out["time_utc"].dt.strftime("%Y.%m.%d %H:%M:%S")
    out.to_csv(bars_csv, index=False)

    ctx = ll.build_context(bars, ll.DEFAULTS)
    rows = []
    for t in range(n - 60, n):
        levels = ll.build_choice_set(ctx, t, ll.DEFAULTS)
        if not levels:
            continue
        X = ll.feature_matrix(ctx, t, levels, ll.DEFAULTS)
        for i, lv in enumerate(levels):
            row = {"snapshot_time_utc": idx[t].strftime("%Y.%m.%d %H:%M:%S"),
                   "row": i, "price": lv.price, "kind": lv.kind,
                   "formation_time_utc": idx[lv.formation_idx].strftime("%Y.%m.%d %H:%M:%S"),
                   "side": lv.side,
                   "dist_atr": (lv.price - ctx.close[t]) / ctx.atr[t],
                   "score": 0.0, "prob": 0.0}
            for f in range(len(ll.FEATURE_NAMES)):
                row[f"f{f}"] = X[i, f]
            rows.append(row)
    levels_csv = tmp_path / "GC_LQ_levels.csv"
    pd.DataFrame(rows).to_csv(levels_csv, index=False)
    return tmp_path


class TestParityHarness:
    def test_identical_export_passes(self, synthetic_export):
        result = clp.check(synthetic_export)
        assert result.ok, result.summary()
        assert result.n_snapshots > 0

    def test_a_shifted_price_is_caught(self, synthetic_export):
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        df.loc[df.index[0], "price"] = float(df.loc[df.index[0], "price"]) + 0.05
        df.to_csv(path, index=False)
        result = clp.check(synthetic_export)
        assert not result.ok
        assert any("price" in m for m in result.mismatches)

    def test_a_wrong_kind_is_caught(self, synthetic_export):
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        df.loc[df.index[0], "kind"] = "pw_high"
        df.to_csv(path, index=False)
        result = clp.check(synthetic_export)
        assert not result.ok
        assert any("kind" in m for m in result.mismatches)

    def test_a_missing_level_is_caught(self, synthetic_export):
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        first_ts = df.snapshot_time_utc.iloc[0]
        drop = df[(df.snapshot_time_utc == first_ts) & (df.row == 0)].index
        df.drop(index=drop).to_csv(path, index=False)
        result = clp.check(synthetic_export)
        assert not result.ok
        assert any("count" in m or "row" in m for m in result.mismatches)

    def test_a_feature_off_by_more_than_tolerance_is_caught(self, synthetic_export):
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        df.loc[df.index[0], "f6"] = float(df.loc[df.index[0], "f6"]) + 1.0
        df.to_csv(path, index=False)
        result = clp.check(synthetic_export)
        assert not result.ok
        assert any("f6" in m or "feature" in m for m in result.mismatches)

    def test_cli_exit_code_reflects_the_verdict(self, synthetic_export):
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts/check_liquidity_parity.py"),
             "--dir", str(synthetic_export), "--mqh", "does_not_exist.mqh"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_cli_exit_code_is_1_when_parity_fails(self, synthetic_export):
        """The success path alone does not pin the exit code: an implementation
        that always returned 0 would satisfy it. This is the half that makes the
        exit code usable as a gate."""
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        df.loc[df.index[0], "price"] = float(df.loc[df.index[0], "price"]) + 0.05
        df.to_csv(path, index=False)
        proc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts/check_liquidity_parity.py"),
             "--dir", str(synthetic_export), "--mqh", "does_not_exist.mqh"],
            capture_output=True, text=True)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "PARITY FAILED" in proc.stdout


class TestScoreComparison:
    """Scores are only compared when a .mqh is supplied — the synthetic fixture
    writes score 0.0, so an all-zero beta makes the expected score 0.0 too."""

    def _write_mqh(self, path, beta):
        n = len(beta)
        body = ",\n".join(f"{v: .10e}" for v in beta)
        ones = ",\n".join(" 1.0000000000e+00" for _ in range(n))
        zeros = ",\n".join(" 0.0000000000e+00" for _ in range(n))
        path.write_text(
            f'#define LQ_N_FEATURES {n}\n'
            f'#define LQ_MODEL_MODE "full"\n'
            f"const double LQ_BETA[LQ_N_FEATURES] = {{\n{body}\n}};\n"
            f"const double LQ_MEAN[LQ_N_FEATURES] = {{\n{zeros}\n}};\n"
            f"const double LQ_STD[LQ_N_FEATURES] = {{\n{ones}\n}};\n"
            f"const double LQ_PLATT_A =  1.0000000000e+00;\n"
            f"const double LQ_PLATT_B =  0.0000000000e+00;\n")

    def test_coefficients_parse_out_of_the_generated_header(self, tmp_path):
        mqh = tmp_path / "c.mqh"
        beta = np.linspace(-1.0, 1.0, len(ll.FEATURE_NAMES))
        self._write_mqh(mqh, beta)
        coef = clp.load_coefficients(mqh)
        assert coef["LQ_BETA"] == pytest.approx(beta)
        assert coef["LQ_MEAN"] == pytest.approx(np.zeros(len(beta)))
        assert coef["LQ_STD"] == pytest.approx(np.ones(len(beta)))
        assert coef["mode"] == "full"

    def test_zero_beta_matches_the_fixtures_zero_scores(self, synthetic_export):
        mqh = synthetic_export / "c.mqh"
        self._write_mqh(mqh, np.zeros(len(ll.FEATURE_NAMES)))
        # zero beta -> v = 0 for every level; the fixture wrote score 0.0, so scores
        # agree, but prob (1/(1+k)) will not match the fixture's 0.0 -> expect a
        # prob mismatch and NO score mismatch
        result = clp.check(synthetic_export, mqh=mqh)
        assert not result.ok
        assert all("score" not in m for m in result.mismatches)
        assert any("prob" in m for m in result.mismatches)

    def test_a_corrupted_score_column_is_caught(self, synthetic_export):
        mqh = synthetic_export / "c.mqh"
        self._write_mqh(mqh, np.zeros(len(ll.FEATURE_NAMES)))
        path = synthetic_export / "GC_LQ_levels.csv"
        df = pd.read_csv(path)
        df.loc[df.index[0], "score"] = 7.5
        df.to_csv(path, index=False)
        result = clp.check(synthetic_export, mqh=mqh)
        assert any("score" in m for m in result.mismatches)
