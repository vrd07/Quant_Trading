"""WaveletCycleStrategy: the three-confirmation entry contract (spec §5.3)."""
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.core.constants import MarketRegime, OrderSide
from src.core.types import Symbol
from src.cycles.correlation import MTFAlignment
from src.cycles.cycle_tracker import CycleState
from src.cycles.goertzel import CyclePhase
from src.cycles.regime import CycleRegime, RegimeState
from src.strategies.wavelet_cycle_strategy import WaveletCycleStrategy


CONFIG = {
    "enabled": True,
    "timeframe": "15m",
    "asset_preset": "gold",
    "allowed_symbols": ["XAUUSD"],
    "atr_period": 14,
    "entry_dev_atr": 0.5,
    "rr": 2.0,
    "min_strength": 0.3,
}


def symbol(ticker="XAUUSD"):
    return Symbol(ticker=ticker, pip_value=Decimal("0.01"),
                  min_lot=Decimal("0.01"), max_lot=Decimal("100.0"))


def bars(n=400, base=2000.0, seed=0):
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.standard_normal(n) * 0.5)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": close, "high": close + 1.0, "low": close - 1.0,
                         "close": close, "volume": np.ones(n)}, index=idx)


def cycle(position, deviation, period=32.0, tradeable=True, trend=2000.0):
    return CycleState(period=period, prominence=3.0, power_fraction=0.4, stable=True,
                      tradeable=tradeable,
                      phase=CyclePhase(amplitude=2.0, phase=0.0, position=position),
                      trend=trend, deviation=deviation, raw_deviation=deviation,
                      group_delay=11.0, bandpass=0.5, reason="ok")


def consistent_cycle(position, deviation, data, **kw):
    """Cycle state obeying the engine's invariant deviation = price - trend.

    The mean-revert target IS the trend line, so a state that sets trend and
    deviation independently can put the target on the wrong side of entry --
    which the strategy correctly refuses as self-contradictory input.
    """
    trend = float(data["close"].iloc[-1]) - deviation
    return cycle(position, deviation, trend=trend, **kw)


def regime(kind):
    mult = 0.2 if kind is CycleRegime.UNCERTAIN else 1.0
    return RegimeState(kind, adx=30.0, hurst=0.6, vol_percentile=0.5,
                       vol_of_vol=0.1, size_multiplier=mult)


NEUTRAL_MTF = MTFAlignment(bias=0, size_multiplier=1.0, allow_long=True, allow_short=True)


def run(strategy, cycle_state, regime_state, data=None, mtf=NEUTRAL_MTF):
    """Drive on_bar with the engine stubbed out.

    The HTF anchors are resampled from `data`, so on a random-walk fixture their
    bias is arbitrary and would make every entry test flaky. Stub it to neutral
    here; TestHigherTimeframeGate exercises the real thing on shaped data.
    """
    data = bars() if data is None else data
    with patch.object(strategy._tracker, "update", return_value=cycle_state), \
         patch.object(strategy._regime, "classify", return_value=regime_state), \
         patch("src.strategies.wavelet_cycle_strategy.mtf_alignment", return_value=mtf):
        return strategy.on_bar(data)


@pytest.fixture
def strat():
    return WaveletCycleStrategy(symbol(), dict(CONFIG))


class TestSymbolGate:
    def test_rejects_symbol_outside_allowlist(self):
        s = WaveletCycleStrategy(symbol("EURUSD"), dict(CONFIG))
        assert run(s, cycle(0.60, +5.0), regime(CycleRegime.TREND)) is None

    def test_accepts_broker_suffixed_ticker(self):
        """Broker gold is suffixed (XAUUSD.p); the gate must prefix-match."""
        s = WaveletCycleStrategy(symbol("XAUUSD.p"), dict(CONFIG))
        assert run(s, cycle(0.60, +5.0), regime(CycleRegime.TREND)) is not None


