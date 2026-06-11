"""
research_voltarget_kalman.py — Does forecast-vol position scaling reduce Kalman's
drawdown on its ACTUAL XAUUSD trade series?

The GLD proxy said a GARCH/EWMA overlay adds ~5pts of max-DD reduction over the
ATR sizing Kalman already uses. This tests it on Kalman's real backtested trades.

Method (honest, drift/lever-controlled):
  - Each trade's pnl scales linearly with position size.
  - Compute an EWMA forecast of daily XAUUSD vol at each trade's entry date.
  - vol_scalar = clip(target_vol / forecast_vol, lo, hi).
  - NORMALIZE scalars to mean 1 over the sample → pure REDISTRIBUTION of risk
    across time (more in calm, less in turbulent), NOT an overall de-lever.
  - Compare baseline vs reweighted equity: max drawdown, total pnl, trade Sharpe.
A real overlay cuts max DD at ~equal total pnl.
"""
import numpy as np
import pandas as pd

TRADES = "data/backtests/backtest_result_kalman_regime_trades.csv"
PRICE = "data/historical/XAUUSD_5m_real.csv"


def daily_ewma_vol(lam=0.94):
    px = pd.read_csv(PRICE, parse_dates=["timestamp"])
    px["ts"] = pd.to_datetime(px["timestamp"], utc=True)
    daily = px.set_index("ts")["close"].resample("1D").last().dropna()
    ret = daily.pct_change().dropna()
    v = np.zeros(len(ret))
    v[0] = ret.iloc[:20].var()
    r = ret.values
    for t in range(1, len(r)):
        v[t] = lam * v[t - 1] + (1 - lam) * r[t - 1] ** 2   # causal forecast for day t
    fvol = pd.Series(np.sqrt(v * 252), index=ret.index)      # annualized
    return fvol


def max_dd(equity):
    peak = np.maximum.accumulate(equity)
    return float((equity - peak).min())


def stats(pnl):
    pnl = np.asarray(pnl, dtype=float)
    eq = np.cumsum(pnl)
    dd = max_dd(eq)
    sharpe = pnl.mean() / pnl.std() * np.sqrt(252) if pnl.std() > 0 else np.nan
    return eq[-1], dd, sharpe


def main():
    tr = pd.read_csv(TRADES, parse_dates=["timestamp"])
    tr["date"] = pd.to_datetime(tr["timestamp"], utc=True).dt.normalize()
    fvol = daily_ewma_vol()
    fvol.index = fvol.index.normalize()
    # map each trade to forecast vol at its entry date (nearest prior available)
    fv = fvol.reindex(fvol.index.union(tr["date"].unique())).ffill()
    tr["fvol"] = tr["date"].map(fv)
    tr = tr.dropna(subset=["fvol", "pnl"]).reset_index(drop=True)
    print(f"Kalman trades with vol mapped: {len(tr)}  "
          f"forecast-vol range {tr.fvol.min():.2f}–{tr.fvol.max():.2f} (median {tr.fvol.median():.2f})")

    base_pnl = tr["pnl"].values
    e0, dd0, sh0 = stats(pd.Series(base_pnl))
    print(f"\nBASELINE: totPnl=${e0:,.0f}  maxDD=${dd0:,.0f}  tradeSharpe={sh0:+.2f}")

    target = float(np.median(tr["fvol"]))  # target = median forecast vol (neutral)
    print(f"\n{'lo–hi cap':>10} {'totPnl':>12} {'maxDD':>12} {'DD vs base':>11} {'Sharpe':>7}")
    for lo, hi in [(0.7, 1.5), (0.5, 2.0), (0.5, 1.5), (0.33, 3.0)]:
        scalar = np.clip(target / tr["fvol"].values, lo, hi)
        scalar = scalar / scalar.mean()        # normalize → mean 1 (no net lever)
        rw = base_pnl * scalar
        e, dd, sh = stats(pd.Series(rw))
        print(f"{lo:.2f}–{hi:.2f}  ${e:>10,.0f}  ${dd:>10,.0f}  "
              f"{(dd-dd0)/abs(dd0)*100:>+9.1f}%  {sh:>+6.2f}")
    print("\n(DD vs base < 0 = drawdown reduced; want lower DD at ~equal totPnl)")


if __name__ == "__main__":
    main()
