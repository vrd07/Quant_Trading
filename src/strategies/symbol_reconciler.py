"""
Symbol/strategy auto-on reconciliation + EA-streaming reminders.

The bug this fixes (found 2026-06-24): a strategy's ``enabled: true`` is INERT
unless its required symbol is ALSO ``enabled: true`` in ``symbols:``. monday_drift
sat enabled on the $25k config for days but never fired a single trade because
GBPUSD/AUDUSD were not enabled there — the StrategyManager only instantiates a
strategy on symbols that exist in the active set, and monday_drift's in-code gate
rejects every other symbol. london_breakout had the same latent break (USDJPY off
everywhere but the $5k config).

These are pure functions — no I/O. main.setup() calls reconcile_enabled_symbols()
to force the symbols on, and the main loop calls streaming_reminder() once per day
to warn which non-chart symbols the MT5 EA must carry in its WatchSymbols input.
"""
from typing import Any, Dict, List, Tuple

# Symbol-gated strategies → the symbols they need streaming. Defaults mirror each
# strategy's in-code allowed_symbols; the live value is read from config when set.
_STRATEGY_SYMBOLS: Dict[str, List[str]] = {
    'monday_drift':    ['GBPUSD', 'AUDUSD'],
    'london_breakout': ['USDJPY'],
    'index_overnight': ['US30'],   # NAS100 dropped — broker offers no NASDAQ index CFD
    'wednesday_drift': ['AUDJPY'],
    'squeeze_breakout': ['XAUUSD'],
    'stoch_pullback':  ['XAUUSD'],
    'kalman_regime':   ['XAUUSD'],
    'bos_structure':   ['XAUUSD'],
    'ema200_nasdaq':   ['NAS100'],  # broker's NASDAQ-100 ticker is user-set via start script
}

# Day-gated strategies → weekday they fire (Mon=0 .. Sun=6), for reminders.
# Strategies absent here fire on multiple/all days (no day-specific nag).
_FIRE_WEEKDAY: Dict[str, int] = {
    'monday_drift':    0,   # Monday
    'index_overnight': 1,   # Tuesday
    'wednesday_drift': 1,   # enters Tuesday (for the Wed move) — needs AUDJPY streaming Tue/Wed
}

_WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def required_symbols(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """{strategy: [SYMBOLS]} for every ENABLED symbol-gated strategy, using its
    configured allowed_symbols (falling back to the in-code defaults)."""
    strat_cfg = config.get('strategies', {}) or {}
    out: Dict[str, List[str]] = {}
    for name, defaults in _STRATEGY_SYMBOLS.items():
        sc = strat_cfg.get(name, {}) or {}
        if not sc.get('enabled', False):
            continue
        out[name] = [s.upper() for s in (sc.get('allowed_symbols') or defaults)]
    return out


def reconcile_enabled_symbols(config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Force-enable (IN PLACE) every enabled strategy's required symbols.

    Returns (auto_enabled, missing): symbols flipped on, and symbols a strategy
    needs but which have NO ``symbols:`` block at all (can't trade — warn loudly).
    """
    symbols_cfg = config.setdefault('symbols', {})
    needed = sorted({s for syms in required_symbols(config).values() for s in syms})
    auto_enabled, missing = [], []
    for sym in needed:
        block = symbols_cfg.get(sym)
        if block is None:
            missing.append(sym)
        elif not block.get('enabled', False):
            block['enabled'] = True
            auto_enabled.append(sym)
    return auto_enabled, missing


def _non_chart_symbols(config: Dict[str, Any], chart_symbol: str) -> List[str]:
    """Required symbols other than the EA's chart symbol — these are the ones
    that only stream if listed in the EA WatchSymbols input."""
    chart = (chart_symbol or '').upper()
    syms = {s for v in required_symbols(config).values() for s in v}
    if not chart:
        return sorted(syms)   # chart unknown → warn about ALL required symbols
    # prefix match so a base ticker (US30) counts the broker-suffixed chart (US30.cash)
    return sorted(s for s in syms if not (chart.startswith(s) or s.startswith(chart)))


def streaming_warning(config: Dict[str, Any], chart_symbol: str = '') -> List[str]:
    """One-time startup warning lines: which non-chart symbols the EA must stream
    and which day-gated strategy each unlocks. Empty if nothing extra is needed."""
    extra = _non_chart_symbols(config, chart_symbol)
    if not extra:
        return []
    req = required_symbols(config)
    lines = [
        "⚠️  EA STREAMING REQUIRED — these strategies are ON but their symbols only",
        "    receive bars if listed in the EA 'WatchSymbols' input (comma-separated,",
        "    use the broker suffix, e.g. \"GBPUSDs,AUDUSDs,US30.cash,NAS100.cash\"):",
    ]
    for strat, syms in sorted(req.items()):
        extra_for = [s for s in syms if s in extra]
        if not extra_for:
            continue
        day = _FIRE_WEEKDAY.get(strat)
        when = f"fires {_WD[day]}s" if day is not None else "fires daily"
        lines.append(f"      • {strat} ({when}): keep {', '.join(extra_for)} streaming")
    return lines


def streaming_reminder(config: Dict[str, Any], weekday: int,
                       chart_symbol: str = '') -> List[str]:
    """Day-aware reminder for the day-gated strategies: warn on the fire day AND
    the day before (so the user can switch the EA WatchSymbols in time). Returns
    [] on days with nothing scheduled."""
    extra = set(_non_chart_symbols(config, chart_symbol))
    req = required_symbols(config)
    lines: List[str] = []
    for strat, fire_wd in _FIRE_WEEKDAY.items():
        if strat not in req:
            continue
        syms = [s for s in req[strat] if s in extra]
        if not syms:
            continue
        if weekday == fire_wd:
            lines.append(
                f"⚠️  {strat} fires TODAY ({_WD[fire_wd]}) — confirm {', '.join(syms)} "
                f"are in the EA WatchSymbols and streaming NOW.")
        elif weekday == (fire_wd - 1) % 7:
            lines.append(
                f"⏰ {strat} fires TOMORROW ({_WD[fire_wd]}) — make sure {', '.join(syms)} "
                f"will be in the EA WatchSymbols.")
    return lines
