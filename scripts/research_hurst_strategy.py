"""
research_hurst_strategy.py — Stage 2: proper Hurst estimator + drift-controlled
strategy test on daily gold.

Stage 1 (structure-function Hurst) refuted the textbook premise and hinted the
relationship is inverted, but the estimator looked biased (mean H ~0.4). Here:

  1. R/S (rescaled-range, classic Mandelbrot) AND DFA Hurst estimators — verify
     the H values are sane and agree.
  2. Premise test at multiple forward horizons (5/10/20d), bucketed by H.
  3. Direct strategy backtests with daily rebalance, costs, IS/OOS split, and —
     critically — a BUY-AND-HOLD benchmark plus a DRIFT CONTROL: re-run every
     return series demeaned (subtract the average daily gold return) so we can
     see whether any 'edge' survives once gold's bull drift is removed.

A strategy only counts if it beats buy-and-hold on Sharpe AND keeps a positive
Sharpe after demeaning (i.e. it is timing, not just being long a bull market).
"""
import numpy as np
import pandas as pd
from research_hurst_gold import load_gld

COST_BPS = 2.0  # round-trip cost per rebalance (bps of notional); gold spot is tight


# ── Hurst estimators ─────────────────────────────────────────────────────────
def hurst_rs(ts: np.ndarray) -> float:
    """Classic rescaled-range (R/S) Hurst on a series. ts = price levels."""
    ts = np.asarray(ts, float)
    N = len(ts)
    if N < 20:
        return np.nan
    # Use log-returns for R/S (standard).
    x = np.diff(np.log(ts))
    n = len(x)
    # average R/S over a few sub-series sizes
    sizes = [s for s in (8, 16, 32, 64, 128) if s <= n]
    if len(sizes) < 3:
        return np.nan
    rs_means = []
    for s in sizes:
        chunks = n // s
        rs_vals = []
        for k in range(chunks):
            seg = x[k * s:(k + 1) * s]
            z = seg - seg.mean()
            Z = np.cumsum(z)
            R = Z.max() - Z.min()
            S = seg.std()
            if S > 0:
                rs_vals.append(R / S)
        if rs_vals:
            rs_means.append((s, np.mean(rs_vals)))
    if len(rs_means) < 3:
        return np.nan
    ls = np.log([s for s, _ in rs_means])
    lr = np.log([v for _, v in rs_means])
    return float(np.polyfit(ls, lr, 1)[0])


