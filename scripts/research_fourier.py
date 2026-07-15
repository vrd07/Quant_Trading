#!/usr/bin/env python3
"""
Fourier-transform edge research — XAUUSD (15m primary, 1H secondary).

Question: can the FFT predict price structure, or help an existing strategy?

Three testable uses (each with a different null):
  A. EXTRAPOLATION — rolling window W, linear detrend, rFFT, keep top-K
     harmonics, extrapolate H bars ahead. If markets have stable periodic
     structure the predicted move correlates with the realized move (IC > 0)
     and trading its sign is profitable. This is the literal "predict the
     structure" claim.
  B. DOMINANT-CYCLE PHASE (Ehlers-style) — find the strongest cycle in a
     period band, gate on how much variance it explains (power fraction),
     buy near the trough phase / sell near the peak, exit after half a cycle.
  C. SPECTRAL SHAPE AS A REGIME FEATURE — spectral entropy / low-frequency
     power fraction of the detrended window vs FUTURE trendiness (forward
     efficiency ratio). Not a signal — a potential gate for existing strats.

Harness: same conventions as the other research scripts — signal at bar t
fills at open of t+1, cost in points per side (0.20 base, 0.50 strict check),
2025 (Feb–Dec) and 2026 (Jan–Jul) reported separately as walk-forward slices.

Writes: reports/fourier_research.md
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_CSV = PROJECT_ROOT / "data/historical/XAUUSD_5m_real.csv"
REPORT = PROJECT_ROOT / "reports/fourier_research.md"
COST = 0.20          # points per side (base); strict check uses 0.50
STRICT_COST = 0.50
YEARS = {"2025": ("2025-02-01", "2026-01-01"),
         "2026": ("2026-01-01", "2026-07-15")}

OUT = []


def say(s=""):
    print(s)
    OUT.append(s)


def load_bars(tf_min: int) -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, parse_dates=["timestamp"], index_col="timestamp")
    bars = (df.resample(f"{tf_min}min", label="left", closed="left")
            .agg({"open": "first", "high": "max", "low": "min",
                  "close": "last", "volume": "sum"})
            .dropna(subset=["open", "high", "low", "close"]))
    return bars


def atr_series(bars: pd.DataFrame, period=14) -> np.ndarray:
    h, l, c = bars["high"].to_numpy(float), bars["low"].to_numpy(float), bars["close"].to_numpy(float)
    pc = np.roll(c, 1)
    pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(period).mean().to_numpy()


def rolling_fft(close: np.ndarray, W: int):
    """Rolling linear-detrended rFFT. Row r covers bars [r, r+W); 'now' = r+W-1.

    Returns (F, slope): F complex (nrows, W//2+1), slope per row (trend pts/bar).
    """
    X = sliding_window_view(close, W)                      # (nrows, W)
    t = np.arange(W, dtype=float)
    tm, tv = t.mean(), ((t - t.mean()) ** 2).sum()
    xm = X.mean(axis=1, keepdims=True)
    slope = ((X - xm) * (t - tm)).sum(axis=1) / tv
    detr = X - xm - slope[:, None] * (t - tm)
    F = np.fft.rfft(detr, axis=1)
    return F, slope


def extrapolate_delta(F: np.ndarray, W: int, K: int, H: int) -> np.ndarray:
    """Predicted CYCLE move over the next H bars from the top-K harmonics.

    For harmonic k: x_k(t) = (2/W)|F_k| cos(2πkt/W + φ_k). Delta = Σ_k
    [x_k(W-1+H) − x_k(W-1)]. Trend contribution is handled by the caller.
    """
    nrows, nf = F.shape
    mag = np.abs(F)
    mag[:, 0] = 0.0                                        # DC out
    if nf > 1:
        mag[:, -1] = 0.0                                   # Nyquist out
    top = np.argpartition(mag, -K, axis=1)[:, -K:]         # (nrows, K)
    rows = np.arange(nrows)[:, None]
    Fk = F[rows, top]
    amp = 2.0 * np.abs(Fk) / W
    ph = np.angle(Fk)
    w = 2.0 * np.pi * top / W
    t1, t2 = W - 1.0, W - 1.0 + H
    return (amp * (np.cos(w * t2 + ph) - np.cos(w * t1 + ph))).sum(axis=1)


def dominant_cycle(F: np.ndarray, W: int, pmin: int, pmax: int):
    """Dominant cycle in period band [pmin, pmax] bars.

    Returns (period, power_frac, phase_now, cyc_val, cyc_slope):
      power_frac — dominant bin's share of total detrended power,
      cyc_val    — cos(phase at bar W-1) in [-1, 1] (+1 peak, −1 trough),
      cyc_slope  — d/dt of the cycle at 'now' (sign: rising/falling).
    """
    nrows, nf = F.shape
    power = np.abs(F) ** 2
    power[:, 0] = 0.0
    k_lo = max(1, int(np.ceil(W / pmax)))
    k_hi = min(nf - 1, int(np.floor(W / pmin)))
    band = power[:, k_lo:k_hi + 1]
    kdom = band.argmax(axis=1) + k_lo
    rows = np.arange(nrows)
    p_dom = power[rows, kdom]
    p_tot = power[:, 1:].sum(axis=1)
    frac = np.divide(p_dom, p_tot, out=np.zeros(nrows), where=p_tot > 0)
    Fk = F[rows, kdom]
    w = 2.0 * np.pi * kdom / W
    theta = w * (W - 1) + np.angle(Fk)
    cyc_val = np.cos(theta)
    cyc_slope = -np.sin(theta)                             # ∝ d/dt cos(wt+φ)
    period = W / kdom.astype(float)
    return period, frac, cyc_val, cyc_slope


def sim_time_exit(bars: pd.DataFrame, entries, cost: float):
    """entries: list of (bar_idx, dir, hold_bars). Fill open[i+1], exit
    open[i+1+hold]. One position at a time. Returns per-trade DataFrame (pts).
    """
    o = bars["open"].to_numpy(float)
    n = len(bars)
    ts = bars.index
    out, busy_until = [], -1
    for i, d, hold in entries:
        if i <= busy_until:
            continue
        e, x = i + 1, i + 1 + hold
        if x >= n:
            continue
        pnl = d * (o[x] - o[e]) - 2 * cost
        out.append({"entry_ts": ts[e], "exit_ts": ts[x], "dir": d, "pnl": pnl})
        busy_until = x - 1
    return pd.DataFrame(out)


def pstats(t: pd.DataFrame) -> str:
    if len(t) == 0:
        return "n=0"
    wins, losses = t[t.pnl > 0], t[t.pnl < 0]
    gw, gl = wins.pnl.sum(), -losses.pnl.sum()
    pf = gw / gl if gl > 0 else float("inf")
    tstat = t.pnl.mean() / (t.pnl.std(ddof=1) / np.sqrt(len(t))) if len(t) > 1 and t.pnl.std() > 0 else 0.0
    pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
    return (f"n={len(t):>4}  wr={100 * len(wins) / len(t):5.1f}%  PF={pfs:>5}  "
            f"net={t.pnl.sum():>+9.1f}pts  exp={t.pnl.mean():>+6.2f}  t={tstat:>+5.2f}")


def year_mask(index, label):
    lo = pd.Timestamp(YEARS[label][0], tz=index.tz)
    hi = pd.Timestamp(YEARS[label][1], tz=index.tz)
    return (index >= lo) & (index < hi)


def spearman(a, b):
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    ra = (ra - ra.mean()) / ra.std()
    rb = (rb - rb.mean()) / rb.std()
    return float((ra * rb).mean())


# ---------------------------------------------------------------------------
# A. Fourier extrapolation
# ---------------------------------------------------------------------------

def run_extrapolation(bars, tf_label):
    close = bars["close"].to_numpy(float)
    atr = atr_series(bars)
    n = len(bars)
    say(f"\n### A. Fourier extrapolation — {tf_label}")
    say("IC = Spearman(predicted H-bar move, realized H-bar move), per slice.")
    say("")
    say("| W | K | H | trend | IC 2025 | IC 2026 | sign-hit 2025 | sign-hit 2026 |")
    say("|---|---|---|-------|---------|---------|---------------|---------------|")
    best = []
    for W in (128, 256, 512):
        F, slope = rolling_fft(close, W)
        now_idx = np.arange(W - 1, n)                      # bar index of each row
        for K in (3, 5):
            for H in (8, 16, 32):
                cyc = extrapolate_delta(F, W, K, H)
                for with_trend in (False, True):
                    pred = cyc + (slope * H if with_trend else 0.0)
                    valid = now_idx + H < n
                    idx = now_idx[valid]
                    p = pred[valid]
                    real = close[idx + H] - close[idx]
                    row_ic, row_hit = {}, {}
                    for yl in YEARS:
                        m = year_mask(bars.index[idx], yl)
                        if m.sum() < 100:
                            row_ic[yl], row_hit[yl] = np.nan, np.nan
                            continue
                        row_ic[yl] = spearman(p[m], real[m])
                        nz = (p[m] != 0) & (real[m] != 0)
                        row_hit[yl] = float((np.sign(p[m][nz]) == np.sign(real[m][nz])).mean())
                    say(f"| {W} | {K} | {H} | {'Y' if with_trend else 'n'} "
                        f"| {row_ic['2025']:+.3f} | {row_ic['2026']:+.3f} "
                        f"| {row_hit['2025']:.3f} | {row_hit['2026']:.3f} |")
                    best.append((min(row_ic['2025'], row_ic['2026']), W, K, H, with_trend))
    best.sort(reverse=True)
    say("")
    say(f"Best both-slice cell (by min-slice IC): W={best[0][1]} K={best[0][2]} "
        f"H={best[0][3]} trend={best[0][4]} (min IC {best[0][0]:+.3f})")

    # Trade the best cell: sign of prediction, threshold on ATR, hold H bars.
    _, W, K, H, with_trend = best[0]
    F, slope = rolling_fft(close, W)
    cyc = extrapolate_delta(F, W, K, H)
    pred = cyc + (slope * H if with_trend else 0.0)
    now_idx = np.arange(W - 1, n)
    say(f"\nTrading the best cell (hold {H} bars, one position, cost {COST}/side):")
    for thr in (0.0, 0.25, 0.5):
        entries = []
        for r, i in enumerate(now_idx):
            a = atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            if pred[r] > thr * a:
                entries.append((i, +1, H))
            elif pred[r] < -thr * a:
                entries.append((i, -1, H))
        t = sim_time_exit(bars, entries, COST)
        if len(t) == 0:
            say(f"  thr={thr:.2f}ATR: no trades")
            continue
        for yl in YEARS:
            sub = t[year_mask(pd.DatetimeIndex(t.entry_ts), yl)]
            say(f"  thr={thr:.2f}ATR {yl}: {pstats(sub)}")
    return best[0]


# ---------------------------------------------------------------------------
# B. Dominant-cycle phase trading
# ---------------------------------------------------------------------------

def run_cycle_phase(bars, tf_label, W=256, pmin=16, pmax=128):
    close = bars["close"].to_numpy(float)
    n = len(bars)
    F, _ = rolling_fft(close, W)
    period, frac, cyc_val, cyc_slope = dominant_cycle(F, W, pmin, pmax)
    now_idx = np.arange(W - 1, n)
    say(f"\n### B. Dominant-cycle phase — {tf_label} (W={W}, band {pmin}-{pmax} bars)")
    say(f"Power-fraction distribution: median {np.median(frac):.3f}, "
        f"p75 {np.percentile(frac, 75):.3f}, p90 {np.percentile(frac, 90):.3f}")
    say("Entry: cyc at trough (cos<−0.8, turning up) → BUY / peak → SELL; "
        "hold half the dominant period. Gate on power fraction q.")
    for q in (0.0, 0.15, 0.25, 0.35):
        entries = []
        for r, i in enumerate(now_idx):
            if frac[r] < q:
                continue
            hold = max(2, int(round(period[r] / 2)))
            if cyc_val[r] < -0.8 and cyc_slope[r] > 0:
                entries.append((i, +1, hold))
            elif cyc_val[r] > 0.8 and cyc_slope[r] < 0:
                entries.append((i, -1, hold))
        t = sim_time_exit(bars, entries, COST)
        say(f"\n  gate power_frac ≥ {q:.2f}: {len(t)} trades")
        if len(t) == 0:
            continue
        for yl in YEARS:
            sub = t[year_mask(pd.DatetimeIndex(t.entry_ts), yl)]
            say(f"    {yl}: {pstats(sub)}")


# ---------------------------------------------------------------------------
# C. Spectral shape as regime feature
# ---------------------------------------------------------------------------

def run_regime_feature(bars, tf_label, W=256, fwd=32):
    close = bars["close"].to_numpy(float)
    n = len(bars)
    F, _ = rolling_fft(close, W)
    power = np.abs(F) ** 2
    power[:, 0] = 0.0
    tot = power[:, 1:].sum(axis=1)
    p = power[:, 1:] / np.maximum(tot[:, None], 1e-12)
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = -np.nansum(np.where(p > 0, p * np.log(p), 0.0), axis=1) / np.log(p.shape[1])
    k_lo = 1
    k_slow = max(2, int(np.ceil(W / 64)))                  # periods ≥ 64 bars
    lowfrac = power[:, k_lo:k_slow + 1].sum(axis=1) / np.maximum(tot, 1e-12)

    # forward efficiency ratio over next `fwd` bars: |net move| / Σ|bar moves|
    dc = np.abs(np.diff(close))
    now_idx = np.arange(W - 1, n)
    valid = now_idx + fwd < n
    idx = now_idx[valid]
    net = np.abs(close[idx + fwd] - close[idx])
    denom = np.array([dc[i:i + fwd].sum() for i in idx])
    er = np.divide(net, denom, out=np.zeros_like(net), where=denom > 0)

    say(f"\n### C. Spectral shape vs future trendiness — {tf_label} (W={W}, fwd={fwd} bars)")
    say("| feature | Spearman vs fwd ER 2025 | 2026 |")
    say("|---------|------------------------|------|")
    for name, feat in (("spectral entropy", ent[valid]), ("low-freq power frac", lowfrac[valid])):
        row = {}
        for yl in YEARS:
            m = year_mask(bars.index[idx], yl)
            row[yl] = spearman(feat[m], er[m]) if m.sum() > 100 else np.nan
        say(f"| {name} | {row['2025']:+.3f} | {row['2026']:+.3f} |")

    # sanity anchor: does PAST trendiness predict future trendiness at all?
    past_net = np.abs(close[idx] - close[idx - fwd])
    past_den = np.array([dc[i - fwd:i].sum() for i in idx])
    past_er = np.divide(past_net, past_den, out=np.zeros_like(past_net), where=past_den > 0)
    row = {}
    for yl in YEARS:
        m = year_mask(bars.index[idx], yl)
        row[yl] = spearman(past_er[m], er[m]) if m.sum() > 100 else np.nan
    say(f"| (anchor: past ER) | {row['2025']:+.3f} | {row['2026']:+.3f} |")


def main():
    say("# Fourier transform edge research — XAUUSD")
    say(f"\nData: {DATA_CSV.name}, slices 2025 (Feb–Dec) / 2026 (Jan–Jul). "
        f"Cost {COST} pts/side (strict {STRICT_COST}). Fills: next-bar open.")

    for tf, label in ((15, "15m"), (60, "1H")):
        bars = load_bars(tf)
        say(f"\n\n## Timeframe {label} — {len(bars)} bars")
        run_extrapolation(bars, label)
        run_cycle_phase(bars, label)
        run_regime_feature(bars, label)

    REPORT.write_text("\n".join(OUT) + "\n")
    print(f"\nreport -> {REPORT}")


if __name__ == "__main__":
    main()
