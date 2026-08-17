#!/usr/bin/env python3
"""
Parity check: EA_KalmanRegime.mq5  vs  src/strategies/kalman_regime_strategy.py

The EA is a hand port. A hand port is a claim until it is measured. This script
measures it: it replays the bars the EA exported through the REAL Python
strategy and diffs, bar by bar, both the indicator values and the decisions.

WHAT IT COMPARES
    indicators   kalman, rv, rv_ma, regime, zscore, rsi, adx, atr,
                 htf_close, htf_ema
    decision     fired / side / strength
    reasoning    the first failing gate — the EA writes the same wording the
                 Python `_log_no_signal()` uses, so a mismatch here catches a
                 divergence in gate ORDER even when the outcome happens to agree

HOW TO PRODUCE THE INPUT
    1. Attach EA_KalmanRegime to the gold chart (or run it in Strategy Tester)
       with InpExportCSV = true.
    2. Let it run. It writes two files into the MT5 *Common* Files directory:
           kalman_parity.csv       one row per bar
           kalman_parity_meta.csv  every input, so this script rebuilds the
                                   exact same strategy config
    3. python scripts/check_kalman_ea_parity.py

TOLERANCES ARE NOT KNOBS
    If a comparison fails, fix the port. Loosening --rtol to make it pass turns
    a measurement back into a claim.

Exit code 0 = every checked value matched. Non-zero = at least one did not.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.types import Symbol                                   # noqa: E402
from src.data.indicators import Indicators                          # noqa: E402
from src.strategies.kalman_regime_strategy import KalmanRegimeStrategy  # noqa: E402

# Fields compared per bar. (csv column, label, how to get it from Python.)
INDICATOR_FIELDS = [
    "kalman", "rv", "rv_ma", "regime", "zscore",
    "rsi", "adx", "atr", "htf_close", "htf_ema",
]

# pandas resample aliases the strategy understands, keyed by seconds.
_RESAMPLE_BY_SECONDS = {
    900: "15min",
    1800: "30min",
    3600: "1h",
    7200: "2h",
    14400: "4h",
}


# ----------------------------------------------------------------------------
# input location
# ----------------------------------------------------------------------------
def default_common_dir() -> Path:
    """MT5 Common\\Files, via the same detection the file bridge already uses."""
    try:
        from mt5_bridge.mt5_file_client import MT5FileClient
        return MT5FileClient._get_default_mt5_path()
    except Exception:  # pragma: no cover - fallback for odd installs
        return Path.home()


# ----------------------------------------------------------------------------
# meta -> strategy config
# ----------------------------------------------------------------------------
def load_meta(path: Path) -> dict:
    raw = pd.read_csv(path)
    if not {"key", "value"}.issubset(raw.columns):
        raise SystemExit(f"{path.name}: expected 'key,value' columns, got {list(raw.columns)}")
    return {str(k): str(v) for k, v in zip(raw["key"], raw["value"])}


def _b(meta: dict, key: str, default: bool = False) -> bool:
    return str(meta.get(key, str(default))).strip().lower() == "true"


def _i(meta: dict, key: str, default: int) -> int:
    try:
        return int(float(meta[key]))
    except (KeyError, ValueError, TypeError):
        return default


def _f(meta: dict, key: str, default: float) -> float:
    try:
        return float(meta[key])
    except (KeyError, ValueError, TypeError):
        return default


def parse_sessions(spec: str) -> list:
    """'1-4,7-8,10-12' -> [[1, 4], [7, 8], [10, 12]]"""
    out = []
    for token in str(spec).split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            out.append([int(a), int(b)])
        else:
            out.append(int(token))
    return out


def build_config(meta: dict) -> dict:
    htf_seconds = _i(meta, "htf_resample_seconds", 3600)
    resample = _RESAMPLE_BY_SECONDS.get(htf_seconds)
    if resample is None:
        raise SystemExit(
            f"HTF period of {htf_seconds}s has no pandas alias the strategy accepts. "
            f"Supported: {sorted(_RESAMPLE_BY_SECONDS)}"
        )
    return {
        "enabled": True,
        "news_filter": False,
        "allowed_symbols": [meta.get("allowed_symbols", "XAUUSD")],
        "kalman_q": _f(meta, "kalman_q", 1e-5),
        "kalman_r": _f(meta, "kalman_r", 0.01),
        "rv_window": _i(meta, "rv_window", 20),
        "rv_ma_window": _i(meta, "rv_ma_window", 100),
        "zscore_window": _i(meta, "zscore_window", 20),
        "entry_threshold": _f(meta, "entry_threshold", 2.0),
        "trend_adx_min": _f(meta, "trend_adx_min", 17),
        "kalman_confirm_bars": _i(meta, "kalman_confirm_bars", 4),
        "atr_period": _i(meta, "atr_period", 14),
        "cooldown_bars": _i(meta, "cooldown_bars", 2),
        "long_only": _b(meta, "long_only"),
        "min_signal_strength": _f(meta, "min_signal_strength", 0.50),
        "min_signal_strength_sell": _f(meta, "min_signal_strength_sell", 0.85),
        "session_filter_enabled": _b(meta, "session_filter_enabled", True),
        "allowed_sessions": parse_sessions(meta.get("allowed_sessions", "")),
        "ema_confirm_enabled": _b(meta, "ema_confirm_enabled"),
        "ema_fast_period": _i(meta, "ema_fast_period", 9),
        "ema_slow_period": _i(meta, "ema_slow_period", 21),
        "macd_confirmation": _b(meta, "macd_confirmation"),
        "macd_fast": _i(meta, "macd_fast", 12),
        "macd_slow": _i(meta, "macd_slow", 26),
        "macd_signal": _i(meta, "macd_signal", 9),
        "kalman_accel_enabled": _b(meta, "kalman_accel_enabled"),
        "kalman_accel_bars": _i(meta, "kalman_accel_bars", 5),
        "trend_quality_gate_enabled": _b(meta, "trend_quality_gate_enabled"),
        "trend_quality_slope_bars": _i(meta, "trend_quality_slope_bars", 3),
        "trend_quality_std_window": _i(meta, "trend_quality_std_window", 20),
        "trend_quality_min_score": _f(meta, "trend_quality_min_score", 0.75),
        "range_rsi_buy": _f(meta, "range_rsi_buy", 42.0),
        "range_rsi_sell": _f(meta, "range_rsi_sell", 65.0),
        "stoch_confirm_enabled": _b(meta, "stoch_confirm_enabled"),
        "stoch_oversold": _f(meta, "stoch_oversold", 25),
        "stoch_overbought": _f(meta, "stoch_overbought", 75),
        "range_channel_enabled": _b(meta, "range_channel_enabled"),
        "range_channel_bars": _i(meta, "range_channel_bars", 20),
        "range_channel_atr_period": _i(meta, "range_channel_atr_period", 50),
        "range_channel_atr_mult": _f(meta, "range_channel_atr_mult", 1.5),
        "range_divergence_enabled": _b(meta, "range_divergence_enabled"),
        "range_divergence_lookback": _i(meta, "range_divergence_lookback", 60),
        "range_poc_enabled": _b(meta, "range_poc_enabled"),
        "range_poc_bars": _i(meta, "range_poc_bars", 50),
        "range_poc_bins": _i(meta, "range_poc_bins", 24),
        "range_poc_atr_mult": _f(meta, "range_poc_atr_mult", 1.0),
        "htf_sell_filter_enabled": _b(meta, "htf_sell_filter_enabled", True),
        "htf_buy_filter_enabled": _b(meta, "htf_buy_filter_enabled"),
        "htf_sell_resample_to": resample,
        "htf_ema_period": _i(meta, "htf_ema_period", 50),
    }


# ----------------------------------------------------------------------------
# bars
# ----------------------------------------------------------------------------
def load_bars(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time_utc", "open", "high", "low", "close", "tick_volume", "evaluated"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"{path.name}: missing columns {sorted(missing)}")

    df["time_utc"] = pd.to_datetime(df["time_utc"], format="%Y.%m.%d %H:%M:%S")
    df = df.sort_values("time_utc").reset_index(drop=True)
    df["volume"] = df["tick_volume"]
    return df


def strategy_window(df: pd.DataFrame, end_pos: int, size: int) -> pd.DataFrame:
    """The exact window the EA evaluated: `size` bars ending at end_pos inclusive."""
    start = end_pos - size + 1
    win = df.iloc[start:end_pos + 1][["time_utc", "open", "high", "low", "close", "volume"]].copy()
    # Live shape: DatetimeIndex *and* a `timestamp` column (the strategy reads
    # the column for the session hour and the index for the HTF resample).
    win["timestamp"] = win["time_utc"]
    win = win.set_index(pd.DatetimeIndex(win["time_utc"]))
    return win.drop(columns=["time_utc"])


# ----------------------------------------------------------------------------
# python-side recomputation of the exported indicator columns
# ----------------------------------------------------------------------------
def python_indicators(win: pd.DataFrame, cfg: dict) -> dict:
    close = win["close"]
    kalman = Indicators.kalman_filter(close, q=cfg["kalman_q"], r=cfg["kalman_r"])
    rv = Indicators.realized_vol(close, window=cfg["rv_window"])
    rv_ma = rv.rolling(cfg["rv_ma_window"]).mean()
    regime = Indicators.rv_regime(close, rv_window=cfg["rv_window"], rv_ma_window=cfg["rv_ma_window"])
    z = Indicators.ou_zscore(close, kalman, window=cfg["zscore_window"])
    rsi = Indicators.rsi(win, period=14)
    adx = Indicators.adx(win, period=14)
    atr = Indicators.atr(win, period=cfg["atr_period"])

    htf_close = float("nan")
    htf_ema = float("nan")
    if cfg["htf_sell_filter_enabled"] or cfg["htf_buy_filter_enabled"]:
        htf = win[["open", "high", "low", "close", "volume"]].resample(
            cfg["htf_sell_resample_to"]
        ).agg({"open": "first", "high": "max", "low": "min",
               "close": "last", "volume": "sum"}).dropna()
        if len(htf) > 0:
            htf_close = float(htf["close"].iloc[-1])
            htf_ema = float(
                htf["close"].ewm(span=cfg["htf_ema_period"], adjust=False).mean().iloc[-1]
            )

    return {
        "kalman": float(kalman.iloc[-1]),
        "rv": float(rv.iloc[-1]),
        "rv_ma": float(rv_ma.iloc[-1]),
        "regime": float(regime.iloc[-1]),
        "zscore": float(z.iloc[-1]),
        "rsi": float(rsi.iloc[-1]),
        "adx": float(adx.iloc[-1]),
        "atr": float(atr.iloc[-1]),
        "htf_close": htf_close,
        "htf_ema": htf_ema,
    }


# ----------------------------------------------------------------------------
# comparison
# ----------------------------------------------------------------------------
def close_enough(a: float, b: float, rtol: float, atol: float) -> bool:
    a_nan = a is None or (isinstance(a, float) and math.isnan(a))
    b_nan = b is None or (isinstance(b, float) and math.isnan(b))
    if a_nan and b_nan:
        return True
    if a_nan or b_nan:
        return False
    return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)


def csv_float(value) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, str) and value.strip() == "":
        return float("nan")
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=None,
                    help="directory holding the exported CSVs (default: MT5 Common\\Files)")
    ap.add_argument("--csv", default="kalman_parity.csv", help="bar/value export filename")
    ap.add_argument("--meta", default="kalman_parity_meta.csv", help="meta export filename")
    ap.add_argument("--rows", type=int, default=0,
                    help="check only the last N evaluated bars (0 = all). "
                         "Note: the cooldown is sequential state, so truncating "
                         "can produce spurious decision mismatches near the start.")
    ap.add_argument("--rtol", type=float, default=1e-6, help="relative tolerance")
    ap.add_argument("--atol", type=float, default=1e-12, help="absolute tolerance")
    ap.add_argument("--show", type=int, default=10, help="how many mismatches to print")
    args = ap.parse_args()

    logging.disable(logging.CRITICAL)  # the strategy logs every rejection; not useful here

    base = args.dir or default_common_dir()
    csv_path = base / args.csv
    meta_path = base / args.meta
    for p in (csv_path, meta_path):
        if not p.exists():
            print(f"ERROR: {p} not found. Run the EA with InpExportCSV=true first.")
            return 2

    meta = load_meta(meta_path)
    cfg = build_config(meta)
    df = load_bars(csv_path)

    window_size = _i(meta, "warmup_bars", 1500)
    ticker = meta.get("symbol", "XAUUSD")

    eval_pos = [i for i, flag in enumerate(df["evaluated"]) if int(flag) == 1]
    if not eval_pos:
        print("ERROR: no evaluated bars in the export — the EA wrote history only.")
        return 2

    # Every evaluated bar needs `window_size` bars of history behind it.
    usable = [i for i in eval_pos if i - window_size + 1 >= 0]
    dropped = len(eval_pos) - len(usable)
    if args.rows > 0:
        usable = usable[-args.rows:]
    if not usable:
        print(f"ERROR: no evaluated bar has {window_size} bars of history in the export.")
        return 2

    symbol = Symbol(ticker=ticker)
    strat = KalmanRegimeStrategy(symbol, cfg)

    # Capture the gate the Python strategy stopped at, so reasoning can be diffed.
    captured = {"reason": ""}
    original_log = strat._log_no_signal

    def capture(reason: str) -> None:
        captured["reason"] = reason
        original_log(reason)

    strat._log_no_signal = capture  # type: ignore[method-assign]

    print(f"EA parity check — {ticker}")
    print(f"  export      : {csv_path}")
    print(f"  bars        : {len(df)} rows, {len(eval_pos)} evaluated")
    print(f"  window      : {window_size} bars"
          + (f" ({dropped} evaluated bars dropped: not enough history)" if dropped else ""))
    print(f"  checking    : {len(usable)} bars")
    print(f"  tolerance   : rtol={args.rtol} atol={args.atol}")
    print()

    mismatches: list[str] = []
    counts = {name: 0 for name in INDICATOR_FIELDS}
    counts["decision"] = 0
    counts["reason"] = 0
    fired_ea = 0
    fired_py = 0

    for n, pos in enumerate(usable, start=1):
        row = df.iloc[pos]
        win = strategy_window(df, pos, window_size)

        py = python_indicators(win, cfg)
        for name in INDICATOR_FIELDS:
            ea_val = csv_float(row.get(name))
            if not close_enough(ea_val, py[name], args.rtol, args.atol):
                counts[name] += 1
                mismatches.append(
                    f"{row['time_utc']}  {name:<10} EA={ea_val!r:>22}  PY={py[name]!r:>22}"
                )

        captured["reason"] = ""
        signal = strat.on_bar(win)

        ea_fired = int(row.get("fired", 0)) == 1
        py_fired = signal is not None
        fired_ea += int(ea_fired)
        fired_py += int(py_fired)

        ea_side = str(row.get("side", "") or "").strip()
        py_side = signal.side.value.upper() if py_fired else ""
        ea_strength = csv_float(row.get("strength"))
        py_strength = float(signal.strength) if py_fired else float("nan")

        if ea_fired != py_fired or (py_fired and ea_side != py_side) or \
           (py_fired and not close_enough(ea_strength, py_strength, args.rtol, args.atol)):
            counts["decision"] += 1
            mismatches.append(
                f"{row['time_utc']}  decision   "
                f"EA={'FIRE ' + ea_side if ea_fired else 'none'} str={ea_strength!r}  "
                f"PY={'FIRE ' + py_side if py_fired else 'none'} str={py_strength!r}"
            )

        if not ea_fired and not py_fired:
            ea_reason = str(row.get("reject", "") or "").strip()
            py_reason = captured["reason"].strip()
            if ea_reason and py_reason and ea_reason != py_reason:
                counts["reason"] += 1
                mismatches.append(
                    f"{row['time_utc']}  reason     EA={ea_reason!r}\n"
                    f"{' ' * len(str(row['time_utc']))}  reason     PY={py_reason!r}"
                )

        if n % 250 == 0:
            print(f"  ... {n}/{len(usable)} bars, {len(mismatches)} mismatches so far")

    print()
    print("RESULT")
    print(f"  entries: EA fired {fired_ea}, Python fired {fired_py}")
    for name, c in counts.items():
        status = "ok" if c == 0 else f"MISMATCH x{c}"
        print(f"  {name:<12} {status}")

    if mismatches:
        print()
        print(f"First {min(args.show, len(mismatches))} of {len(mismatches)} mismatches:")
        for line in mismatches[:args.show]:
            print("   " + line)
        print()
        print("FAIL — the EA does not reproduce the Python strategy. Fix the port;")
        print("       do not widen --rtol to make this pass.")
        return 1

    print()
    print("PASS — every checked indicator and every decision matched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
