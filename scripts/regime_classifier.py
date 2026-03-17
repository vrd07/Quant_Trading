#!/usr/bin/env python3
"""
Nightly Regime Classifier — ML-powered market type prediction.

Runs at midnight, analyzes the last N days of XAUUSD data,
trains a RandomForestClassifier, and writes config_override.json
with strategy enable/disable recommendations for the next session.

Usage:
    python scripts/regime_classifier.py [--bars-file data/historical/XAUUSD_5m_real.csv]

Output:
    data/config_override.json  (read by main.py at startup)

Regime Labels:
    TREND    → directional day, big move relative to ATR
    RANGE    → sideways/choppy day, small net move
    VOLATILE → large ATR but no clear direction (news day)

Strategy Rules (written to config_override.json):
    TREND    → enable breakout + momentum + kalman_regime, disable mean_reversion + vwap
    RANGE    → enable mean_reversion + vwap, disable breakout + momentum
    VOLATILE → enable only kalman_regime (least exposed), disable all breakout/momentum
"""

import sys
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OVERRIDE_FILE = PROJECT_ROOT / "data" / "config_override.json"
DEFAULT_BARS = PROJECT_ROOT / "data" / "historical" / "XAUUSD_5m_real.csv"
LIVE_LOG_BARS = PROJECT_ROOT / "data" / "logs" / "candle_store_XAUUSD_5m.csv"


# ─── Feature Engineering ──────────────────────────────────────────────────────

