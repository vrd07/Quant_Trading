"""
research_pairs_ou.py — OU mean-reversion on the gold/silver spread (stat-arb).

Physics: Ornstein-Uhlenbeck / Langevin — a particle in a potential well.
  dS = -theta (S - mu) dt + sigma dW
The restoring force exists ONLY if S is stationary. Single gold price is NOT
stationary (it trends), but a cointegrated SPREAD of gold vs silver can be.

Premise gates (must pass IN-SAMPLE *and* hold OOS or we stop):
  1. Cointegration: regress log(GLD) on log(SLV); residual = spread. ADF test
     (implemented here, no statsmodels) must reject unit root.
  2. OU half-life = ln(2)/theta from AR(1) on the spread must be short enough
     to trade (days-to-weeks, not years).
Then: z-score strategy, market-neutral (long gold / short silver at hedge ratio),
daily rebalance, costs, IS/OOS, vs a do-nothing benchmark. Market-neutral by
construction strips out gold's bull-drift — the control single-asset tests failed.
"""
import sys
import json
import urllib.request
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "historical"
COST_BPS = 4.0  # round-trip per leg per rebalance (two legs → applied to turnover)


def yf_daily(sym: str) -> pd.Series:
    cache = CACHE / f"{sym}_yf_daily.csv"
    if cache.exists():
        s = pd.read_csv(cache, parse_dates=["date"]).set_index("date")["close"]
        return s
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=0&period2=9999999999&interval=1d")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    j = json.load(urllib.request.urlopen(req, timeout=40))
    r = j["chart"]["result"][0]
    ts = r["timestamp"]
    close = r["indicators"]["quote"][0]["close"]
    idx = pd.to_datetime([dt.date.fromtimestamp(t) for t in ts])
    s = pd.Series(close, index=idx, name="close").dropna()
    s = s[~s.index.duplicated(keep="last")]
    s.rename_axis("date").to_frame().to_csv(cache)
    print(f"  cached {len(s)} daily {sym} → {cache.name}")
    return s


def adf(series: np.ndarray, max_lag: int = 1) -> float:
    """Augmented Dickey-Fuller t-stat on the lagged-level coefficient.
    More negative = more stationary. Critical ~ -2.86 (5%), -3.43 (1%)."""
    y = np.asarray(series, float)
    dy = np.diff(y)
    lag_y = y[:-1]
    X = [lag_y]
    for k in range(1, max_lag + 1):
        X.append(np.concatenate([np.full(k, 0.0), dy[:-k]])[:len(dy)])
    X.append(np.ones(len(dy)))
    X = np.column_stack(X)
    beta, *_ = np.linalg.lstsq(X, dy, rcond=None)
    resid = dy - X @ beta
    dof = len(dy) - X.shape[1]
    s2 = (resid @ resid) / dof
    cov = s2 * np.linalg.inv(X.T @ X)
    return float(beta[0] / np.sqrt(cov[0, 0]))


def ou_half_life(spread: np.ndarray) -> float:
    """AR(1): Δs = a + b·s_{t-1}; theta=-b; half-life=ln2/theta (in days)."""
    s = np.asarray(spread, float)
    ds = np.diff(s)
    lag = s[:-1]
    X = np.column_stack([lag, np.ones(len(lag))])
    beta, *_ = np.linalg.lstsq(X, ds, rcond=None)
    b = beta[0]
    if b >= 0:
        return np.inf
    return float(np.log(2) / -b)


def hedge_ratio(logA, logB):
    """OLS log(A) = alpha + beta*log(B); return (alpha, beta)."""
    X = np.column_stack([logB, np.ones(len(logB))])
    beta, *_ = np.linalg.lstsq(X, logA, rcond=None)
    return beta[1], beta[0]  # alpha, beta


def sharpe(r):
    r = np.asarray(r, float)
    r = r[~np.isnan(r)]
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return r.mean() / r.std() * np.sqrt(252)


def main():
    gld = yf_daily("GLD")
    slv = yf_daily("SLV")
    df = pd.concat([gld.rename("GLD"), slv.rename("SLV")], axis=1).dropna()
    print(f"Aligned GLD/SLV: {len(df)} days  {df.index.min().date()} → {df.index.max().date()}")
    logA, logB = np.log(df["GLD"].values), np.log(df["SLV"].values)

    # IS/OOS split for honesty
    cut = int(len(df) * 0.7)

    # 1. Cointegration on IN-SAMPLE only (no lookahead on hedge ratio)
    alpha, beta = hedge_ratio(logA[:cut], logB[:cut])
    spread_full = logA - (alpha + beta * logB)
    print(f"\nHedge ratio (IS): log(GLD) = {alpha:+.3f} + {beta:.3f}·log(SLV)")
    print(f"ADF on spread:  IS t={adf(spread_full[:cut]):+.2f}  "
          f"OOS t={adf(spread_full[cut:]):+.2f}   (reject unit root < -2.86)")
    print(f"OU half-life:   IS={ou_half_life(spread_full[:cut]):.1f}d  "
          f"OOS={ou_half_life(spread_full[cut:]):.1f}d")

    # gold/silver RATIO spread (simpler, beta=1) as a robustness cross-check
    ratio = np.log(df["GLD"].values) - np.log(df["SLV"].values)
    print(f"\nLog-ratio (GLD/SLV) cross-check: ADF IS t={adf(ratio[:cut]):+.2f} "
          f"OOS t={adf(ratio[cut:]):+.2f}  half-life IS={ou_half_life(ratio[:cut]):.1f}d")

    # 2. Strategy on the regression spread: z-score from a rolling window
    for zwin in (20, 40, 60):
        s = pd.Series(spread_full, index=df.index)
        mu = s.rolling(zwin).mean()
        sd = s.rolling(zwin).std()
        z = (s - mu) / sd
        # Position in the SPREAD: short spread when z high (spread too wide),
        # long when z low. entry |z|>2, exit |z|<0.5. Hold between.
        pos = pd.Series(np.nan, index=df.index)
        pos[z > 2.0] = -1.0
        pos[z < -2.0] = 1.0
        pos[z.abs() < 0.5] = 0.0
        pos = pos.ffill().fillna(0.0)
        # spread daily return ≈ pos * dSpread (spread is in log units → ~return of
        # the market-neutral book: long GLD, short beta·SLV)
        dspread = s.diff()
        gross = pos.shift(1) * dspread
        turn = pos.shift(1).diff().abs().fillna(0)
        net = gross - turn * (COST_BPS / 1e4) * (1 + abs(beta))
        tot = net.sum()
        print(f"\n  z-win={zwin}: trades≈{int(turn.gt(0).sum())}  "
              f"Sharpe ALL={sharpe(net):+.2f}  IS={sharpe(net.iloc[:cut]):+.2f}  "
              f"OOS={sharpe(net.iloc[cut:]):+.2f}  totLogPnl={tot:+.3f}")


if __name__ == "__main__":
    main()
