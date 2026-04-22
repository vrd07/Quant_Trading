#!/usr/bin/env python3
"""
Nightly Regime Classifier — ML-powered market type prediction
with Markov transitions and RL-lite performance feedback.

Runs at midnight, analyzes the last N days of XAUUSD data,
trains a RandomForestClassifier, smooths predictions with a
Markov transition model, and writes config_override.json with
confidence-weighted strategy recommendations.

Usage:
    python scripts/regime_classifier.py [--bars-file data/historical/XAUUSD_5m_real.csv]

Output:
    data/config_override.json  (read by main.py at startup)

Regime Labels:
    TREND    → directional day, big move relative to ATR
    RANGE    → sideways/choppy day, small net move
    VOLATILE → large ATR but no clear direction (news day)

Key improvements over v1:
    - Dynamic strategy weights (0.0-1.0) instead of binary on/off
    - Markov chain smooths day-to-day regime flip-flopping
    - Trade-performance feedback adjusts weights from real P&L
    - Low-confidence predictions enable more strategies (wider net)
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

from scripts.strategy_scorer import compute_strategy_scores, compute_regime_strategy_scores, adjust_weights

OVERRIDE_DIR = PROJECT_ROOT / "data"
LEGACY_OVERRIDE_FILE = OVERRIDE_DIR / "config_override.json"  # kept as XAUUSD copy for back-compat
HISTORICAL_DIR = PROJECT_ROOT / "data" / "historical"
LIVE_LOG_DIR = PROJECT_ROOT / "data" / "logs"


def override_path_for(symbol: str) -> Path:
    return OVERRIDE_DIR / f"config_override_{symbol.upper()}.json"


def historical_bars_for(symbol: str) -> Path:
    return HISTORICAL_DIR / f"{symbol.upper()}_5m_real.csv"


def discover_symbols() -> list:
    """Find every symbol with usable data — historical CSV or live candle_store.

    Returns a sorted list of base tickers (e.g. ["BTCUSD", "XAUUSD"]).
    """
    symbols = set()
    if HISTORICAL_DIR.exists():
        for p in HISTORICAL_DIR.glob("*_5m_real.csv"):
            symbols.add(p.name.split("_5m_real.csv")[0].upper())
    if LIVE_LOG_DIR.exists():
        for p in LIVE_LOG_DIR.glob("candle_store_*_5m.csv"):
            # candle_store_XAUUSD.x_5m.csv -> XAUUSD.x -> XAUUSD
            mid = p.name[len("candle_store_"):-len("_5m.csv")]
            base = mid.split(".")[0].upper()
            if base:
                symbols.add(base)
    return sorted(symbols)


def _discover_live_candle_csvs(symbol: str) -> list:
    """Auto-discover all broker-variant candle_store CSVs for this symbol.

    The bot may store bars under e.g. XAUUSD, XAUUSD.x, XAUUSD.e depending on
    which broker ticker is active. Returns all of them so the classifier
    always has the freshest live data for the given base symbol.
    """
    if not LIVE_LOG_DIR.exists():
        return []
    return sorted(LIVE_LOG_DIR.glob(f"candle_store_{symbol.upper()}*_5m.csv"))


# --- Feature Engineering ---------------------------------------------------

def compute_daily_bars(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 5m bars into daily OHLCV.

    Blow lens: avoid unnecessary .copy() — groupby doesn't mutate the source.
    Carmack lens: make the date column derivation visible, not buried.
    """
    dates = pd.to_datetime(df_5m["timestamp"]).dt.date
    daily = df_5m.assign(date=dates).groupby("date").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    return daily.sort_values("date").reset_index(drop=True)


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, span: int = 14) -> pd.Series:
    """Compute Average True Range — shared by features and labels.

    Knuth lens: single source of truth for a shared computation.
    Blow lens: eliminate hidden O(n) duplicate between compute_features and compute_labels.
    """
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=span, adjust=False).mean()


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

    # ATR (14) — single computation, reused by compute_labels via _compute_atr
    atr = _compute_atr(high, low, close, span=14)

    # ADX (14) — Wilder's smoothing: alpha = 1/period (not EWM span=14 which uses alpha=2/15)
    # Using consistent alpha=1/14 for TR, +DM and -DM so DI ratios are correct.
    _wilder_alpha = 1.0 / 14
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    )
    smooth_tr = tr.ewm(alpha=_wilder_alpha, adjust=False).mean()
    smooth_plus_dm = plus_dm.ewm(alpha=_wilder_alpha, adjust=False).mean()
    smooth_minus_dm = minus_dm.ewm(alpha=_wilder_alpha, adjust=False).mean()
    plus_di = (smooth_plus_dm / smooth_tr.replace(0, np.nan)) * 100
    minus_di = (smooth_minus_dm / smooth_tr.replace(0, np.nan)) * 100
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    adx = dx.ewm(alpha=_wilder_alpha, adjust=False).mean()

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

    # Parkinson volatility estimator: σ² = (1/4ln2) × E[ln(H/L)²]
    # Responds faster to intraday volatility shifts than EWM-ATR (no lag from close-to-close).
    # Expressed as z-score for stationarity across different price levels.
    log_hl = np.log((high / low.replace(0, np.nan)))
    park_var = (log_hl ** 2) / (4.0 * np.log(2))
    park_vol = park_var.rolling(5, min_periods=3).mean().pow(0.5)
    park_mean = park_vol.rolling(20, min_periods=10).mean()
    park_std = park_vol.rolling(20, min_periods=10).std()
    park_zscore = (park_vol - park_mean) / park_std.replace(0, np.nan)

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
        "park_vol_zscore": park_zscore.values,   # Parkinson vol z-score (fast vol regime signal)
    })
    return feat