class TestThreeConfirmations:
    def test_trend_long_fires_on_all_three(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert sig is not None and sig.side is OrderSide.BUY

    def test_trend_short_fires_on_all_three(self, strat):
        sig = run(strat, cycle(0.10, -5.0), regime(CycleRegime.TREND))
        assert sig is not None and sig.side is OrderSide.SELL

    def test_no_signal_when_structural_confirmation_missing(self, strat):
        assert run(strat, cycle(0.60, -5.0), regime(CycleRegime.TREND)) is None

    def test_no_signal_when_cyclic_confirmation_missing(self, strat):
        assert run(strat, cycle(0.30, +5.0), regime(CycleRegime.TREND)) is None

    def test_no_signal_when_regime_forbids(self, strat):
        assert run(strat, cycle(0.60, +5.0), regime(CycleRegime.MEAN_REVERT)) is None

    def test_uncertain_regime_is_flat(self, strat):
        assert run(strat, cycle(0.60, +5.0), regime(CycleRegime.UNCERTAIN)) is None

    def test_untradeable_cycle_blocks_everything(self, strat):
        assert run(strat, cycle(0.60, +5.0, tradeable=False), regime(CycleRegime.TREND)) is None


class TestMeanRevertEntries:
    def test_long_at_trough_when_oversold(self, strat):
        data = bars()
        sig = run(strat, consistent_cycle(0.50, -40.0, data),
                  regime(CycleRegime.MEAN_REVERT), data=data)
        assert sig is not None and sig.side is OrderSide.BUY

    def test_short_at_peak_when_overbought(self, strat):
        data = bars()
        sig = run(strat, consistent_cycle(0.98, +40.0, data),
                  regime(CycleRegime.MEAN_REVERT), data=data)
        assert sig is not None and sig.side is OrderSide.SELL

    def test_small_deviation_does_not_qualify(self, strat):
        data = bars()
        assert run(strat, consistent_cycle(0.50, -0.01, data),
                   regime(CycleRegime.MEAN_REVERT), data=data) is None

    def test_mean_revert_target_is_the_trend_line(self, strat):
        data = bars()
        state = consistent_cycle(0.50, -40.0, data)
        sig = run(strat, state, regime(CycleRegime.MEAN_REVERT), data=data)
        assert float(sig.take_profit) == pytest.approx(state.trend, abs=1e-6)

    def test_target_behind_entry_is_refused(self, strat):
        """deviation says price is far above the trend but the trend sits above
        entry: the two disagree, so the trade is dropped rather than inverted."""
        data = bars()
        contradictory = cycle(0.98, +40.0, trend=float(data["close"].iloc[-1]) + 10.0)
        assert run(strat, contradictory, regime(CycleRegime.MEAN_REVERT), data=data) is None


class TestHigherTimeframeGate:
    """Spec §6.4 -- the anchor bans counter-trend entries, conflict halves size."""

    def test_bearish_anchor_blocks_a_long(self, strat):
        bearish = MTFAlignment(bias=-1, size_multiplier=1.0,
                               allow_long=False, allow_short=True)
        assert run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), mtf=bearish) is None

    def test_bullish_anchor_blocks_a_short(self, strat):
        bullish = MTFAlignment(bias=1, size_multiplier=1.0,
                               allow_long=True, allow_short=False)
        assert run(strat, cycle(0.10, -5.0), regime(CycleRegime.TREND), mtf=bullish) is None

    def test_conflicting_anchors_halve_the_size_multiplier(self, strat):
        conflict = MTFAlignment(bias=0, size_multiplier=0.5,
                                allow_long=True, allow_short=True)
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), mtf=conflict)
        assert sig.metadata["size_multiplier"] == pytest.approx(0.5)

    def test_bias_recorded_in_metadata(self, strat):
        bullish = MTFAlignment(bias=1, size_multiplier=1.0,
                               allow_long=True, allow_short=True)
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), mtf=bullish)
        assert sig.metadata["mtf_bias"] == 1

    def test_real_anchors_are_resampled_without_patching(self):
        """Proves the anchor path actually runs -- a stub-only test would pass
        even if _anchors() were never called."""
        s = WaveletCycleStrategy(symbol(), dict(CONFIG))
        up = bars(n=800)
        up["close"] = 2000.0 + np.arange(800) * 0.5
        for col in ("open", "high", "low"):
            up[col] = up["close"]
        a1, a2 = s._anchors(up)
        assert a1 is not None and len(a1) == 200   # 800 x 15m -> 1h
        assert a2 is not None and len(a2) == 50    # 800 x 15m -> 4h


