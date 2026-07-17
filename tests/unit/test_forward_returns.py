"""Unit tests for src/microstructure/forward_returns.py — synthetic, no I/O."""
import numpy as np
import pandas as pd
import pytest

from src.microstructure import forward_returns as fr


def mids(prices, start="2026-07-16 09:00", freq="30s"):
    idx = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.Series([float(p) for p in prices], index=idx)


CFG = fr.LabelConfig(sl_atr=1.0, tp_atr=2.0, max_hold_bars=16, cost_pts=0.0,
                     timeframe="15min")


class TestEventDirection:
    def test_long_short_none(self):
        assert fr.event_direction("bullish_divergence") == "long"
        assert fr.event_direction("sweep_low") == "long"
        assert fr.event_direction("absorption_of_selling") == "long"
        assert fr.event_direction("imbalance_buy") == "long"
        assert fr.event_direction("bearish_divergence") == "short"
        assert fr.event_direction("sweep_high") == "short"
        assert fr.event_direction("absorption_of_buying") == "short"
        assert fr.event_direction("imbalance_sell") == "short"
        assert fr.event_direction("liquidity_withdrawal") is None


class TestAtr:
    def test_atr_constant_range(self):
        idx = pd.date_range("2026-07-16 09:00", periods=20, freq="15min", tz="UTC")
        bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 100.0,
                             "close": 100.5}, index=idx)
        a = fr.atr(bars, period=14)
        assert a.iloc[-1] == pytest.approx(1.0)   # every TR == 1.0


class TestLabelEvent:
    def test_target_before_stop(self):
        # long entry 100, atr 1 -> stop 99, target 102; price rises to 102
        out = fr.label_event(mids([100, 100.5, 102.0]), "long", 1.0, CFG)
        assert out["outcome"] == "target"
        assert out["R_net"] == pytest.approx(2.0)   # tp_atr/sl_atr, zero cost
        assert out["mfe"] == pytest.approx(2.0)

    def test_gap_to_stop(self):
        out = fr.label_event(mids([100, 99.5, 99.0]), "long", 1.0, CFG)
        assert out["outcome"] == "stop"
        assert out["R_net"] == pytest.approx(-1.0)

    def test_intrabar_stop_then_target_counts_as_stop(self):
        # dips to 99 (stop) FIRST, then to 102 (target) -> must be STOP
        out = fr.label_event(mids([100, 99.0, 102.0]), "long", 1.0, CFG)
        assert out["outcome"] == "stop"

    def test_time_exit_signed_r(self):
        # never hits 99 or 102 within max_hold; ends at 100.5 -> +0.5R
        cfg = fr.LabelConfig(sl_atr=1.0, tp_atr=2.0, max_hold_bars=1,
                             cost_pts=0.0, timeframe="15min")
        out = fr.label_event(mids([100, 100.2, 100.5], freq="20s"), "long", 1.0, cfg)
        assert out["outcome"] == "time"
        assert out["R_net"] == pytest.approx(0.5)

    def test_short_direction_target(self):
        # short entry 100, target 98, stop 101; price falls to 98
        out = fr.label_event(mids([100, 99.0, 98.0]), "short", 1.0, CFG)
        assert out["outcome"] == "target"
        assert out["R_net"] == pytest.approx(2.0)

    def test_costs_strictly_lower_r(self):
        free = fr.label_event(mids([100, 102.0]), "long", 1.0, CFG)
        costed = fr.label_event(mids([100, 102.0]), "long", 1.0,
                                fr.LabelConfig(cost_pts=0.5))
        assert costed["R_net"] < free["R_net"]

    def test_degenerate_atr_returns_none(self):
        assert fr.label_event(mids([100, 101]), "long", 0.0, CFG) is None
        assert fr.label_event(pd.Series(dtype=float), "long", 1.0, CFG) is None


class TestSummarize:
    def _events(self, kind, direction, r_list, start="2026-07-01"):
        idx = pd.date_range(start, periods=len(r_list), freq="1h", tz="UTC")
        return [{"ts": t, "kind": kind, "direction": direction,
                 "R_net": float(r), "bars_held": 3}
                for t, r in zip(idx, r_list)]

    def test_expectancy_pf_winrate(self):
        evs = self._events("imbalance_buy", "long", [2.0, -1.0, 2.0, -1.0])
        cell = fr.summarize(evs)["cells"][0]
        assert cell["n"] == 4
        assert cell["expectancy"] == pytest.approx(0.5)
        assert cell["win_rate"] == pytest.approx(0.5)
        assert cell["profit_factor"] == pytest.approx(2.0)  # 4 / 2

    def test_thin_when_small_n(self):
        cell = fr.summarize(self._events("sweep_low", "long", [1.0, 1.0]))["cells"][0]
        assert cell["verdict"] == "thin"

    def test_candidate_needs_both_halves_positive_and_significant(self):
        # 100 strongly-positive events, low variance; split 0.6 -> both
        # halves >=30, both positive, t>2 -> CANDIDATE
        evs = self._events("sweep_low", "long", [1.0, 0.9] * 50)
        cell = fr.summarize(evs, split_frac=0.6)["cells"][0]
        assert cell["n_is"] >= 30 and cell["n_oos"] >= 30
        assert cell["verdict"] == "CANDIDATE"

    def test_one_sided_flagged(self):
        # IS all +1, OOS all -1 (>=30 each) -> one-sided, not candidate
        pos = self._events("imbalance_buy", "long", [1.0] * 45, start="2026-07-01")
        neg = self._events("imbalance_buy", "long", [-1.0] * 45, start="2026-08-01")
        cell = fr.summarize(pos + neg, split_frac=0.5)["cells"][0]
        assert cell["verdict"] == "one-sided"

    def test_dead_when_both_negative(self):
        # 100 negative events; split 0.6 -> both halves >=30 and negative -> dead
        evs = self._events("sweep_high", "short", [-1.0, -0.9] * 50)
        cell = fr.summarize(evs, split_frac=0.6)["cells"][0]
        assert cell["n_is"] >= 30 and cell["n_oos"] >= 30
        assert cell["verdict"] == "dead"

    def test_cells_sorted_by_total_r_desc(self):
        good = self._events("sweep_low", "long", [1.0] * 10, start="2026-07-01")
        bad = self._events("sweep_high", "short", [-1.0] * 10, start="2026-07-01")
        cells = fr.summarize(good + bad)["cells"]
        assert cells[0]["total_R"] >= cells[1]["total_R"]