def compute_labels(daily: pd.DataFrame, feat: pd.DataFrame) -> pd.Series:
    """Auto-label each day based on NEXT DAY's price action.

    Knuth lens: reuses _compute_atr() instead of duplicating the O(n) ATR
    computation that compute_features() already performs.

    TREND    : |next_close - today_close| / today_ATR > 1.2
    VOLATILE : today's range/ATR > 2.0 but net move < 0.8*ATR
    RANGE    : everything else
    """
    close = daily["close"]
    high = daily["high"]
    low = daily["low"]

    # Reuse shared ATR computation — was previously duplicated O(n)
    atr = _compute_atr(high, low, close, span=14)

    next_move = close.shift(-1) - close
    move_atr_ratio = (next_move.abs() / atr)
    range_atr = (high - low) / atr

    labels = pd.Series("RANGE", index=daily.index)
    labels[move_atr_ratio > 1.2] = "TREND"
    labels[(range_atr > 2.0) & (move_atr_ratio < 0.8)] = "VOLATILE"
    return labels


# --- Core Classifier -------------------------------------------------------

# Dynamic weights (0.0-1.0) replace the old binary STRATEGY_MAP.
# Each value is a prior confidence that a strategy performs well in that regime.
# Strategies with weight >= CONFIDENCE_THRESHOLD get enabled.
STRATEGY_WEIGHTS = {
    "TREND": {
        "breakout":       0.85,
        "momentum":       0.80,
        "kalman_regime":  0.90,
        "mean_reversion": 0.00,  # disabled: -1.63%, PF 0.40, 15% WR — no edge on gold
        "vwap":           0.00,  # disabled: -0.98%, PF 0.77 — loses money on trending gold
        "mini_medallion": 0.65,
        "sbr":            0.80,  # v2 backtest: PF 1.88, DD 1.61% — strong trend performer
        "supply_demand":  0.20,  # backtest: PF 1.07, no tunable edge on XAUUSD
        "asia_range_fade": 0.15, # range-focused; trend regime = not its edge
        "descending_channel_breakout": 0.70,
        "smc_ob":         0.70,
        "fibonacci_retracement": 0.70,  # big_trend sweep: PF 1.75, DD 9.2% (6mo)
    },
    "RANGE": {
        "breakout":       0.60,  # raised: cooldown-improved breakout works across regimes
        "momentum":       0.55,  # raised: still captures pullback momentum entries in range
        "kalman_regime":  0.75,  # raised: range-mode OU mean-reversion is Kalman's strength
        "mean_reversion": 0.00,  # disabled: backtest confirmed no edge
        "vwap":           0.45,  # 2026-04-15 refresh: PF 1.67, +1.54%, DD 1.3% over 2yr (30 trades, thin sample) — small clean edge
        "mini_medallion": 0.60,
        "sbr":            0.40,
        "supply_demand":  0.25,
        "asia_range_fade": 0.70, # PF 1.31, WR 45.3%, DD 1.6% — range-optimised
        "descending_channel_breakout": 0.45,
        "smc_ob":         0.50,
        "fibonacci_retracement": 0.20,  # only_in_regime=TREND — gated off in range
    },
    "VOLATILE": {
        "breakout":       0.55,  # raised: breakout captures vol expansion moves
        "momentum":       0.50,  # raised: momentum rides vol-driven trends
        "kalman_regime":  0.90,
        "mean_reversion": 0.00,  # disabled
        "vwap":           0.45,  # 2026-04-15: re-enabled alongside RANGE (same backtest evidence)
        "mini_medallion": 0.70,
        "sbr":            0.55,
        "supply_demand":  0.25,
        "asia_range_fade": 0.30, # wide ranges hurt fade logic
        "descending_channel_breakout": 0.55,
        "smc_ob":         0.60,
        "fibonacci_retracement": 0.55,  # strong trends with pullbacks = fib's edge
    },
}

