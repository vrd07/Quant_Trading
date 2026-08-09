#!/usr/bin/env python3
"""
Full behavioural port of GoldHTF_AutoOpt_EA.mq5 v3.00 — used to prove which parts of
the EA actually execute, and to produce a dated backtest with a stage-by-stage funnel.

This is NOT a re-implementation of the idea; it is a line-faithful port of OnTick and
everything it calls, including the MQL5 buffer indexing convention (index 0 = the
FORMING bar of that timeframe, index 1 = the last CLOSED bar).

Fidelity limits — read these before trusting a number:
  * The EA evaluates the legacy chain on EVERY TICK against the forming M5 candle.
    This sim evaluates once per M5 CLOSE. Legacy trade counts here are a LOWER BOUND;
    the real tester will fire more often and at worse average prices.
  * adx[0] uses the last CLOSED H4 value, not a forming-bar ADX. It only feeds regime
    classification, which with InpUseFixedRR=true drives the ATR multiplier and lot
    multiplier only -- and the lot multiplier is largely inert under a 0.08 lot floor.
  * No per-bar spread series, so the InpMaxSpread gate is not modelled. Cost is the
    repo-standard strict 0.20/side proxy instead.
  * UpdateMarketRegime is re-evaluated every M5 close (the EA throttles to 30s).

Usage: python scripts/simulate_goldhtf_ea.py [--start 2026-07-01] [--end 2026-07-31]
                                             [--balance 10000] [--min-lot 0.08]
"""

import sys
import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CSV = PROJECT_ROOT / "data/historical/XAUUSD_5m_real.csv"
REPORT = None  # set from the date range in main()

POINT = 0.01          # MQL5 SYMBOL_POINT for 2-digit gold
VALUE_PER_LOT = 100.0  # $ per 1.00 price move per lot
TICK_SIZE = 0.01
TICK_VALUE = 1.0       # $ per tick per lot
LOT_STEP = 0.01
COST = 0.20            # strict per-side cost in price units

# ---- EA inputs (v3.00 defaults) -------------------------------------------
INP = dict(
    NoRepaint=True,   # read signals off CLOSED bars only (EA v3.1 fix)
    RegimeATR_H4=True,  # measure regime volatility on H4, not M5
    EntryShift=1,       # 0 = trigger on the just-closed M5 bar, 1 = one back
    RiskPercent=1.0, MaxOpenTrades=1, MinLot=0.08, MaxLot=1.00, Slippage=30,
    UseDoubleFVG=True, DFVG_Only=False, DFVG_Lookback=60, DFVG_MinATR=0.25,
    DFVG_PairWindow=12, DFVG_MaxAgeBars=40, DFVG_TapMinPct=50.0,
    DFVG_TapMaxPct=60.0, DFVG_RequireColor=True, DFVG_RequireTrend=False,
    UseFVG=True, UseOB=True, FVGMinPips=5, OBLookback=10,
    # v2 legacy-zone semantics (see zone_v2 helpers): scan the whole lookback,
    # discard gaps price has already traded through, and require the zone to have
    # been TOUCHED since it formed instead of testing only the last closed bar.
    ZoneV2=False, FVGLookback=20, ZoneATR=False, ZoneATR_DFVG=False,
    ZoneATRMult=1.0,
    # Entry-chain ablation switches. Each removes exactly ONE leg; all default off,
    # so an unflagged run is byte-identical to the shipped config.
    # See docs/superpowers/specs/2026-08-10-goldhtf-entry-ablation-design.md
    NoTrendLeg=False, NoMTFLeg=False, NoPatternLeg=False, NoConfirm=False,
    SkipRanging=False,
    NoZoneLeg=False, ZoneStopATRMult=0.0,
    UseEngulfing=True, UseHammer=True, UseInvHammer=True, UsePiercing=True,
    Use3Candle=True, UseWM=True,
    UseMAFilter=True, MAFast=9, MASlow=21, UseRSIFilter=True, RSIPeriod=14,
    RSIBull=55.0, RSIBear=45.0, MaxZoneDistATR=0.0, CooldownMinutes=30,
    UseSession=True, StartHour=8, EndHour=20, UseATR=True, ATRPeriod=14,
    UseProfitLadder=True, BETriggerPct=0.0, LockTriggerPct=50.0, LockPct=10.0,
    TrailStartPct=80.0, TrailGapPct=20.0, TrailStepPct=5.0, RemoveTPOnTrail=True,
    UseAutoOpt=True, UseFixedRR=False, DefaultRR=2.0, MaxRR=2.5, MinRR=0.8,
    MaxATRMult=3.0, MinATRMult=1.0, DryLotFactor=0.5,
)
BROKER_UTC_OFFSET = 2   # session hours are broker-server time; GMT+2 assumed


# ---------------------------------------------------------------------------
# Indicator helpers (Wilder smoothing, matching MT5)
# ---------------------------------------------------------------------------
def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def true_range(df):
    pc = df.close.shift()
    return pd.concat([df.high - df.low, (df.high - pc).abs(),
                      (df.low - pc).abs()], axis=1).max(axis=1)


def rsi(series, n):
    d = series.diff()
    return 100 - 100 / (1 + wilder(d.clip(lower=0), n) / wilder(-d.clip(upper=0), n))


def adx(df, n=14):
    up, dn = df.high.diff(), -df.low.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = wilder(true_range(df), n)
    pdi = 100 * wilder(pd.Series(plus, index=df.index), n) / atr_
    mdi = 100 * wilder(pd.Series(minus, index=df.index), n) / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return wilder(dx.fillna(0), n)


# ---------------------------------------------------------------------------
# Timeframe scaffolding: completed bars + the running FORMING bar
# ---------------------------------------------------------------------------
class TF:
    """Completed-bar frame for one timeframe, plus per-M5-bar forming aggregates."""

    def __init__(self, m5, rule, minutes):
        self.minutes = minutes
        bars = (m5.resample(rule, label="left", closed="left")
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "volume": "sum"})
                .dropna(subset=["open", "high", "low", "close"]))
        self.bars = bars
        self.lab = bars.index.to_numpy()
        self.o = bars.open.to_numpy(float)
        self.h = bars.high.to_numpy(float)
        self.l = bars.low.to_numpy(float)
        self.c = bars.close.to_numpy(float)

        t = m5.index
        tf_td = pd.Timedelta(minutes=minutes)
        # last bucket fully closed by the close of M5 bar j
        self.closed_idx = np.searchsorted(
            self.lab, (t + pd.Timedelta(minutes=5) - tf_td).to_numpy(), "right") - 1
        # bucket currently forming at M5 bar j
        self.form_idx = np.searchsorted(self.lab, t.to_numpy(), "right") - 1

        # running OHLC of the forming bucket, up to and including M5 bar j
        g = pd.Series(self.form_idx, index=t)
        self.f_open = m5.open.groupby(g).transform("first").to_numpy(float)
        self.f_high = m5.high.groupby(g).cummax().to_numpy(float)
        self.f_low = m5.low.groupby(g).cummin().to_numpy(float)
        self.f_close = m5.close.to_numpy(float)

    def idx(self, j, m):
        """MQL5 buffer index m at M5 bar j -> completed-bar array index (m>=1)."""
        return self.closed_idx[j] - (m - 1)


def ema_at_forming(tf, period, j):
    """EMA(period) including the forming bar, computed O(1) from the closed-bar EMA."""
    k = tf.closed_idx[j]
    if k < period:
        return np.nan
    prev = tf.ema_cache[period][k]
    a = 2.0 / (period + 1.0)
    return a * tf.f_close[j] + (1 - a) * prev