class TestMacroCorrelation:
    """Spec §6.3 -- halve size when gold is dollar-driven rather than cyclical."""

    def test_no_proxy_is_pass_through(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert sig.metadata["size_multiplier"] == pytest.approx(1.0)
        assert np.isnan(sig.metadata["macro_correlation"])

    def test_highly_correlated_proxy_halves_size(self, strat):
        data = bars()
        strat.set_proxy_series(data["close"] * 3.0)  # perfectly correlated
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), data=data)
        assert sig.metadata["size_multiplier"] == pytest.approx(0.5)
        assert abs(sig.metadata["macro_correlation"]) > 0.9

    def test_gold_gates_on_absolute_correlation(self, strat):
        """An inverse dollar relationship is just as macro-driven as a direct one."""
        data = bars()
        strat.set_proxy_series(-data["close"])
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), data=data)
        assert sig.metadata["size_multiplier"] == pytest.approx(0.5)

    def test_btc_preset_uses_signed_correlation(self):
        s = WaveletCycleStrategy(symbol("BTCUSD"),
                                 {**CONFIG, "asset_preset": "btc",
                                  "allowed_symbols": ["BTCUSD"], "timeframe": "1h"})
        assert s._corr.use_abs is False
        assert s._corr.threshold == pytest.approx(0.8)


class TestStopsAndMetadata:
    def test_stop_is_below_entry_for_a_long(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert float(sig.stop_loss) < float(sig.entry_price)

    def test_trend_target_respects_rr(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        risk = float(sig.entry_price) - float(sig.stop_loss)
        reward = float(sig.take_profit) - float(sig.entry_price)
        assert reward == pytest.approx(2.0 * risk, rel=1e-6)

    def test_preserves_structural_sl(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert sig.metadata["preserve_structural_sl"] is True

    def test_publishes_atr_for_downstream_overlays(self, strat):
        """Two prior features silently no-op'd because a strategy never published
        ATR (project_plumbing_null_trap). Do not repeat it."""
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert sig.metadata["atr"] > 0

    def test_time_stop_is_one_and_a_half_cycles(self, strat):
        sig = run(strat, cycle(0.60, +5.0, period=32.0), regime(CycleRegime.TREND))
        assert sig.metadata["time_stop_minutes"] == 720  # ceil(1.5 * 32 * 15)

    def test_regime_recorded_on_signal(self, strat):
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND))
        assert sig.regime is MarketRegime.TREND
        assert sig.metadata["regime"] == "TREND"


class TestGuards:
    def test_disabled_strategy_emits_nothing(self, strat):
        strat.disable()
        assert run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND)) is None

    def test_short_history_emits_nothing(self, strat):
        assert strat.on_bar(bars(n=20)) is None

    def test_signal_timestamp_comes_from_the_bar_not_the_clock(self, strat):
        """Signal.timestamp defaults to now(); a replay stamped 'now' makes every
        look-ahead check meaningless (project_squeeze_volume_filter_smelltest)."""
        data = bars()
        sig = run(strat, cycle(0.60, +5.0), regime(CycleRegime.TREND), data=data)
        assert sig.timestamp == data.index[-1].to_pydatetime()


class TestRegistration:
    def test_present_in_the_strategy_registry(self):
        from src.strategies.strategy_manager import StrategyManager
        assert StrategyManager.STRATEGY_REGISTRY["wavelet_cycle"] is WaveletCycleStrategy

    def test_weights_table_has_all_three_regimes(self):
        from scripts.regime_classifier import STRATEGY_WEIGHTS
        for regime in ("TREND", "RANGE", "VOLATILE"):
            assert "wavelet_cycle" in STRATEGY_WEIGHTS[regime]

    def test_ships_disabled_in_every_live_config(self):
        """Unvalidated strategy: registered for consistency, off for safety."""
        import glob
        import yaml
        paths = glob.glob("config/config_live*.yaml")
        assert len(paths) >= 7
        for path in paths:
            with open(path) as fh:
                cfg = yaml.safe_load(fh)
            block = cfg["strategies"]["wavelet_cycle"]
            assert block["enabled"] is False, f"{path} has wavelet_cycle enabled"