# Strategies with weight below this threshold are disabled.
# Low-confidence predictions lower this threshold to enable a wider strategy net.
CONFIDENCE_THRESHOLD = 0.40

# Torvalds lens: frozenset for O(1) membership tests in Markov loop.
# List preserved as REGIME_ORDER for deterministic iteration.
REGIME_ORDER = ("TREND", "RANGE", "VOLATILE")
REGIMES = frozenset(REGIME_ORDER)

FEATURE_COLS = [
    "adx_14", "atr_pct", "bb_width_ratio",
    "close_ema20_pct", "momentum_1d", "momentum_5d",
    "range_atr_ratio", "vol_ratio",
    "park_vol_zscore",   # Parkinson vol z-score: fast regime-change signal
]


# --- Markov Chain Transition Model -----------------------------------------

def compute_transition_matrix(labels: pd.Series) -> dict:
    """Compute a 3x3 Markov transition matrix from historical regime labels.

    Returns a dict: {from_regime: {to_regime: probability}}.
    Rows sum to 1.0. Uses Laplace smoothing to avoid zero probabilities.
    """
    counts = {r: {r2: 1 for r2 in REGIME_ORDER} for r in REGIME_ORDER}  # Laplace prior

    clean = labels.dropna().values
    for i in range(len(clean) - 1):
        from_r = clean[i]
        to_r = clean[i + 1]
        if from_r in REGIMES and to_r in REGIMES:
            counts[from_r][to_r] += 1

    matrix = {}
    for from_r in REGIME_ORDER:
        total = sum(counts[from_r].values())
        matrix[from_r] = {
            to_r: round(counts[from_r][to_r] / total, 4) for to_r in REGIME_ORDER
        }
    return matrix


def smooth_prediction_with_markov(
    rf_proba: dict,
    prev_regime: str,
    transition_matrix: dict,
    alpha: float = 0.7,
) -> dict:
    """Blend RandomForest probabilities with Markov prior.

    P(regime) = alpha * RF_prob + (1-alpha) * Markov_transition_prob

    Args:
        rf_proba: {regime: probability} from RandomForest
        prev_regime: yesterday's regime label
        transition_matrix: from compute_transition_matrix()
        alpha: weight for RF prediction (0.7 = trust RF mostly)

    Returns:
        {regime: smoothed_probability}
    """
    if prev_regime not in transition_matrix:
        return rf_proba

    markov_prior = transition_matrix[prev_regime]
    smoothed = {}
    for regime in REGIME_ORDER:
        rf_p = rf_proba.get(regime, 0.0)
        mk_p = markov_prior.get(regime, 1.0 / len(REGIME_ORDER))
        smoothed[regime] = alpha * rf_p + (1 - alpha) * mk_p

    # Renormalize to sum to 1.0
    total = sum(smoothed.values())
    if total > 0:
        smoothed = {k: v / total for k, v in smoothed.items()}

    return smoothed