# ---------------------------------------------------------------------------
# Ported EA functions
# ---------------------------------------------------------------------------
def get_htf_trend(h4, j, st):
    k = h4.closed_idx[j]
    if k < 60:
        return 0, "no_data"
    maf = ema_at_forming(h4, INP["MAFast"], j)
    mas = ema_at_forming(h4, INP["MASlow"], j)
    r = st["h4_rsi"][k]                       # RSI on last closed bar (see fidelity note)
    if not (np.isfinite(maf) and np.isfinite(mas) and np.isfinite(r)):
        return 0, "no_data"

    h1_, h2_, h3_ = h4.h[k], h4.h[k - 1], h4.h[k - 2]
    l1_, l2_, l3_ = h4.l[k], h4.l[k - 1], h4.l[k - 2]
    hh = h1_ > h2_ > h3_
    hl = l1_ > l2_ > l3_
    lh = h1_ < h2_ < h3_
    ll = l1_ < l2_ < l3_

    use_momo = INP["UseMAFilter"] or INP["UseRSIFilter"]
    ma_bull = (not INP["UseMAFilter"]) or (maf > mas)
    ma_bear = (not INP["UseMAFilter"]) or (maf < mas)
    rsi_bull = (not INP["UseRSIFilter"]) or (r > INP["RSIBull"])
    rsi_bear = (not INP["UseRSIFilter"]) or (r < INP["RSIBear"])

    if (hh and hl) or (use_momo and ma_bull and rsi_bull):
        return 1, "ok"
    if (lh and ll) or (use_momo and ma_bear and rsi_bear):
        return -1, "ok"
    return 0, "flat"


def detect_fvg(h1, j, min_gap, zone):
    """Legacy H1 FVG. Uses CLOSED bars only (MQL5 loop i=1..2 -> i+2 max 4)."""
    k = h1.closed_idx[j]
    if k < 5:
        return 0
    for m in (1, 2):
        i, i2 = h1.idx(j, m), h1.idx(j, m + 2)
        if h1.l[i] - h1.h[i2] > min_gap:                 # bullish
            zone["high"], zone["low"], zone["type"] = h1.l[i], h1.h[i2], 1
            return 1
        if h1.l[i2] - h1.h[i] > min_gap:                 # bearish
            zone["high"], zone["low"], zone["type"] = h1.l[i2], h1.h[i], -1
            return -1
    return 0


def is_zone_mitigated(h1, j, typ, zone):
    """NoRepaint: index 1 (last CLOSED H1 bar). Else index 0 (forming)."""
    if INP["NoRepaint"]:
        k = h1.closed_idx[j]
        c0, l0, h0 = h1.c[k], h1.l[k], h1.h[k]
    else:
        c0, l0, h0 = h1.f_close[j], h1.f_low[j], h1.f_high[j]
    if typ == 1:
        return l0 <= zone["high"] and c0 > zone["low"]
    return h0 >= zone["low"] and c0 < zone["high"]


# --- v2 zone helpers -------------------------------------------------------
# A zone is a price band plus the bar index it formed on. `high` is the upper
# edge and `low` the lower edge; for a BULLISH zone price returns down and meets
# `high` first, for a BEARISH zone it returns up and meets `low` first.
def zone_filled(tf, k, d, top, bot, idx):
    """Has price traded fully THROUGH the zone since it formed? (invalidated)"""
    for i in range(idx + 1, k + 1):
        if d == 1 and tf.l[i] <= bot:
            return True
        if d == -1 and tf.h[i] >= top:
            return True
    return False


def zone_touched(tf, k, d, top, bot, idx):
    """Has price entered the zone at ANY point since it formed? (tested + held)"""
    for i in range(idx + 1, k + 1):
        if d == 1 and tf.l[i] <= top:
            return True
        if d == -1 and tf.h[i] >= bot:
            return True
    return False


def detect_fvg_v2(h1, j, min_gap, zone):
    """Scan the full lookback for the newest UNFILLED, already-TOUCHED gap.

    v1 scanned MQL indices 1..2 only (a 4-bar window) and never checked whether
    the gap had been traded through. Its companion mitigation test was also
    vacuous for the freshest gap: with zone.high == l[1], `l[1] <= zone.high` is
    true by construction, so a gap that formed on the last closed bar passed the
    gate without price ever returning to it.
    """
    k = h1.closed_idx[j]
    if k < 5:
        return 0
    lo = max(2, k - INP["FVGLookback"] + 1)
    for i in range(k, lo - 1, -1):              # newest -> oldest
        if h1.l[i] - h1.h[i - 2] > min_gap:
            d, top, bot = 1, h1.l[i], h1.h[i - 2]
        elif h1.l[i - 2] - h1.h[i] > min_gap:
            d, top, bot = -1, h1.l[i - 2], h1.h[i]
        else:
            continue
        if zone_filled(h1, k, d, top, bot, i):
            continue
        if not zone_touched(h1, k, d, top, bot, i):
            continue
        zone["high"], zone["low"], zone["type"] = top, bot, d
        return d
    return 0


def detect_ob_v2(h1, j, zone):
    """Same OB pattern as v1, but unfilled + touched instead of one-bar mitigation."""
    k = h1.closed_idx[j]
    if k < INP["OBLookback"] + 5:
        return 0
    for m in range(2, INP["OBLookback"]):
        i, im1 = h1.idx(j, m), h1.idx(j, m - 1)
        bear = h1.c[i] < h1.o[i]
        bull_disp = h1.c[im1] > h1.o[im1] and \
            (h1.c[im1] - h1.o[im1]) > (h1.o[i] - h1.c[i]) * 0.5
        bull = h1.c[i] > h1.o[i]
        bear_disp = h1.c[im1] < h1.o[im1] and \
            (h1.o[im1] - h1.c[im1]) > (h1.c[i] - h1.o[i]) * 0.5
        if bear and bull_disp and h1.c[im1] > h1.o[i]:
            d = 1
        elif bull and bear_disp and h1.c[im1] < h1.o[i]:
            d = -1
        else:
            continue
        top, bot = h1.h[i], h1.l[i]
        if zone_filled(h1, k, d, top, bot, i):
            continue
        if not zone_touched(h1, k, d, top, bot, i):
            continue
        zone["high"], zone["low"], zone["type"] = top, bot, d
        return d
    return 0


def detect_ob(h1, j, zone):
    k = h1.closed_idx[j]
    if k < INP["OBLookback"] + 5:
        return 0
    for m in range(2, INP["OBLookback"]):
        i, im1 = h1.idx(j, m), h1.idx(j, m - 1)
        bear = h1.c[i] < h1.o[i]
        bull_disp = h1.c[im1] > h1.o[im1] and \
            (h1.c[im1] - h1.o[im1]) > (h1.o[i] - h1.c[i]) * 0.5
        if bear and bull_disp and h1.c[im1] > h1.o[i]:
            zone["high"], zone["low"], zone["type"] = h1.h[i], h1.l[i], 1
            return 1
        bull = h1.c[i] > h1.o[i]
        bear_disp = h1.c[im1] < h1.o[im1] and \
            (h1.o[im1] - h1.c[im1]) > (h1.c[i] - h1.o[i]) * 0.5
        if bull and bear_disp and h1.c[im1] < h1.o[i]:
            zone["high"], zone["low"], zone["type"] = h1.h[i], h1.l[i], -1
            return -1
    return 0


