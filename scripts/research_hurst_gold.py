"""
research_hurst_gold.py — Stage 1: does the Hurst exponent actually separate
trending vs mean-reverting regimes on daily gold?

Physics: fractional Brownian motion (Mandelbrot). For a series X(t),
  E[|X(t+lag) - X(t)|] ∝ lag^H.
H = 0.5 → ordinary Brownian motion (random walk, no edge).
H > 0.5 → persistent / super-diffusive (trends continue).
H < 0.5 → anti-persistent / sub-diffusive (moves revert).

Before building anything we test the PREMISE: bucket days by their trailing
Hurst and measure the forward-return autocorrelation in each bucket. If the
physics is real, high-H days should show positive momentum (continuation) and
low-H days negative (reversion). If both buckets look like ~0 (random walk),
there is no edge and we stop.

Data: SPDR GLD daily close (proxy for XAUUSD direction), history to 2004.
Cached to data/historical/GLD_daily.csv on first run.
"""
import sys
import io
import zipfile
import datetime as dt
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "historical" / "GLD_daily.csv"
_URL = ("https://api.spdrgoldshares.com/api/v1/historical-archive"
        "?product=gld&exchange=NYSE&lang=en")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_UA = {"User-Agent": "Mozilla/5.0"}


def load_gld() -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_csv(CACHE, parse_dates=["date"])
        return df.set_index("date")
    raw = urllib.request.urlopen(
        urllib.request.Request(_URL, headers=_UA), timeout=60).read()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    si = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings = ["".join(t.text or "" for t in s.iter(f"{_NS}t"))
               for s in si.findall(f"{_NS}si")]
    sheets = [n for n in zf.namelist()
              if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
    root = ET.fromstring(max((zf.read(n) for n in sheets), key=len))
    rows = []
    for row in root.iter(f"{_NS}row"):
        cells = {}
        for c in row.findall(f"{_NS}c"):
            col = c.get("r", "").rstrip("0123456789")
            v = c.find(f"{_NS}v")
            if v is None:
                continue
            cells[col] = strings[int(v.text)] if c.get("t") == "s" else v.text
        a, b = cells.get("A"), cells.get("B")
        if not a or b in (None, "US Holiday"):
            continue
        try:
            d = dt.datetime.strptime(a, "%d-%b-%Y").date()
            rows.append((d, float(b)))
        except (ValueError, TypeError):
            continue
    rows.sort(key=lambda x: x[0])
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df.to_csv(CACHE, index=False)
    print(f"  cached {len(df)} daily bars → {CACHE}")
    return df.set_index("date")


def hurst(ts: np.ndarray, max_lag: int = 20) -> float:
    """Structure-function Hurst estimator on a price-level window.
    Returns slope of log(std of lagged diffs) vs log(lag)."""
    ts = np.asarray(ts, dtype=float)
    lags = np.arange(2, max_lag)
    tau = []
    for lag in lags:
        d = ts[lag:] - ts[:-lag]
        s = np.std(d)
        tau.append(s if s > 0 else np.nan)
    tau = np.array(tau)
    ok = np.isfinite(tau) & (tau > 0)
    if ok.sum() < 5:
        return np.nan
    return float(np.polyfit(np.log(lags[ok]), np.log(tau[ok]), 1)[0])


def rolling_hurst(close: pd.Series, window: int = 100, max_lag: int = 20) -> pd.Series:
    vals = np.full(len(close), np.nan)
    arr = close.values
    for i in range(window, len(close)):
        vals[i] = hurst(arr[i - window:i], max_lag=max_lag)
    return pd.Series(vals, index=close.index)


def main():
    df = load_gld()
    print(f"GLD daily: {len(df)} bars  {df.index.min().date()} → {df.index.max().date()}")
    close = df["close"]
    ret = close.pct_change()

    for window in (60, 100, 150):
        H = rolling_hurst(close, window=window)
        valid = H.dropna()
        print(f"\n=== window={window}: Hurst distribution ===")
        print(f"  mean={valid.mean():.3f}  std={valid.std():.3f}  "
              f"frac>0.5={np.mean(valid>0.5):.2f}  "
              f"p10={valid.quantile(.1):.3f}  p90={valid.quantile(.9):.3f}")

        # PREMISE TEST: forward 5-day return autocorrelation, bucketed by H.
        # momentum signal = sign of trailing 5d return; does it predict fwd 5d?
        trail = close.pct_change(5)
        fwd = close.shift(-5) / close - 1.0
        d = pd.DataFrame({"H": H, "trail": trail, "fwd": fwd}).dropna()
        hi = d[d.H > d.H.quantile(0.66)]
        lo = d[d.H < d.H.quantile(0.34)]
        for name, b in [("HIGH-H (expect momentum)", hi),
                        ("LOW-H  (expect reversion)", lo)]:
            # correlation of trailing vs forward return = persistence sign
            corr = b["trail"].corr(b["fwd"])
            # momentum P&L: follow trailing sign
            mom = np.sign(b["trail"]) * b["fwd"]
            t = mom.mean() / (mom.std() / np.sqrt(len(mom))) if len(mom) > 2 else np.nan
            print(f"  {name}: n={len(b)} corr(trail,fwd)={corr:+.3f} "
                  f"momentum_t={t:+.2f} mean_fwd_mom={mom.mean()*1e4:+.1f}bps")


if __name__ == "__main__":
    main()