def resolve_strategy_overrides(
    regime: str,
    confidence: float,
    performance_scores: dict,
    regime_performance_scores: dict = None,
) -> dict:
    """Convert regime + confidence into strategy enable/disable dict.

    Low-confidence predictions lower the threshold so more strategies
    stay enabled (wider safety net). Regime-specific performance scores
    are preferred over global scores when available.

    Args:
        regime: Predicted regime string (TREND/RANGE/VOLATILE)
        confidence: Classifier confidence [0, 1]
        performance_scores: Global {strategy: score} fallback
        regime_performance_scores: {regime: {strategy: score}} — preferred when present
    """
    base_weights = STRATEGY_WEIGHTS.get(regime, STRATEGY_WEIGHTS["RANGE"])

    # Prefer regime-specific scores so a strategy's TREND performance
    # doesn't penalise it during RANGE days and vice versa.
    scores_for_regime = (
        (regime_performance_scores or {}).get(regime)
        or (regime_performance_scores or {}).get(regime.upper())
        or performance_scores
        or {}
    )

    # Apply RL-lite feedback from trade performance
    if scores_for_regime:
        final_weights = adjust_weights(base_weights, scores_for_regime, blend_ratio=0.4)
    else:
        final_weights = dict(base_weights)

    # Adjust threshold: low confidence -> lower threshold -> more strategies enabled
    effective_threshold = CONFIDENCE_THRESHOLD
    if confidence < 0.55:
        effective_threshold *= 0.7  # 0.28 -- nearly everything enabled
    elif confidence < 0.65:
        effective_threshold *= 0.85  # 0.34

    overrides = {}
    for strategy, weight in final_weights.items():
        overrides[strategy] = bool(weight >= effective_threshold)

    return overrides


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


