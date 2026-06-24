"""Tests for the symbol/strategy auto-on reconciler (the monday_drift-never-fired fix)."""

from src.strategies.symbol_reconciler import (
    required_symbols, reconcile_enabled_symbols, streaming_warning, streaming_reminder,
)


def base_config():
    return {
        'strategies': {
            'monday_drift':    {'enabled': True, 'allowed_symbols': ['GBPUSD', 'AUDUSD']},
            'london_breakout': {'enabled': True, 'allowed_symbols': ['USDJPY']},
            'index_overnight': {'enabled': True, 'allowed_symbols': ['US30']},
            'kalman_regime':   {'enabled': True},
        },
        'symbols': {
            'XAUUSD': {'enabled': True},
            'GBPUSD': {'enabled': False},
            'AUDUSD': {'enabled': False},
            'USDJPY': {'enabled': False},
            'US30':   {'enabled': False},
        },
    }


class TestRequiredSymbols:
    def test_only_enabled_strategies_counted(self):
        cfg = base_config()
        cfg['strategies']['monday_drift']['enabled'] = False
        req = required_symbols(cfg)
        assert 'monday_drift' not in req
        assert req['index_overnight'] == ['US30']

    def test_uses_configured_allowed_symbols(self):
        cfg = base_config()
        cfg['strategies']['index_overnight']['allowed_symbols'] = ['US30']
        assert required_symbols(cfg)['index_overnight'] == ['US30']


class TestReconcile:
    def test_forces_required_symbols_on(self):
        cfg = base_config()
        auto, missing = reconcile_enabled_symbols(cfg)
        assert set(auto) == {'GBPUSD', 'AUDUSD', 'USDJPY', 'US30'}
        assert missing == []
        for s in ('GBPUSD', 'AUDUSD', 'USDJPY', 'US30'):
            assert cfg['symbols'][s]['enabled'] is True

    def test_missing_block_reported_not_crashed(self):
        # A required symbol with no config block is reported, and the strategy's
        # other required symbols still get auto-enabled (monday_drift = 2 symbols).
        cfg = base_config()
        del cfg['symbols']['GBPUSD']
        auto, missing = reconcile_enabled_symbols(cfg)
        assert 'GBPUSD' in missing
        assert 'AUDUSD' in auto

    def test_disabled_strategy_symbols_untouched(self):
        cfg = base_config()
        cfg['strategies']['monday_drift']['enabled'] = False
        auto, _ = reconcile_enabled_symbols(cfg)
        assert 'GBPUSD' not in auto
        assert cfg['symbols']['GBPUSD']['enabled'] is False

    def test_idempotent(self):
        cfg = base_config()
        reconcile_enabled_symbols(cfg)
        auto2, _ = reconcile_enabled_symbols(cfg)
        assert auto2 == []   # already on second time


class TestWarnings:
    def test_streaming_warning_lists_non_chart_symbols(self):
        lines = streaming_warning(base_config(), chart_symbol='XAUUSD')
        blob = "\n".join(lines)
        assert 'GBPUSD' in blob and 'US30' in blob and 'USDJPY' in blob
        assert 'monday_drift' in blob and 'index_overnight' in blob

    def test_chart_symbol_excluded(self):
        # If the chart is US30.cash, US30 should not be flagged as needing WatchSymbols.
        cfg = base_config()
        cfg['strategies'] = {'index_overnight': {'enabled': True, 'allowed_symbols': ['US30']}}
        lines = streaming_warning(cfg, chart_symbol='US30.cash')
        assert lines == []

    def test_reminder_fires_today_monday(self):
        lines = streaming_reminder(base_config(), weekday=0, chart_symbol='XAUUSD')  # Monday
        blob = "\n".join(lines)
        assert 'monday_drift fires TODAY' in blob
        assert 'GBPUSD' in blob

    def test_reminder_warns_day_before_tuesday(self):
        lines = streaming_reminder(base_config(), weekday=0, chart_symbol='XAUUSD')  # Monday
        blob = "\n".join(lines)
        assert 'index_overnight fires TOMORROW' in blob   # Tue is tomorrow

    def test_reminder_quiet_on_unrelated_day(self):
        # Thursday: monday_drift (Mon) and index_overnight (Tue) neither fire nor are imminent.
        assert streaming_reminder(base_config(), weekday=3, chart_symbol='XAUUSD') == []