def compute_daily_bars(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5m bars into daily OHLCV."""
    df = df_5m.copy()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    daily = df.groupby("date").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    return daily.sort_values("date").reset_index(drop=True)


def compute_features(daily: pd.DataFrame) -> pd.DataFrame:
    """
    Compute ML features from daily OHLCV bars.

    Features:
     - adx_14         : Average Directional Index (trend strength)
     - atr_pct        : ATR / close (normalized volatility)
     - bb_width_ratio : BB width / rolling mean (squeeze detection)
     - close_ema20_pct: Distance of close from EMA20 as %
     - momentum_1d    : 1-day return
     - momentum_5d    : 5-day return
     - range_atr_ratio: Day range (H-L) / ATR (how much of ATR was used)
     - vol_ratio      : Today's volume / 20-day avg volume
    """
    close = daily["close"]
    high = daily["high"]
    low = daily["low"]

    # ATR (14)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()

    # ADX (14) — simplified (EWM of DI difference)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_di = pd.Series(plus_dm).ewm(span=14, adjust=False).mean() / atr * 100
    minus_di = pd.Series(minus_dm).ewm(span=14, adjust=False).mean() / atr * 100
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.ewm(span=14, adjust=False).mean()

    # EMA20
    ema20 = close.ewm(span=20, adjust=False).mean()

    # Bollinger Band width
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_width = (2 * std20) / sma20
    bb_width_ratio = bb_width / bb_width.rolling(20, min_periods=5).mean()

    # Momentum
    momentum_1d = close.pct_change(1)
    momentum_5d = close.pct_change(5)

    # Volume ratio
    vol_ratio = daily["volume"] / daily["volume"].rolling(20).mean()

    feat = pd.DataFrame({
        "date": daily["date"],
        "adx_14": adx.values,
        "atr_pct": (atr / close).values,
        "bb_width_ratio": bb_width_ratio.values,
        "close_ema20_pct": ((close - ema20) / ema20).values,
        "momentum_1d": momentum_1d.values,
        "momentum_5d": momentum_5d.values,
        "range_atr_ratio": ((high - low) / atr).values,
        "vol_ratio": vol_ratio.values,
    })
    return feat


def compute_labels(daily: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
    """
    Auto-label each day based on NEXT DAY's price action.

    TREND    : |next_close - today_close| / today_ATR > 1.2
    VOLATILE : today's range/ATR > 2.0 but net move < 0.8×ATR
    RANGE    : everything else
    """
    close = daily["close"]
    high = daily["high"]
    low = daily["low"]

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()

    next_move = close.shift(-1) - close
    move_atr_ratio = (next_move.abs() / atr)
    range_atr = (high - low) / atr

    labels = pd.Series("RANGE", index=daily.index)
    labels[move_atr_ratio > 1.2] = "TREND"
    labels[(range_atr > 2.0) & (move_atr_ratio < 0.8)] = "VOLATILE"
    return labels


# ─── Core Classifier ──────────────────────────────────────────────────────────

STRATEGY_MAP = {
    "TREND": {
        "breakout":      True,
        "momentum":      True,
        "kalman_regime": True,
        "mean_reversion": False,
        "vwap":          False,
        "mini_medallion": True,
    },
    "RANGE": {
        "breakout":      False,
        "momentum":      False,
        "kalman_regime": True,   # still useful as regime watchdog
        "mean_reversion": False,  # NOTE: disabled for prop challenge — kept as False
        "vwap":          True,
        "mini_medallion": True,
    },
    "VOLATILE": {
        "breakout":      False,
        "momentum":      False,
        "kalman_regime": True,
        "mean_reversion": False,
        "vwap":          False,
        "mini_medallion": True,
    },
}

FEATURE_COLS = [
    "adx_14", "atr_pct", "bb_width_ratio",
    "close_ema20_pct", "momentum_1d", "momentum_5d",
    "range_atr_ratio", "vol_ratio",
]


def classify_rule_based(feat_row: dict) -> tuple[str, float]:
    """
    Fallback rule-based classifier (no sklearn needed, always works).
    Returns (regime, confidence).
    """
    adx = feat_row.get("adx_14", 20)
    atr_pct = feat_row.get("atr_pct", 0.01)
    bb_ratio = feat_row.get("bb_width_ratio", 1.0)
    range_atr = feat_row.get("range_atr_ratio", 1.0)

    if range_atr > 2.5 and adx < 20:
        return "VOLATILE", 0.60

    if adx > 28 and bb_ratio > 1.1:
        return "TREND", 0.70 + min(adx / 100, 0.20)

    if adx < 20 and bb_ratio < 0.9:
        return "RANGE", 0.65

    # Ambiguous
    if adx > 24:
        return "TREND", 0.55
    return "RANGE", 0.52


def classify_ml(feat_df: pd.DataFrame, labels: pd.Series, feat_row: dict) -> tuple[str, float, str]:
    """
    Train a RandomForestClassifier on historical data and predict tomorrow's regime.
    Returns (regime, confidence, classifier_name).
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import LabelEncoder
    except ImportError:
        return None, None, None

    X = feat_df[FEATURE_COLS].dropna()
    y = labels.loc[X.index].dropna()
    X = X.loc[y.index]

    if len(X) < 30:
        return None, None, None

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        min_samples_leaf=5,
        random_state=42,
        class_weight="balanced",
    )
    clf.fit(X, y_enc)

    # Build feature row as DataFrame
    row_df = pd.DataFrame([feat_row])[FEATURE_COLS]
    if row_df.isnull().any().any():
        return None, None, None

    proba = clf.predict_proba(row_df)[0]
    pred_idx = proba.argmax()
    confidence = float(proba[pred_idx])
    regime = le.inverse_transform([pred_idx])[0]

    return regime, confidence, f"RandomForest (n={len(X)} samples)"


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_classifier(bars_file: Path = None) -> dict:
    """
    Run the full classification pipeline.
    Returns the override dict (also written to disk).
    """
    now_utc = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"🤖  Nightly Regime Classifier — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── Load bars ────────────────────────────────────────────────
    sources = [
        bars_file,
        LIVE_LOG_BARS,
        DEFAULT_BARS,
    ]
    df_5m = None
    used_source = None
    for src in sources:
        if src and Path(src).exists():
            try:
                df_5m = pd.read_csv(src)
                # Normalize column names
                df_5m.columns = [c.lower() for c in df_5m.columns]
                if "timestamp" not in df_5m.columns and "time" in df_5m.columns:
                    df_5m = df_5m.rename(columns={"time": "timestamp"})
                used_source = src
                print(f"📂  Loaded {len(df_5m):,} 5m bars from: {Path(src).name}")
                break
            except Exception as e:
                print(f"   ⚠️  Could not load {src}: {e}")

    if df_5m is None or len(df_5m) < 50:
        print("⚠️  Insufficient data — using rule-based classification only.")
        regime, confidence = classify_rule_based({})
        return _write_override(regime, confidence, {}, "rule-based (no data)", now_utc)

    # ── Aggregate to daily ────────────────────────────────────────
    daily = compute_daily_bars(df_5m)
    print(f"📅  Aggregated to {len(daily)} daily bars "
          f"({daily['date'].min().date()} → {daily['date'].max().date()})")

    # ── Compute features ─────────────────────────────────────────
    feat_df = compute_features(daily)
    labels = compute_labels(daily, feat_df)

    # Drop NaN rows
    valid = feat_df[FEATURE_COLS].dropna().index
    feat_df = feat_df.loc[valid]
    labels = labels.loc[valid]

    # ── Current market features (last row = today's state) ───────
    if len(feat_df) == 0:
        print("⚠️  No valid feature rows — falling back to rule-based.")
        regime, confidence = classify_rule_based({})
        return _write_override(regime, confidence, {}, "rule-based (no features)", now_utc)

    last_feat = feat_df.iloc[-1][FEATURE_COLS].to_dict()
    print(f"\n📊  Today's market features:")
    for k, v in last_feat.items():
        print(f"     {k:<22} {v:+.4f}")

    # ── Try ML classifier ─────────────────────────────────────────
    regime, confidence, clf_name = classify_ml(feat_df.iloc[:-1], labels.iloc[:-1], last_feat)

    if regime is None:
        print("\n⚠️  ML classifier unavailable — using rule-based fallback.")
        regime, confidence = classify_rule_based(last_feat)
        clf_name = "rule-based"

    print(f"\n🎯  Predicted regime : {regime}")
    print(f"    Confidence       : {confidence:.0%}")
    print(f"    Classifier       : {clf_name}")

    return _write_override(regime, confidence, last_feat, clf_name, now_utc)


def _write_override(
    regime: str,
    confidence: float,
    diagnostics: dict,
    clf_name: str,
    now_utc: datetime,
) -> dict:
    """Write config_override.json and return the dict."""
    strategy_overrides = STRATEGY_MAP.get(regime, STRATEGY_MAP["TREND"])

    override = {
        "generated_at": now_utc.isoformat(),
        "valid_until": (now_utc + timedelta(hours=24)).isoformat(),
        "regime": regime,
        "confidence": round(confidence, 4),
        "classifier": clf_name,
        "strategy_overrides": strategy_overrides,
        "diagnostics": {k: round(float(v), 6) if v is not None else None
                        for k, v in diagnostics.items()},
    }

    OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OVERRIDE_FILE, "w") as f:
        json.dump(override, f, indent=2)

    print(f"\n✅  Written to: {OVERRIDE_FILE}")
    print(f"\n📋  Strategy overrides for tomorrow:")
    for strat, enabled in strategy_overrides.items():
        icon = "✅" if enabled else "❌"
        print(f"     {icon}  {strat}")
    print()

    return override


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nightly ML Regime Classifier")
    parser.add_argument(
        "--bars-file",
        default=None,
        help="Path to 5m OHLCV CSV (default: auto-detect from data/)",
    )
    args = parser.parse_args()
    run_classifier(bars_file=args.bars_file)