def classify_ml(
    feat_df: pd.DataFrame,
    labels: pd.Series,
    feat_row: dict,
    prev_regime: str = None,
    transition_matrix: dict = None,
) -> tuple[str, float, str, dict]:
    """Train a calibrated RandomForestClassifier and predict tomorrow's regime.

    Uses Platt/isotonic calibration so confidence values reflect true probabilities
    (raw RF proba is often overconfident). Markov chain smoothing applied when
    previous regime and transition matrix are available.
    Returns (regime, confidence, classifier_name, raw_proba).
    """
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.preprocessing import LabelEncoder
    except ImportError:
        return None, None, None, None

    X = feat_df[FEATURE_COLS].dropna()
    y = labels.loc[X.index].dropna()
    X = X.loc[y.index]

    if len(X) < 30:
        return None, None, None, None

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    base_clf = RandomForestClassifier(
        n_estimators=100,           # 100 trees is plenty for ~300 rows × 9 features
        max_depth=5,
        min_samples_leaf=5,
        max_features="sqrt",
        random_state=42,
        class_weight="balanced",
        n_jobs=-1,                  # Use all CPU cores for parallel tree fitting
    )

    # Calibrate probabilities so confidence=0.67 actually means 67% accuracy,
    # not the raw overconfident RF estimate. Use 'sigmoid' (fast Platt scaling)
    # instead of 'isotonic' (needs more data and is slower).
    import numpy as np
    min_class_count = int(np.bincount(y_enc).min())
    cv_folds = min(2, min_class_count)
    if cv_folds >= 2:
        clf = CalibratedClassifierCV(base_clf, cv=cv_folds, method="sigmoid")
        clf.fit(X, y_enc)
    else:
        # Insufficient per-class samples for calibration — use base RF directly
        base_clf.fit(X, y_enc)
        clf = base_clf

    # Build feature row as DataFrame
    row_df = pd.DataFrame([feat_row])[FEATURE_COLS]
    if row_df.isnull().any().any():
        return None, None, None, None

    proba = clf.predict_proba(row_df)[0]
    classes = le.classes_

    # Convert to {regime: probability} dict
    rf_proba = {classes[i]: float(proba[i]) for i in range(len(classes))}

    # Apply Markov chain smoothing if available
    if prev_regime and transition_matrix:
        smoothed = smooth_prediction_with_markov(
            rf_proba, prev_regime, transition_matrix, alpha=0.7
        )
        print(f"\n\U0001f517  Markov smoothing (prev={prev_regime}):")
        for r in REGIME_ORDER:
            rf_p = rf_proba.get(r, 0.0)
            sm_p = smoothed.get(r, 0.0)
            print(f"     {r:<10} RF={rf_p:.2%} -> smoothed={sm_p:.2%}")
        final_proba = smoothed
    else:
        final_proba = rf_proba

    best_regime = max(final_proba, key=final_proba.get)
    confidence = final_proba[best_regime]

    return best_regime, confidence, f"RandomForest+Markov (n={len(X)} samples)", final_proba


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_classifier(symbol: str = "XAUUSD", bars_file: Path = None) -> dict:
    """
    Run the full classification pipeline for one symbol.
    Returns the override dict (also written to disk as config_override_{SYMBOL}.json).
    """
    symbol = symbol.upper()
    now_utc = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f"🤖  Nightly Regime Classifier [{symbol}] — {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── Load and Stitch bars ─────────────────────────────────────
    sources = [historical_bars_for(symbol)] + _discover_live_candle_csvs(symbol)
    if bars_file:
        sources.append(bars_file)

    dfs = []
    for src in sources:
        if src and Path(src).exists():
            try:
                df_src = pd.read_csv(src)
                if df_src.empty:
                    continue
                # Normalize column names
                df_src.columns = [c.lower() for c in df_src.columns]
                if "timestamp" not in df_src.columns and "time" in df_src.columns:
                    df_src = df_src.rename(columns={"time": "timestamp"})
                # Normalize timestamp types to prevent naive/aware comparison crashes
                df_src["timestamp"] = pd.to_datetime(df_src["timestamp"], utc=True)
                dfs.append(df_src)
                print(f"📂  Loaded {len(df_src):,} 5m bars from: {Path(src).name}")
            except Exception as e:
                print(f"   ⚠️  Could not load {src}: {e}")

    df_5m = None
    if dfs:
        df_5m = pd.concat(dfs, ignore_index=True)
        df_5m = df_5m.drop_duplicates(subset=["timestamp"], keep="last")
        df_5m = df_5m.sort_values("timestamp").reset_index(drop=True)
        print(f"🔗  Stitched total {len(df_5m):,} unique 5m bars")

    if df_5m is None or len(df_5m) < 50:
        print("⚠️  Insufficient data — using rule-based classification only.")
        regime, confidence = classify_rule_based({})
        return _write_override(symbol, regime, confidence, {}, "rule-based (no data)", now_utc)

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
        return _write_override(symbol, regime, confidence, {}, "rule-based (no features)", now_utc)

    last_feat = feat_df.iloc[-1][FEATURE_COLS].to_dict()
    print(f"\n\U0001f4ca  Today's market features:")
    for k, v in last_feat.items():
        print(f"     {k:<22} {v:+.4f}")

    # -- Compute Markov transition matrix -----------------------------------
    trans_matrix = compute_transition_matrix(labels)
    prev_regime = None
    prev_override = _load_previous_override(symbol)
    if prev_override:
        prev_regime = prev_override.get("regime")
    print(f"\n\U0001f517  Markov chain: previous regime = {prev_regime or 'N/A'}")

    # -- Compute trade-performance scores (RL-lite feedback) ----------------
    # Per-symbol scoring so BTC P&L doesn't distort XAU weights and vice versa.
    perf_scores = compute_strategy_scores(lookback_days=30, symbol=symbol)
    regime_perf_scores = compute_regime_strategy_scores(lookback_days=30, symbol=symbol)
    if perf_scores:
        print(f"\n\U0001f4c8  Trade performance scores (last 30d, global):")
        for strat, score in sorted(perf_scores.items(), key=lambda x: -x[1]):
            icon = "\U0001f4c8" if score > 0 else "\U0001f4c9"
            print(f"     {icon} {strat:<20} {score:+.4f}")
    else:
        print("\n\U0001f4c8  No trade performance data available yet.")

    if regime_perf_scores:
        print(f"\n\U0001f4ca  Regime-specific performance scores:")
        for reg, scores in sorted(regime_perf_scores.items()):
            for strat, sc in sorted(scores.items(), key=lambda x: -x[1]):
                icon = "\U0001f4c8" if sc > 0 else "\U0001f4c9"
                print(f"     {icon} [{reg:<8}] {strat:<20} {sc:+.4f}")

    # -- Try ML classifier with Markov smoothing ----------------------------
    regime, confidence, clf_name, raw_proba = classify_ml(
        feat_df.iloc[:-1], labels.iloc[:-1], last_feat,
        prev_regime=prev_regime, transition_matrix=trans_matrix,
    )

    if regime is None:
        print("\n\u26a0\ufe0f  ML classifier unavailable \u2014 using rule-based fallback.")
        regime, confidence = classify_rule_based(last_feat)
        clf_name = "rule-based"
        raw_proba = {}

    print(f"\n\U0001f3af  Predicted regime : {regime}")
    print(f"    Confidence       : {confidence:.0%}")
    print(f"    Classifier       : {clf_name}")

    return _write_override(
        symbol, regime, confidence, last_feat, clf_name, now_utc,
        transition_matrix=trans_matrix,
        performance_scores=perf_scores,
        regime_performance_scores=regime_perf_scores,
        raw_proba=raw_proba,
    )