def hurst_dfa(ts: np.ndarray) -> float:
    """Detrended Fluctuation Analysis Hurst on log-returns integrated profile."""
    ts = np.asarray(ts, float)
    if len(ts) < 20:
        return np.nan
    x = np.diff(np.log(ts))
    y = np.cumsum(x - x.mean())
    n = len(y)
    sizes = [s for s in (8, 16, 32, 64) if s <= n // 2]
    if len(sizes) < 3:
        return np.nan
    F = []
    for s in sizes:
        chunks = n // s
        errs = []
        for k in range(chunks):
            seg = y[k * s:(k + 1) * s]
            t = np.arange(s)
            coef = np.polyfit(t, seg, 1)
            fit = np.polyval(coef, t)
            errs.append(np.mean((seg - fit) ** 2))
        F.append((s, np.sqrt(np.mean(errs))))
    ls = np.log([s for s, _ in F])
    lf = np.log([v for _, v in F if v > 0] or [np.nan])
    if len(lf) != len(ls):
        return np.nan
    return float(np.polyfit(ls, lf, 1)[0])


def rolling_h(close: pd.Series, window: int, fn) -> pd.Series:
    vals = np.full(len(close), np.nan)
    arr = close.values
    for i in range(window, len(close)):
        vals[i] = fn(arr[i - window:i])
    return pd.Series(vals, index=close.index)


def sharpe(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return np.nan
    return float(r.mean() / r.std() * np.sqrt(252))


def report(name: str, strat_ret: pd.Series, bh: pd.Series):
    s = sharpe(strat_ret)
    tot = (1 + strat_ret.fillna(0)).prod() - 1
    # demeaned (drift control): remove average gold daily return from the
    # underlying, keep the same positions
    print(f"  {name:<34} Sharpe={s:+.2f}  totRet={tot*100:+7.1f}%  "
          f"days_in_mkt={ (strat_ret!=0).mean()*100:4.0f}%")
    return s


def main():
    df = load_gld()
    close = df["close"]
    ret = close.pct_change()
    bh_sharpe = sharpe(ret)
    print(f"GLD daily {len(df)} bars {df.index.min().date()}→{df.index.max().date()}")
    print(f"BUY-AND-HOLD: Sharpe={bh_sharpe:+.2f}  "
          f"totRet={((1+ret.fillna(0)).prod()-1)*100:+.0f}%\n")

    # 1. Estimator sanity at window 120
    W = 120
    H_rs = rolling_h(close, W, hurst_rs)
    H_dfa = rolling_h(close, W, hurst_dfa)
    print(f"=== Hurst estimator sanity (window {W}) ===")
    for nm, H in [("R/S", H_rs), ("DFA", H_dfa)]:
        v = H.dropna()
        print(f"  {nm}: mean={v.mean():.3f} std={v.std():.3f} "
              f"frac>0.5={np.mean(v>0.5):.2f} p10={v.quantile(.1):.2f} p90={v.quantile(.9):.2f}")
    print(f"  corr(R/S, DFA) = {H_rs.corr(H_dfa):+.2f}")

    # 2. Premise test at horizons, using R/S Hurst
    print(f"\n=== Premise: forward-return momentum by Hurst bucket (R/S, W={W}) ===")
    for horizon in (5, 10, 20):
        trail = close.pct_change(horizon)
        fwd = close.shift(-horizon) / close - 1.0
        d = pd.DataFrame({"H": H_rs, "trail": trail, "fwd": fwd}).dropna()
        hi = d[d.H > d.H.quantile(0.66)]
        lo = d[d.H < d.H.quantile(0.34)]
        def mt(b):
            m = np.sign(b.trail) * b.fwd
            return m.mean() / (m.std() / np.sqrt(len(m))) if len(m) > 2 else np.nan
        print(f"  h={horizon:>2}d: HIGH-H mom_t={mt(hi):+.2f}  LOW-H mom_t={mt(lo):+.2f}")

    # 3. Strategies (daily rebalance, R/S Hurst W=120, momentum lookback=20)
    print(f"\n=== Strategies (daily, R/S Hurst, drift control) ===")
    lookback = 20
    mom_dir = np.sign(close.pct_change(lookback))         # trend direction
    ma = close.rolling(lookback).mean()
    z = (close - ma) / close.rolling(lookback).std()       # reversion z
    H = H_rs
    fwd1 = close.shift(-1) / close - 1.0                    # next-day return

    def pnl(position: pd.Series, label: str):
        pos = position.shift(1).fillna(0)                  # act next day (no lookahead)
        turn = pos.diff().abs().fillna(0)
        gross = pos * fwd1
        net = gross - turn * (COST_BPS / 1e4)
        report(label, net, ret)
        return net

    # Textbook: H>0.5 → momentum (follow trend); H<0.5 → reversion (fade z)
    textbook = pd.Series(0.0, index=close.index)
    textbook[H > 0.5] = mom_dir[H > 0.5]
    textbook[H < 0.5] = -np.sign(z)[H < 0.5]
    # Inverted: H<0.5 → momentum; H>0.5 → reversion
    inverted = pd.Series(0.0, index=close.index)
    inverted[H < 0.5] = mom_dir[H < 0.5]
    inverted[H > 0.5] = -np.sign(z)[H > 0.5]

    n_tb = pnl(textbook, "Textbook (H>.5 mom / H<.5 rev)")
    n_iv = pnl(inverted, "Inverted (H<.5 mom / H>.5 rev)")
    n_mo = pnl(mom_dir, "Plain 20d momentum (no Hurst)")
    n_lo = pnl(pd.Series(1.0, index=close.index), "Always-long (=buy&hold w/cost)")

    # DRIFT CONTROL: redo best two on demeaned returns (remove bull drift)
    print("\n  --- drift control: same positions on DEMEANED gold returns ---")
    fwd1_dm = fwd1 - fwd1.mean()
    for pos_series, lab in [(textbook, "Textbook (demeaned)"),
                            (inverted, "Inverted (demeaned)"),
                            (mom_dir, "Plain momentum (demeaned)")]:
        pos = pos_series.shift(1).fillna(0)
        turn = pos.diff().abs().fillna(0)
        net = pos * fwd1_dm - turn * (COST_BPS / 1e4)
        report(lab, net, ret)

    # IS/OOS on the better of textbook/inverted
    print("\n  --- IS/OOS (70/30) for both ---")
    cut = int(len(close) * 0.7)
    for pos_series, lab in [(textbook, "Textbook"), (inverted, "Inverted")]:
        pos = pos_series.shift(1).fillna(0)
        net = pos * fwd1 - pos.diff().abs().fillna(0) * (COST_BPS / 1e4)
        print(f"    {lab:<10} IS Sharpe={sharpe(net.iloc[:cut]):+.2f}  "
              f"OOS Sharpe={sharpe(net.iloc[cut:]):+.2f}")


if __name__ == "__main__":
    main()