def check_mtf_structure(m15, j, trend):
    k = m15.closed_idx[j]
    if k < 6:
        return False
    if INP["NoRepaint"]:
        c0 = m15.c[k]
        h1_, h2_, h3_ = m15.h[k - 1], m15.h[k - 2], m15.h[k - 3]
        l1_, l2_, l3_ = m15.l[k - 1], m15.l[k - 2], m15.l[k - 3]
    else:
        c0 = m15.f_close[j]
        h1_, h2_, h3_ = m15.h[k], m15.h[k - 1], m15.h[k - 2]
        l1_, l2_, l3_ = m15.l[k], m15.l[k - 1], m15.l[k - 2]
    if trend == 1:
        return (c0 > h2_ and l1_ > l2_) or (l1_ > l3_)
    return (c0 < l2_ and h1_ < h2_) or (h1_ < h3_)


# --- candlestick patterns (index 0 = M5 bar j) -----------------------------
def _hammer(o, h, l, c, bull):
    body, rng = abs(c - o), h - l
    if rng == 0:
        return False
    low_sh = (o if bull else c) - l
    up_sh = h - (c if bull else o)
    return body / rng < 0.3 and low_sh / rng > 0.6 and up_sh / rng < 0.1


def _inv_hammer(o, h, l, c, bull):
    body, rng = abs(c - o), h - l
    if rng == 0:
        return False
    up_sh = h - (c if bull else o)
    low_sh = (o if bull else c) - l
    return body / rng < 0.3 and up_sh / rng > 0.6 and low_sh / rng < 0.1


def check_entry_confirmation(o, h, l, c, j, trend, htf_signal):
    if trend != htf_signal and htf_signal != 0:
        return 0, "trend_vs_zone_mismatch"

    sh = INP["EntryShift"] if INP["NoRepaint"] else 0
    def O(m): return o[j - m - sh]
    def H(m): return h[j - m - sh]
    def L(m): return l[j - m - sh]
    def C(m): return c[j - m - sh]

    # Ablating the pattern leg makes the pattern vacuously true; the directional
    # close-confirm (and, unless ablated too, nothing else) becomes the trigger.
    pat = INP["NoPatternLeg"]
    if trend == 1:
        if not INP["NoPatternLeg"]:
            if INP["UseHammer"] and _hammer(O(1), H(1), L(1), C(1), True): pat = True
            if INP["UseInvHammer"] and _inv_hammer(O(1), H(1), L(1), C(1), True): pat = True
            if INP["UseEngulfing"] and (C(2) < O(2) and C(1) > O(1)
                                        and O(1) < C(2) and C(1) > O(2)): pat = True
            if INP["UsePiercing"] and (C(2) < O(2) and C(1) > O(1)
                                       and C(1) > (O(2) + C(2)) / 2 and O(1) < C(2)): pat = True
            if INP["Use3Candle"] and (C(3) < O(3)
                                      and abs(C(2) - O(2)) < abs(C(3) - O(3)) * 0.3
                                      and C(1) > O(1) and C(1) > (O(3) + C(3)) / 2): pat = True
            if INP["UseWM"] and (L(5) < L(4) and L(5) < L(3) and L(1) < L(2) and L(1) < L(3)
                                 and L(3) > L(5) and L(3) > L(1) and C(1) > H(3)): pat = True
        if pat and (INP["NoConfirm"] or C(0) > O(0)):
            return 1, "fired"
    else:
        if not INP["NoPatternLeg"]:
            if INP["UseHammer"] and _hammer(O(1), H(1), L(1), C(1), False): pat = True
            if INP["UseInvHammer"] and _inv_hammer(O(1), H(1), L(1), C(1), False): pat = True
            if INP["UseEngulfing"] and (C(2) > O(2) and C(1) < O(1)
                                        and O(1) > C(2) and C(1) < O(2)): pat = True
            if INP["UsePiercing"] and (C(2) > O(2) and C(1) < O(1)
                                       and C(1) < (O(2) + C(2)) / 2 and O(1) > C(2)): pat = True
            if INP["Use3Candle"] and (C(3) > O(3)
                                      and abs(C(2) - O(2)) < abs(C(3) - O(3)) * 0.3
                                      and C(1) < O(1) and C(1) < (O(3) + C(3)) / 2): pat = True
            if INP["UseWM"] and (H(5) > H(4) and H(5) > H(3) and H(1) > H(2) and H(1) > H(3)
                                 and H(3) < H(5) and H(3) < H(1) and C(1) < L(3)): pat = True
        if pat and (INP["NoConfirm"] or C(0) < O(0)):
            return -1, "fired"
    return 0, ("no_close_confirm" if pat else "no_pattern")


def update_regime(j, st, h4):
    """UpdateMarketRegime. NB: ATR ratio uses the M5 ATR, ADX/BB use H4."""
    k = h4.closed_idx[j]
    a = st["adx"][k] if k >= 0 else np.nan
    if INP["RegimeATR_H4"]:
        if k < 52:
            return None
        atr_now = st["h4_atr"][k - 1]
        atr_hist = st["h4_atr"][k - 51:k - 1]
    else:
        atr_now = st["m5_atr"][j]
        atr_hist = st["m5_atr"][j - 50:j]
    if not np.isfinite(a) or not np.isfinite(atr_now) or len(atr_hist) < 50:
        return None
    atr_mean = np.nanmean(atr_hist)
    ratio = atr_now / atr_mean if atr_mean > 0 else 1.0
    bb_u, bb_l, bb_m = st["bb_u"][k], st["bb_l"][k], st["bb_m"][k]
    bb_w = (bb_u - bb_l) / bb_m if bb_m > 0 else 0.0

    if a > 30 and ratio >= 1.0:
        reg, rr, atr_mult, lotm = "STRONG", INP["MaxRR"], INP["MinATRMult"], 1.0
    elif a > 20:
        reg, rr, atr_mult, lotm = "WEAK", 1.5, 1.3, 1.0
    elif ratio < 0.6 or bb_w < 0.005:
        reg, rr, atr_mult, lotm = "DRY", INP["MinRR"], INP["MaxATRMult"], INP["DryLotFactor"]
    else:
        reg, rr, atr_mult, lotm = "RANGING", 1.0, 2.0, 0.8

    vol = ratio * {"RANGING": 0.85, "DRY": 0.7, "STRONG": 1.3, "WEAK": 1.0}[reg]
    fvg_pips = max(INP["FVGMinPips"] * vol, 2.0)
    return dict(regime=reg, rr=rr, atr_mult=atr_mult, lot_mult=lotm, fvg_pips=fvg_pips)


def calculate_lot(sl_dist, balance, lot_mult):
    if INP["RiskPercent"] <= 0:
        lots = 0.01
    else:
        if sl_dist <= 0:
            return 0.0
        risk_amt = balance * INP["RiskPercent"] / 100.0
        ticks = sl_dist / TICK_SIZE
        lots = risk_amt / (ticks * TICK_VALUE)
    lots *= lot_mult
    min_lot = max(0.01, INP["MinLot"])
    max_lot = min(100.0, INP["MaxLot"])
    if min_lot > max_lot:
        min_lot = max_lot
    lots = np.floor(lots / LOT_STEP) * LOT_STEP
    lots = max(min_lot, min(max_lot, lots))
    return round(lots, 2)


