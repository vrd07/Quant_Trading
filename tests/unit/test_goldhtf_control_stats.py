# tests/unit/test_goldhtf_control_stats.py
"""Control statistics, and proof the fast replay is behaviour-preserving."""
import numpy as np
import pandas as pd
import pytest

import scripts.simulate_goldhtf_ea as sim
from scripts.simulate_goldhtf_ea import control_stats


def test_z_is_zero_when_real_equals_the_null_mean():
    null = np.array([0.9, 1.0, 1.1])
    out = control_stats(1.0, null)
    assert out["z"] == pytest.approx(0.0, abs=1e-9)


def test_z_counts_standard_deviations_above_the_null_mean():
    null = np.array([1.0, 2.0, 3.0])          # mean 2.0, sd(ddof=1) 1.0
    assert control_stats(4.0, null)["z"] == pytest.approx(2.0, abs=1e-9)


def test_percentile_is_the_share_of_draws_beaten():
    null = np.array([1.0, 1.1, 1.2, 1.3])
    assert control_stats(1.25, null)["percentile"] == pytest.approx(75.0, abs=1e-9)


def test_degenerate_null_gives_nan_z_not_a_zero_division():
    out = control_stats(1.2, np.array([1.0, 1.0, 1.0]))
    assert np.isnan(out["z"])


def _frame(n=400):
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(3)
    close = 2000 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({"open": close, "high": close + 1.0,
                         "low": close - 1.0, "close": close,
                         "volume": 1.0}, index=idx)


def _slow_replay(m5, sigs, balance0):
    """Reference implementation: the pre-optimisation loop, kept only as an oracle."""
    o = m5.open.to_numpy(float); hh = m5.high.to_numpy(float)
    ll = m5.low.to_numpy(float); cc = m5.close.to_numpy(float)
    ts = m5.index
    by_bar = {}
    for sg in sigs:
        by_bar.setdefault(sg["bar"], []).append(sg)
    lo = min(by_bar) if by_bar else 0
    trades, ev, pos, balance = [], [], None, balance0
    for j in range(lo, len(m5)):
        if pos is not None:
            res = sim.manage_position(pos, j, o, hh, ll, ev)
            if res is None:
                continue
            sign = 1.0 if pos["side"] == 1 else -1.0
            pnl = (res[0] - pos["entry"]) * pos["lot"] * sim.VALUE_PER_LOT * sign
            balance += pnl
            trades.append(dict(pnl=pnl,
                               r=pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0,
                               reason=res[1], entry_ts=pos["entry_ts"]))
            pos = None
        for sg in by_bar.get(j, []):
            side = sg["side"]
            entry = cc[j] + sim.COST if side == 1 else cc[j] - sim.COST
            dist = (entry - sg["stop"]) if side == 1 else (sg["stop"] - entry)
            if dist <= 0:
                continue
            lot = sim.calculate_lot(dist, balance, sg.get("lot_mult", 1.0))
            pos = sim.open_position(side, entry, sg["stop"], sg["rr"], j, ts[j], lot,
                                    sg.get("path", "CTL"), sg.get("regime", "NA"))
            break
    if pos is not None:
        fill = cc[-1] - sim.COST if pos["side"] == 1 else cc[-1] + sim.COST
        sign = 1.0 if pos["side"] == 1 else -1.0
        pnl = (fill - pos["entry"]) * pos["lot"] * sim.VALUE_PER_LOT * sign
        balance += pnl
        trades.append(dict(pnl=pnl,
                           r=pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0,
                           reason="open_at_window_end", entry_ts=pos["entry_ts"]))
    return pd.DataFrame(trades)


def test_fast_replay_matches_the_reference_exactly():
    sim.ARGS = type("A", (), {"balance": 1000.0})()
    m5 = _frame()
    cc = m5.close.to_numpy(float)
    sigs = [dict(bar=b, side=(1 if b % 2 == 0 else -1),
                 stop=float(cc[b] - 5.0 if b % 2 == 0 else cc[b] + 5.0), rr=2.0)
            for b in (10, 60, 61, 150, 300)]
    fast = sim.replay(m5, sigs, 1000.0)
    slow = _slow_replay(m5, sigs, 1000.0)
    pd.testing.assert_frame_equal(fast, slow)