def _load_previous_override(symbol: str) -> dict:
    """Load the previous config_override_{SYMBOL}.json for Markov chain prior."""
    path = override_path_for(symbol)
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _write_override(
    symbol: str,
    regime: str,
    confidence: float,
    diagnostics: dict,
    clf_name: str,
    now_utc: datetime,
    transition_matrix: dict = None,
    performance_scores: dict = None,
    regime_performance_scores: dict = None,
    raw_proba: dict = None,
) -> dict:
    """Write config_override.json atomically (temp-file rename) with dynamic strategy weights.

    Atomic write prevents JSON corruption from the race between the nightly
    classifier thread and the 4-hour intraday check in main.py.
    """
    strategy_overrides = resolve_strategy_overrides(
        regime, confidence, performance_scores or {},
        regime_performance_scores=regime_performance_scores,
    )

    # Build the weights detail for transparency
    base_weights = STRATEGY_WEIGHTS.get(regime, STRATEGY_WEIGHTS["RANGE"])

    override = {
        "symbol": symbol,
        "generated_at": now_utc.isoformat(),
        "valid_until": (now_utc + timedelta(hours=24)).isoformat(),
        "regime": regime,
        "confidence": round(confidence, 4),
        "classifier": clf_name,
        "strategy_overrides": strategy_overrides,
        "diagnostics": {k: round(float(v), 6) if v is not None else None
                        for k, v in diagnostics.items()},
        "strategy_weights": {k: round(v, 4) for k, v in base_weights.items()},
        "performance_scores": performance_scores or {},
        "regime_probabilities": {k: round(v, 4) for k, v in (raw_proba or {}).items()},
        "transition_matrix": transition_matrix or {},
    }

    out_path = override_path_for(symbol)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to temp file then rename so main.py never reads partial JSON
    import tempfile
    import os
    def _atomic_write(target: Path, payload: dict) -> None:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=target.parent, prefix=".override_tmp_", suffix=".json"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(payload, f, indent=2)
            Path(tmp_path).replace(target)   # POSIX-atomic rename
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    _atomic_write(out_path, override)
    # XAUUSD also refreshes the legacy unsuffixed file so older consumers keep working.
    if symbol == "XAUUSD":
        _atomic_write(LEGACY_OVERRIDE_FILE, override)

    print(f"\n\u2705  Written to: {out_path}")
    print(f"\n\U0001f4cb  Strategy overrides for tomorrow (threshold={CONFIDENCE_THRESHOLD:.2f}):")
    for strat, enabled in strategy_overrides.items():
        w = base_weights.get(strat, 0.0)
        icon = "\u2705" if enabled else "\u274c"
        print(f"     {icon}  {strat:<20} (weight={w:.2f})")
    print()

    return override


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nightly ML Regime Classifier")
    parser.add_argument(
        "--symbol",
        default=None,
        help="Base ticker to classify (e.g. XAUUSD, BTCUSD). "
             "If omitted, runs for every symbol discovered in data/historical "
             "and data/logs/candle_store_*.",
    )
    parser.add_argument(
        "--bars-file",
        default=None,
        help="Optional extra 5m OHLCV CSV to stitch in (in addition to autodiscovered sources).",
    )
    args = parser.parse_args()

    if args.symbol:
        targets = [args.symbol.upper()]
    else:
        targets = discover_symbols() or ["XAUUSD"]
        print(f"🎯  Auto-discovered symbols: {', '.join(targets)}")

    for sym in targets:
        try:
            run_classifier(symbol=sym, bars_file=args.bars_file)
        except Exception as e:
            print(f"\n❌  Classifier failed for {sym}: {e}")