# ---------------------------------------------------------------------------
# Main simulation loop — a faithful OnTick, evaluated at each M5 close
# ---------------------------------------------------------------------------
def manage_position(pos, j, o, hh, ll, events):
    """One bar of exit-checking + ladder advance. Returns (fill, reason) or None.

    Shared by the EA loop and the random-control replay so both are executed by
    exactly the same engine -- a control run through a different executor would
    compare two things at once.
    """
    long = pos["side"] == 1
    if pos["entry_bar"] >= j:
        return None

    # excursions recorded BEFORE the exit test, else a bar that exits never books
    # its own extreme
    fav = (hh[j] - pos["entry"]) if long else (pos["entry"] - ll[j])
    adv = (pos["entry"] - ll[j]) if long else (hh[j] - pos["entry"])
    pos["mfe"] = max(pos["mfe"], fav)
    pos["mae"] = max(pos["mae"], adv)
    pos["peak_pct"] = max(pos["peak_pct"], fav / pos["target"] * 100.0)

    if long:
        if o[j] <= pos["sl"]:
            return o[j] - COST, "stop_gap"
        if pos["tp"] and o[j] >= pos["tp"]:
            return pos["tp"], "tp_gap"
        if ll[j] <= pos["sl"]:
            return pos["sl"] - COST, "stop_loss"
        if pos["tp"] and hh[j] >= pos["tp"]:
            return pos["tp"], "take_profit"
    else:
        if o[j] >= pos["sl"]:
            return o[j] + COST, "stop_gap"
        if pos["tp"] and o[j] <= pos["tp"]:
            return pos["tp"], "tp_gap"
        if hh[j] >= pos["sl"]:
            return pos["sl"] + COST, "stop_loss"
        if pos["tp"] and ll[j] <= pos["tp"]:
            return pos["tp"], "take_profit"

    if not INP["UseProfitLadder"]:
        return None
    prog = pos["peak_pct"]
    if prog <= 0:
        return None
    cur = ((pos["sl"] - pos["entry"]) if long
           else (pos["entry"] - pos["sl"])) / pos["target"] * 100.0
    new, stage = None, None
    if prog >= INP["TrailStartPct"] or pos["tp"] == 0.0:
        new, stage = prog - INP["TrailGapPct"], "trail"
        if INP["RemoveTPOnTrail"] and pos["tp"] != 0.0:
            pos["tp"] = 0.0
            events.append((pos["entry_ts"], "tp_removed", prog))
    elif prog >= INP["LockTriggerPct"]:
        new, stage = INP["LockPct"], "locked"
    elif INP["BETriggerPct"] > 0 and prog >= INP["BETriggerPct"]:
        new, stage = 0.0, "breakeven"
    if new is not None and new > cur + INP["TrailStepPct"]:
        nsl = (pos["entry"] + pos["target"] * new / 100.0 if long
               else pos["entry"] - pos["target"] * new / 100.0)
        if (long and nsl > pos["sl"]) or (not long and nsl < pos["sl"]):
            pos["sl"] = nsl
            pos["stage"] = stage
            events.append((pos["entry_ts"], "sl_moved", new))
    return None


def open_position(side, entry_px, stop, rr, j, ts_j, lot, path, regime):
    tp = entry_px + rr * abs(entry_px - stop) * (1 if side == 1 else -1)
    return dict(side=side, entry=entry_px, sl=stop, sl0=stop, tp=tp, tp0=tp,
                target=abs(tp - entry_px), entry_bar=j, entry_ts=ts_j, lot=lot,
                path=path, regime=regime, peak_pct=0.0, stage="raw",
                mfe=0.0, mae=0.0, rr=rr,
                risk_usd=abs(entry_px - stop) * lot * VALUE_PER_LOT)


def zone_free_stop(price, side, atr, mult):
    """Stop for the A2 ablation, where no zone exists to place one behind.

    Symmetric by construction so the ablation cannot smuggle in a directional
    bias, and `mult` is calibrated once against A0's median structural stop width
    so only entry TIMING differs between the cells.
    """
    if mult <= 0:
        raise ValueError("ZoneStopATRMult must be > 0 for the zone-leg ablation")
    half = mult * atr
    return price - half if side == 1 else price + half


def run(m5, start, end):
    h4 = TF(m5, "4h", 240)
    h1 = TF(m5, "1h", 60)
    m15 = TF(m5, "15min", 15)

    for tf in (h4, h1, m15):
        tf.ema_cache = {p: tf.bars.close.ewm(span=p, adjust=False).mean().to_numpy(float)
                        for p in (INP["MAFast"], INP["MASlow"])}

    bb = h4.bars.close.rolling(20)
    st = dict(
        h4_rsi=rsi(h4.bars.close, INP["RSIPeriod"]).to_numpy(float),
        adx=adx(h4.bars, 14).to_numpy(float),
        bb_m=bb.mean().to_numpy(float),
        bb_u=(bb.mean() + 2 * bb.std(ddof=0)).to_numpy(float),
        bb_l=(bb.mean() - 2 * bb.std(ddof=0)).to_numpy(float),
        m5_atr=wilder(true_range(m5), INP["ATRPeriod"]).to_numpy(float),
        h4_atr=wilder(true_range(h4.bars), INP["ATRPeriod"]).to_numpy(float),
        h1_atr=wilder(true_range(h1.bars), INP["ATRPeriod"]).to_numpy(float),
    )

    # --- double-FVG gap table on H4 closed bars ---------------------------
    g_idx, g_size, g_dir, g_top, g_bot = [], [], [], [], []
    for i in range(2, len(h4.bars)):
        bull = h4.l[i] - h4.h[i - 2]
        bear = h4.l[i - 2] - h4.h[i]
        if bull > 0:
            g_idx.append(i); g_size.append(bull); g_dir.append(1)
            g_top.append(h4.l[i]); g_bot.append(h4.h[i - 2])
        elif bear > 0:
            g_idx.append(i); g_size.append(bear); g_dir.append(-1)
            g_top.append(h4.l[i - 2]); g_bot.append(h4.h[i])
    g_idx = np.array(g_idx, int); g_size = np.array(g_size, float)
    g_dir = np.array(g_dir, int); g_top = np.array(g_top, float)
    g_bot = np.array(g_bot, float)
    h4_atr = wilder(true_range(h4.bars), INP["ATRPeriod"]).to_numpy(float)

    o = m5.open.to_numpy(float); hh = m5.high.to_numpy(float)
    ll = m5.low.to_numpy(float); cc = m5.close.to_numpy(float)
    ts = m5.index
    hours = ((ts.hour + BROKER_UTC_OFFSET) % 24).to_numpy()

    t0 = pd.Timestamp(start, tz="UTC")
    t1 = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)

    funnel = Counter()
    trades, ladder_events, taken = [], [], []
    pos = None
    balance = ARGS.balance
    consumed, armed, cur_k = set(), None, -1
    last_close_bar = -10**9
    zone = dict(high=0.0, low=0.0, type=0)
    dfvg_last_bar = -1

    def close_trade(p, fill, reason, i):
        nonlocal balance
        sign = 1.0 if p["side"] == 1 else -1.0
        pnl = (fill - p["entry"]) * p["lot"] * VALUE_PER_LOT * sign
        balance += pnl
        trades.append(dict(
            entry_ts=p["entry_ts"], exit_ts=ts[i], path=p["path"],
            side="buy" if p["side"] == 1 else "sell", entry=p["entry"], exit=fill,
            sl0=p["sl0"], tp0=p["tp0"], lot=p["lot"], reason=reason,
            regime=p["regime"], bars=i - p["entry_bar"], pnl=pnl,
            risk_usd=p["risk_usd"], r=pnl / p["risk_usd"] if p["risk_usd"] else 0.0,
            max_fav_pct=p["peak_pct"], stage=p["stage"], balance=balance,
            rr=p["rr"], mfe_pts=p["mfe"], mae_pts=p["mae"],
            pts=(fill - p["entry"]) * sign,
            stop_pts=abs(p["entry"] - p["sl0"]),
            mins=(ts[i] - p["entry_ts"]).total_seconds() / 60.0,
            month=p["entry_ts"].strftime("%Y-%m")))

    last_j = -1
    for j in range(60, len(m5)):
        if ts[j] < t0 or ts[j] >= t1:
            continue
        last_j = j
        funnel["bars"] += 1

        # ---- manage / exit ------------------------------------------------
        if pos is not None:
            res = manage_position(pos, j, o, hh, ll, ladder_events)
            if res is not None:
                close_trade(pos, res[0], res[1], j)
                pos = None
                last_close_bar = j
            else:
                funnel["blocked_position_open"] += 1
                continue


        if INP["CooldownMinutes"] > 0 and (j - last_close_bar) * 5 < INP["CooldownMinutes"]:
            funnel["blocked_cooldown"] += 1
            continue

        # ---- regime -------------------------------------------------------
        reg = update_regime(j, st, h4)
        if reg is None:
            funnel["regime_no_data"] += 1
            continue
        rr = INP["DefaultRR"] if INP["UseFixedRR"] else reg["rr"]
        funnel["regime_" + reg["regime"]] += 1
        if INP["SkipRanging"] and reg["regime"] == "RANGING":
            funnel["blocked_ranging"] += 1
            continue

        # ---- double-FVG rescan on a new H4 close ---------------------------
        k = h4.closed_idx[j]
        if INP["UseDoubleFVG"] and k != cur_k:
            cur_k = k
            armed = None
            min_gap = h4_atr[k - 1 if INP["NoRepaint"] else k] * INP["DFVG_MinATR"]
            if np.isfinite(min_gap) and min_gap > 0:
                sel = np.flatnonzero((g_idx >= k - INP["DFVG_Lookback"] + 1)
                                     & (g_idx <= k) & (g_size > min_gap))
                for bi in range(len(sel) - 1, -1, -1):
                    b = sel[bi]; done = False
                    for ai in range(bi - 1, -1, -1):
                        a = sel[ai]
                        if g_idx[b] - g_idx[a] > INP["DFVG_PairWindow"]:
                            break
                        if g_dir[a] != g_dir[b] or g_idx[a] in consumed:
                            continue
                        if k - g_idx[a] > INP["DFVG_MaxAgeBars"]:
                            continue
                        s, e = g_idx[a] + 1, k + 1
                        if g_dir[a] == 1 and s < e and h4.l[s:e].min() <= g_bot[a]:
                            continue
                        if g_dir[a] == -1 and s < e and h4.h[s:e].max() >= g_top[a]:
                            continue
                        armed = (int(g_dir[a]), float(g_top[a]), float(g_bot[a]),
                                 int(g_idx[a]))
                        done = True
                        break
                    if done:
                        break
            if armed is not None:
                funnel["dfvg_armed_scans"] += 1

        # ---- session / spread ---------------------------------------------
        if INP["UseSession"] and not (INP["StartHour"] <= hours[j] < INP["EndHour"]):
            funnel["blocked_session"] += 1
            continue

        signal, path, stop_price = 0, None, None

        # ---- PATH A: double FVG -------------------------------------------
        if INP["UseDoubleFVG"] and armed is not None and j != dfvg_last_bar:
            dfvg_last_bar = j
            d_dir, z_top, z_bot, form_idx = armed
            if k - form_idx > INP["DFVG_MaxAgeBars"]:
                armed = None
            else:
                depth = z_top - z_bot
                trend_ok = True
                if INP["DFVG_RequireTrend"]:
                    trend_ok = get_htf_trend(h4, j, st)[0] == d_dir
                if depth > 0 and trend_ok:
                    if d_dir == 1:
                        lv1 = z_top - depth * INP["DFVG_TapMinPct"] / 100.0
                        lv2 = z_top - depth * INP["DFVG_TapMaxPct"] / 100.0
                        fire = ll[j] <= lv1 and cc[j] >= lv2 and \
                            (cc[j] > o[j] or not INP["DFVG_RequireColor"])
                    else:
                        lv1 = z_bot + depth * INP["DFVG_TapMinPct"] / 100.0
                        lv2 = z_bot + depth * INP["DFVG_TapMaxPct"] / 100.0
                        fire = hh[j] >= lv1 and cc[j] <= lv2 and \
                            (cc[j] < o[j] or not INP["DFVG_RequireColor"])
                    if fire:
                        funnel["dfvg_tap_fired"] += 1
                        signal, path = d_dir, "DFVG"
                        zone["high"], zone["low"] = z_top, z_bot
                        consumed.add(form_idx)
                        armed = None

        # ---- PATH B: legacy chain ------------------------------------------
        if signal == 0 and not INP["DFVG_Only"]:
            # With the trend leg ablated the chain has no direction of its own; the
            # zone supplies it, and the trend/zone agreement check becomes vacuous.
            if INP["NoTrendLeg"]:
                trend = 0
            else:
                trend, why = get_htf_trend(h4, j, st)
                if trend == 0:
                    funnel["legacy_trend_flat"] += 1
            if INP["NoTrendLeg"] or trend != 0:
                if not INP["NoTrendLeg"]:
                    funnel["legacy_trend_ok"] += 1
                min_gap = reg["fvg_pips"] * 10 * POINT
                htf_signal, zone_valid = 0, False
                if INP["NoZoneLeg"]:
                    # No zone: direction stays with the trend, and the agreement
                    # check in check_entry_confirmation is vacuous at htf_signal == 0.
                    htf_signal, zone_valid = 0, True
                    funnel["legacy_zone_ablated"] += 1
                elif INP["ZoneV2"]:
                    if INP["UseFVG"]:
                        htf_signal = detect_fvg_v2(h1, j, min_gap, zone)
                        if htf_signal != 0:
                            zone_valid = True
                            funnel["legacy_fvg_found"] += 1
                            funnel["legacy_fvg_mitigated"] += 1
                    if not zone_valid and INP["UseOB"]:
                        htf_signal = detect_ob_v2(h1, j, zone)
                        if htf_signal != 0:
                            zone_valid = True
                            funnel["legacy_ob_found"] += 1
                            funnel["legacy_ob_mitigated"] += 1
                else:
                    if INP["UseFVG"]:
                        htf_signal = detect_fvg(h1, j, min_gap, zone)
                        if htf_signal != 0:
                            funnel["legacy_fvg_found"] += 1
                            if is_zone_mitigated(h1, j, htf_signal, zone):
                                zone_valid = True
                                funnel["legacy_fvg_mitigated"] += 1
                    if not zone_valid and INP["UseOB"]:
                        htf_signal = detect_ob(h1, j, zone)
                        if htf_signal != 0:
                            funnel["legacy_ob_found"] += 1
                            if is_zone_mitigated(h1, j, htf_signal, zone):
                                zone_valid = True
                                funnel["legacy_ob_mitigated"] += 1

                if INP["NoTrendLeg"]:
                    if INP["NoZoneLeg"]:
                        raise ValueError("NoTrendLeg and NoZoneLeg leaves no direction source")
                    trend = htf_signal          # direction now comes from the zone

                if not zone_valid:
                    funnel["legacy_no_zone"] += 1
                elif not INP["NoMTFLeg"] and not check_mtf_structure(m15, j, trend):
                    funnel["legacy_mtf_fail"] += 1
                else:
                    funnel["legacy_mtf_ok"] += 1
                    s, why2 = check_entry_confirmation(o, hh, ll, cc, j, trend, htf_signal)
                    funnel["legacy_entry_" + why2] += 1
                    if s != 0:
                        signal, path = s, "HTF"

        if signal == 0:
            continue

        # ---- CalculateSLTP -------------------------------------------------
        atr_v = st["m5_atr"][j - 1] if INP["NoRepaint"] else st["m5_atr"][j]
        if not np.isfinite(atr_v):
            continue
        price = cc[j] + COST if signal == 1 else cc[j] - COST
        if INP["MaxZoneDistATR"] > 0 and atr_v > 0:
            ext = (price - zone["high"]) if signal == 1 else (zone["low"] - price)
            if ext > atr_v * INP["MaxZoneDistATR"]:
                funnel["rejected_extended"] += 1
                continue
        # Stop buffer volatility reference. v1 always used the M5 (entry-TF) ATR
        # even though the zone the stop sits behind is an H1/H4 structure, so a
        # swing stop was padded with ~70 minutes of noise. ZoneATR pads with the
        # ATR of the timeframe the zone was found on instead.
        # Legacy path only, matching the EA: the H4 reference was measured WORSE on
        # the double-FVG path (PF 1.06 -> 1.04, peak DD -34% -> -89%).
        if INP["UseATR"] and INP["ZoneATR"] and path != "DFVG":
            buf_atr = st["h1_atr"][h1.closed_idx[j] - 1]
            if not np.isfinite(buf_atr):
                continue
            buf = buf_atr * reg["atr_mult"] * INP["ZoneATRMult"]
        elif INP["UseATR"] and INP["ZoneATR_DFVG"] and path == "DFVG":
            buf_atr = h4_atr[h4.closed_idx[j] - 1]
            if not np.isfinite(buf_atr):
                continue
            buf = buf_atr * reg["atr_mult"] * INP["ZoneATRMult"]
        else:
            buf = atr_v * reg["atr_mult"] if INP["UseATR"] else 50 * POINT
        if INP["NoZoneLeg"]:
            zatr = st["h1_atr"][h1.closed_idx[j] - 1]
            if not np.isfinite(zatr) or zatr <= 0:
                continue
            sl = zone_free_stop(price, signal, zatr, INP["ZoneStopATRMult"])
            tp = price + (price - sl) * rr if signal == 1 else price - (sl - price) * rr
        elif signal == 1:
            sl = zone["low"] - buf
            tp = price + (price - sl) * rr
        else:
            sl = zone["high"] + buf
            tp = price - (sl - price) * rr
        if (signal == 1 and sl >= price) or (signal == -1 and sl <= price):
            funnel["sltp_wrong_side"] += 1
            continue
        dist = abs(price - sl)
        if dist <= 0:
            continue

        lot = calculate_lot(dist, balance, reg["lot_mult"])
        if lot <= 0:
            funnel["lot_zero"] += 1
            continue

        funnel["ENTRY_" + path] += 1
        taken.append(dict(bar=j, side=signal, stop=sl, rr=rr, path=path,
                          regime=reg["regime"]))
        pos = open_position(signal, price, sl, rr, j, ts[j], lot, path,
                            reg["regime"])

    # Mark any position still open at the END OF THE TEST WINDOW, not at the end of
    # the loaded warm-up frame — otherwise the last trade is priced days later.
    if pos is not None and last_j >= 0:
        fill = cc[last_j] - COST if pos["side"] == 1 else cc[last_j] + COST
        close_trade(pos, fill, "open_at_window_end", last_j)

    return pd.DataFrame(trades), funnel, ladder_events, balance, taken


# ---------------------------------------------------------------------------
# Replay engine — executes an explicit signal list through the SAME manager.
# Used for the matched random-entry control, and self-checked against the real
# run so a divergence between the two engines cannot be mistaken for a result.
# ---------------------------------------------------------------------------
def replay(m5, sigs, balance0):
    o = m5.open.to_numpy(float); hh = m5.high.to_numpy(float)
    ll = m5.low.to_numpy(float); cc = m5.close.to_numpy(float)
    ts = m5.index
    by_bar = {}
    for sg in sigs:
        by_bar.setdefault(sg["bar"], []).append(sg)
    lo = min(by_bar) if by_bar else 0
    hi_ = len(m5)

    trades, ev, pos, balance = [], [], None, balance0
    for j in range(lo, hi_):
        if pos is not None:
            res = manage_position(pos, j, o, hh, ll, ev)
            if res is None:
                continue
            sign = 1.0 if pos["side"] == 1 else -1.0
            pnl = (res[0] - pos["entry"]) * pos["lot"] * VALUE_PER_LOT * sign
            balance += pnl
            trades.append(dict(pnl=pnl, r=pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0,
                               reason=res[1], entry_ts=pos["entry_ts"]))
            pos = None
        for sg in by_bar.get(j, []):
            side = sg["side"]
            entry = cc[j] + COST if side == 1 else cc[j] - COST
            dist = (entry - sg["stop"]) if side == 1 else (sg["stop"] - entry)
            if dist <= 0:
                continue
            lot = calculate_lot(dist, balance, sg.get("lot_mult", 1.0))
            pos = open_position(side, entry, sg["stop"], sg["rr"], j, ts[j], lot,
                                sg.get("path", "CTL"), sg.get("regime", "NA"))
            break
    if pos is not None:
        fill = cc[hi_ - 1] - COST if pos["side"] == 1 else cc[hi_ - 1] + COST
        sign = 1.0 if pos["side"] == 1 else -1.0
        pnl = (fill - pos["entry"]) * pos["lot"] * VALUE_PER_LOT * sign
        balance += pnl
        trades.append(dict(pnl=pnl, r=pnl / pos["risk_usd"] if pos["risk_usd"] else 0.0,
                           reason="open_at_window_end", entry_ts=pos["entry_ts"]))
    return pd.DataFrame(trades)


def random_control(m5, real, taken, trials, t0, t1, seed=11):
    """Same trade count, same side mix, stop distances and RRs resampled from the
    real trades, entry bars drawn uniformly from the SAME window."""
    if len(real) == 0 or trials <= 0:
        return None
    rng = np.random.default_rng(seed)
    ts = m5.index
    ok = np.flatnonzero((ts >= t0) & (ts < t1))
    lo, hi_ = int(ok[0]), int(ok[-1]) - 5
    n = len(real)
    sides = np.where(real.side.to_numpy() == "buy", 1, -1)
    dists = np.abs(real.entry.to_numpy(float) - real.sl0.to_numpy(float))
    rrs = real.rr.to_numpy(float)
    cc = m5.close.to_numpy(float)

    out = []
    for _ in range(trials):
        bars = np.sort(rng.integers(lo, hi_, n))
        sd = rng.permutation(sides)
        dd = rng.choice(dists, n, replace=True)
        rr = rng.choice(rrs, n, replace=True)
        sigs = []
        for b, s_, d_, r_ in zip(bars, sd, dd, rr):
            stop = cc[b] - d_ if s_ == 1 else cc[b] + d_
            sigs.append(dict(bar=int(b), side=int(s_), stop=float(stop), rr=float(r_)))
        t = replay(m5, sigs, ARGS.balance)
        if len(t) == 0:
            continue
        l_ = -t[t.pnl < 0].pnl.sum()
        out.append((t[t.pnl > 0].pnl.sum() / l_ if l_ > 0 else 10.0, t.pnl.sum()))
    if not out:
        return None
    pf = np.array([x[0] for x in out]); net = np.array([x[1] for x in out])
    return dict(pf=pf, net=net)


# ---------------------------------------------------------------------------
def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-01")
    ap.add_argument("--end", default="2026-07-31")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--min-lot", type=float, default=0.02)
    ap.add_argument("--max-lot", type=float, default=1.00)
    ap.add_argument("--be-pct", type=float, default=None,
                    help="breakeven trigger as %% of target (0 disables)")
    ap.add_argument("--fixed-rr", action="store_true",
                    help="force InpDefaultRR instead of the regime RR table")
    ap.add_argument("--entry-shift", type=int, default=None,
                    help="0 = M5 trigger on the just-closed bar (immediate)")
    ap.add_argument("--regime-atr-m5", action="store_true",
                    help="keep the old M5 ATR for regime classification")
    ap.add_argument("--repaint", action="store_true",
                    help="revert to reading the forming bar (pre-fix behaviour)")
    ap.add_argument("--rsi-bull", type=float, default=None)
    ap.add_argument("--rsi-bear", type=float, default=None)
    ap.add_argument("--zone-dist", type=float, default=None)
    ap.add_argument("--cooldown", type=int, default=None)
    ap.add_argument("--lock-pct", type=float, default=None)
    ap.add_argument("--lock-trigger", type=float, default=None)
    ap.add_argument("--min-atr", type=float, default=None)
    ap.add_argument("--pair-window", type=int, default=None)
    ap.add_argument("--tap-min", type=float, default=None)
    ap.add_argument("--tap-max", type=float, default=None)
    ap.add_argument("--zone-v2", action="store_true",
                    help="legacy zone leg: full-lookback scan + unfilled + touched")
    ap.add_argument("--no-trend-leg", action="store_true",
                    help="ablation A1: drop the H4 trend leg, take direction from the zone")
    ap.add_argument("--no-zone-leg", action="store_true",
                    help="ablation A2: drop the H1 zone leg; stop comes from --zone-stop-atr-mult")
    ap.add_argument("--zone-stop-atr-mult", type=float, default=0.0,
                    help="H1-ATR multiple for the A2 stop, calibrated from A0")
    ap.add_argument("--no-mtf-leg", action="store_true",
                    help="ablation A3: drop the M15 structure leg")
    ap.add_argument("--no-pattern-leg", action="store_true",
                    help="ablation A4: drop the M5 candlestick pattern")
    ap.add_argument("--no-confirm", action="store_true",
                    help="ablation A5 (with --no-pattern-leg): drop the close-direction confirm")
    ap.add_argument("--skip-ranging", action="store_true",
                    help="ablation A6: block entries while the regime is RANGING")
    ap.add_argument("--fvg-lookback", type=int, default=None,
                    help="H1 bars scanned by the v2 FVG detector")
    ap.add_argument("--zone-atr", action="store_true",
                    help="legacy path: pad the stop with the H1 (zone TF) ATR")
    ap.add_argument("--zone-atr-dfvg", action="store_true",
                    help="double-FVG path: pad the stop with the H4 ATR")
    ap.add_argument("--zone-atr-mult", type=float, default=1.0,
                    help="extra scale on the zone-ATR buffer (compare stop widths)")
    ap.add_argument("--no-session", action="store_true")
    ap.add_argument("--no-colour", action="store_true")
    ap.add_argument("--max-age", type=int, default=None)
    ap.add_argument("--label", default="")
    ap.add_argument("--dfvg-only", action="store_true",
                    help="InpDFVG_Only: disable the legacy chain")
    ap.add_argument("--no-dfvg", action="store_true",
                    help="InpUseDoubleFVG=false: legacy chain only. Needed to A/B a "
                         "legacy-path change -- with both paths live they compete for "
                         "the single position slot and each change moves BOTH counts.")
    ap.add_argument("--control", type=int, default=0,
                    help="matched random-entry control trials")
    ap.add_argument("--no-ladder", action="store_true",
                    help="disable the profit ladder (plain fixed TP) for an A/B")
    ARGS = ap.parse_args()
    INP["MinLot"], INP["MaxLot"] = ARGS.min_lot, ARGS.max_lot
    if ARGS.entry_shift is not None:
        INP["EntryShift"] = ARGS.entry_shift
    if ARGS.regime_atr_m5:
        INP["RegimeATR_H4"] = False
    if ARGS.repaint:
        INP["NoRepaint"] = False
    if ARGS.no_ladder:
        INP["UseProfitLadder"] = False
    if ARGS.be_pct is not None:
        INP["BETriggerPct"] = ARGS.be_pct
    if ARGS.fixed_rr:
        INP["UseFixedRR"] = True
    if ARGS.dfvg_only:
        INP["DFVG_Only"] = True
    if ARGS.no_dfvg:
        INP["UseDoubleFVG"] = False
    for flag, key in (("rsi_bull", "RSIBull"), ("rsi_bear", "RSIBear"),
                      ("zone_dist", "MaxZoneDistATR"), ("cooldown", "CooldownMinutes"),
                      ("lock_pct", "LockPct"), ("lock_trigger", "LockTriggerPct"),
                      ("min_atr", "DFVG_MinATR"), ("pair_window", "DFVG_PairWindow"),
                      ("tap_min", "DFVG_TapMinPct"), ("tap_max", "DFVG_TapMaxPct"),
                      ("max_age", "DFVG_MaxAgeBars")):
        v = getattr(ARGS, flag)
        if v is not None:
            INP[key] = v
    if ARGS.zone_v2:
        INP["ZoneV2"] = True
    for flag, key in (("no_trend_leg", "NoTrendLeg"), ("no_mtf_leg", "NoMTFLeg"),
                      ("no_pattern_leg", "NoPatternLeg"), ("no_confirm", "NoConfirm"),
                      ("skip_ranging", "SkipRanging")):
        if getattr(ARGS, flag):
            INP[key] = True
    if ARGS.no_zone_leg:
        INP["NoZoneLeg"] = True
    INP["ZoneStopATRMult"] = ARGS.zone_stop_atr_mult
    if ARGS.fvg_lookback is not None:
        INP["FVGLookback"] = ARGS.fvg_lookback
    if ARGS.zone_atr:
        INP["ZoneATR"] = True
    if ARGS.zone_atr_dfvg:
        INP["ZoneATR_DFVG"] = True
    INP["ZoneATRMult"] = ARGS.zone_atr_mult
    if ARGS.no_session:
        INP["UseSession"] = False
    if ARGS.no_colour:
        INP["DFVG_RequireColor"] = False

    global REPORT
    tag = (f"{ARGS.start}_{ARGS.end}" + ("_noladder" if ARGS.no_ladder else "")
           + ("_fixedrr" if ARGS.fixed_rr else "_regimerr")
           + f"_be{INP['BETriggerPct']:g}" + ("_dfvgonly" if ARGS.dfvg_only else "")
           + (f"_{ARGS.label}" if ARGS.label else ""))
    REPORT = PROJECT_ROOT / f"reports/goldhtf_ea_sim_{tag}.md"
    df = pd.read_csv(CSV, parse_dates=["timestamp"], index_col="timestamp")
    df = df[~((df.high == df.low) & (df.volume == 0))]
    warm = pd.Timestamp(ARGS.start, tz="UTC") - pd.Timedelta(days=45)
    m5 = df[df.index >= warm]

    trades, funnel, ladder, bal, taken = run(m5, ARGS.start, ARGS.end)

    L = []
    def say(s=""):
        print(s); L.append(s)

    say("# GoldHTF_AutoOpt_EA v3.00 — July 2026 simulation")
    say()
    say(f"XAUUSD 5m, {ARGS.start} .. {ARGS.end}. Lot band "
        f"{ARGS.min_lot}-{ARGS.max_lot}, risk {INP['RiskPercent']}%/trade, "
        f"start balance ${ARGS.balance:,.0f}, strict cost ${COST}/side/point.")
    say()
    say("## Stage funnel — which code actually ran")
    say()
    say("| Stage | Count |")
    say("|---|---|")
    for kk in ["bars", "blocked_position_open", "blocked_session",
               "regime_STRONG", "regime_WEAK", "regime_RANGING", "regime_DRY",
               "regime_no_data", "dfvg_armed_scans", "dfvg_tap_fired",
               "legacy_trend_ok", "legacy_trend_flat", "legacy_fvg_found",
               "legacy_fvg_mitigated", "legacy_ob_found", "legacy_ob_mitigated",
               "legacy_no_zone", "legacy_mtf_fail", "legacy_mtf_ok",
               "legacy_entry_no_pattern", "legacy_entry_no_close_confirm",
               "legacy_entry_trend_vs_zone_mismatch", "legacy_entry_fired",
               "sltp_wrong_side", "lot_zero", "rejected_extended", "ENTRY_DFVG", "ENTRY_HTF"]:
        say(f"| `{kk}` | {funnel.get(kk, 0):,} |")
    say()

    if len(trades) == 0:
        say("## No trades. See funnel above for where the chain stopped.")
    else:
        t = trades
        wins = t[t.pnl > 0]
        gl = -t[t.pnl < 0].pnl.sum()
        pf = t[t.pnl > 0].pnl.sum() / gl if gl > 0 else float("inf")
        eq = ARGS.balance + t.pnl.cumsum()
        dd = float(((eq - eq.cummax()) / ARGS.balance * 100).min())
        say("## Result")
        say()
        say(f"* trades **{len(t)}**  |  win rate **{100*len(wins)/len(t):.1f}%**  |  "
            f"PF **{pf:.2f}**")
        say(f"* net **${t.pnl.sum():+,.2f}** on ${ARGS.balance:,.0f} "
            f"(**{100*t.pnl.sum()/ARGS.balance:+.2f}%**), ending ${bal:,.2f}")
        say(f"* max drawdown **{dd:.2f}%**  |  avg trade ${t.pnl.mean():+,.2f}  |  "
            f"expectancy **{t.r.mean():+.2f}R**")
        say(f"* risk per trade ${t.risk_usd.min():,.0f}-${t.risk_usd.max():,.0f} "
            f"(median ${t.risk_usd.median():,.0f})")
        say()
        say("### By entry path")
        say()
        say("| Path | n | WR | PF | net |")
        say("|---|---|---|---|---|")
        for p, g in t.groupby("path"):
            g_l = -g[g.pnl < 0].pnl.sum()
            g_pf = g[g.pnl > 0].pnl.sum() / g_l if g_l > 0 else float("inf")
            say(f"| {p} | {len(g)} | {100*(g.pnl>0).mean():.0f}% | {g_pf:.2f} | "
                f"${g.pnl.sum():+,.2f} |")
        say()
        say("### Exit reasons")
        say()
        for r_, g in t.groupby("reason"):
            say(f"* `{r_}` x{len(g)} — net ${g.pnl.sum():+,.2f}")
        say()
        say("### Ladder stages reached")
        say()
        for s_, g in t.groupby("stage"):
            say(f"* `{s_}` x{len(g)} — net ${g.pnl.sum():+,.2f}, "
                f"avg peak {g.max_fav_pct.mean():.0f}% of target")
        say()
        say("### Per-month breakdown")
        say()
        say("| Month | Trades | Wins | Losses | WR | PF | Net $ | Net pts | "
            "Best $ | Worst $ | Avg MFE pts | Avg MAE pts | Avg hold |")
        say("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for mth, g in t.groupby("month"):
            gl = -g[g.pnl < 0].pnl.sum()
            gpf = g[g.pnl > 0].pnl.sum() / gl if gl > 0 else float("inf")
            hrs = g.mins.mean() / 60.0
            say(f"| {mth} | {len(g)} | {(g.pnl>0).sum()} | {(g.pnl<=0).sum()} | "
                f"{100*(g.pnl>0).mean():.0f}% | "
                f"{gpf:.2f} | ${g.pnl.sum():+,.2f} | {g.pts.sum():+.1f} | "
                f"${g.pnl.max():+,.2f} | ${g.pnl.min():+,.2f} | "
                f"{g.mfe_pts.mean():.1f} | {g.mae_pts.mean():.1f} | {hrs:.1f}h |")
        say()
        say("*pts = gold price points ($1 move). MFE = furthest the trade went in "
            "your favour before exiting; MAE = furthest against you.*")
        say()
        say("### Wins vs losses")
        say()
        w, lo_ = t[t.pnl > 0], t[t.pnl <= 0]
        for lbl, g in (("WINS", w), ("LOSSES", lo_)):
            if not len(g):
                continue
            say(f"**{lbl}** — n={len(g)}, avg ${g.pnl.mean():+,.2f} "
                f"({g.r.mean():+.2f}R), avg {g.pts.mean():+.1f} pts, "
                f"avg hold {g.mins.mean()/60:.1f}h, "
                f"avg MFE {g.mfe_pts.mean():.1f} pts, avg MAE {g.mae_pts.mean():.1f} pts")
        say()
        say(f"Biggest win  ${t.pnl.max():+,.2f} ({t.loc[t.pnl.idxmax()].pts:+.1f} pts, "
            f"peak {t.loc[t.pnl.idxmax()].max_fav_pct:.0f}% of target)")
        say(f"Biggest loss ${t.pnl.min():+,.2f} ({t.loc[t.pnl.idxmin()].pts:+.1f} pts, "
            f"peak {t.loc[t.pnl.idxmin()].max_fav_pct:.0f}% of target)")
        say()
        say("### Trade list")
        say()
        say("| # | entry | path | side | regime | RR | lot | entry px | SL | stop pts "
            "| TP | exit | MFE pts | MAE pts | peak% | pts | hold | R | P&L |")
        say("|" + "---|" * 19)
        for i, (_, r_) in enumerate(t.iterrows(), 1):
            say(f"| {i} | {r_.entry_ts:%m-%d %H:%M} | {r_.path} | {r_.side} | "
                f"{r_.regime} | {r_.rr:.1f} | {r_.lot:.2f} | {r_.entry:.2f} | "
                f"{r_.sl0:.2f} | {r_.stop_pts:.1f} | {r_.tp0:.2f} | {r_.reason} | "
                f"{r_.mfe_pts:.1f} | {r_.mae_pts:.1f} | {r_.max_fav_pct:.0f}% | "
                f"{r_.pts:+.1f} | {r_.mins/60:.1f}h | {r_.r:+.2f} | ${r_.pnl:+,.2f} |")
        say()
        trades.to_csv(str(REPORT).replace(".md", "_trades.csv"), index=False)

    if ARGS.control and len(trades):
        t0 = pd.Timestamp(ARGS.start, tz="UTC")
        t1 = pd.Timestamp(ARGS.end, tz="UTC") + pd.Timedelta(days=1)
        ctl = random_control(m5, trades, taken, ARGS.control, t0, t1)
        say("### Matched random-entry control")
        say()
        if ctl is None:
            say("Control produced no trades.")
        else:
            gl = -trades[trades.pnl < 0].pnl.sum()
            rpf = trades[trades.pnl > 0].pnl.sum() / gl if gl > 0 else float("inf")
            beat_pf = 100.0 * (ctl["pf"] < (rpf if np.isfinite(rpf) else 10)).mean()
            beat_net = 100.0 * (ctl["net"] < trades.pnl.sum()).mean()
            say(f"Same trade count ({len(trades)}), same side mix, stop distances and "
                f"RRs resampled from the real trades, entry bars drawn uniformly from "
                f"the same window, executed by the same engine.")
            say()
            say(f"* real: PF **{rpf:.2f}**, net **${trades.pnl.sum():+,.2f}**")
            say(f"* null over {ARGS.control} trials: PF median {np.median(ctl['pf']):.2f}, "
                f"p95 {np.percentile(ctl['pf'], 95):.2f}; "
                f"net median ${np.median(ctl['net']):+,.2f}, "
                f"p95 ${np.percentile(ctl['net'], 95):+,.2f}")
            say(f"* real beats **{beat_pf:.0f}%** of draws on PF, "
                f"**{beat_net:.0f}%** on net")
        say()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n")
    print(f"\nwrote {REPORT}")


if __name__ == "__main__":
    main()
