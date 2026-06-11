"""
research_vol_targeting.py — Applied math: volatility is forecastable even when
direction is not. Does vol-targeting improve risk-adjusted gold returns?

Premise gates:
  1. Vol clustering — is |return| autocorrelated? (ARCH effect, Engle 1982.)
  2. Forecast skill — does an EWMA / GARCH(1,1) variance forecast predict next-day
     realized variance OUT OF SAMPLE (correlation, vs a naive constant)?
Then the strategy test:
  3. Vol-targeting overlay — scale exposure to (target_vol / forecast_vol), capped.
     Compare Sharpe / maxDD vs STATIC sizing, on (a) always-long gold and
     (b) a 50/200 time-series-momentum signal. Edge = higher Sharpe / lower DD,
     not higher raw return.

Honest controls: report static vs targeted side by side; vol-targeting that only
raises return by levering up is not an edge — we want better Sharpe at similar vol.
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.research_hurst_gold import load_gld  # 20y GLD daily, cached

ANNUAL = 252


def sharpe(r):
    r = pd.Series(r).dropna()
    return np.nan if (len(r) < 30 or r.std() == 0) else r.mean() / r.std() * np.sqrt(ANNUAL)


def max_dd(r):
    eq = (1 + pd.Series(r).fillna(0)).cumprod()
    return float((eq / eq.cummax() - 1).min())


def ewma_var(ret, lam=0.94):
    """RiskMetrics EWMA variance, causal (uses only past)."""
    v = np.zeros(len(ret))
    v[0] = ret.iloc[:20].var()
    r = ret.values
    for t in range(1, len(r)):
        v[t] = lam * v[t - 1] + (1 - lam) * (r[t - 1] ** 2)  # forecast for day t
    return pd.Series(v, index=ret.index)


def garch11(ret, n_iter=200):
    """Tiny GARCH(1,1) by grid/coordinate search on (omega,alpha,beta).
    Returns causal one-step variance forecast series. Lightweight, no statsmodels."""
    r = ret.dropna().values
    var_uncond = r.var()
    best = None
    # coarse search over persistence structure
    for alpha in (0.03, 0.05, 0.08, 0.10, 0.15):
        for beta in (0.80, 0.85, 0.90, 0.93):
            if alpha + beta >= 0.999:
                continue
            omega = var_uncond * (1 - alpha - beta)
            v = np.zeros(len(r))
            v[0] = var_uncond
            ll = 0.0
            for t in range(1, len(r)):
                v[t] = omega + alpha * r[t - 1] ** 2 + beta * v[t - 1]
                ll += -0.5 * (np.log(v[t]) + r[t] ** 2 / v[t])
            if best is None or ll > best[0]:
                best = (ll, alpha, beta, omega)
    _, alpha, beta, omega = best
    # produce full causal forecast on the original (NaN-padded) index
    full = np.full(len(ret), np.nan)
    rr = ret.values
    idx0 = np.argmax(~np.isnan(rr))
    v_prev = var_uncond
    for t in range(idx0 + 1, len(ret)):
        rt1 = rr[t - 1] if not np.isnan(rr[t - 1]) else 0.0
        v_prev = omega + alpha * rt1 ** 2 + beta * v_prev
        full[t] = v_prev
    return pd.Series(full, index=ret.index), (alpha, beta)


def main():
    df = load_gld()
    close = df["close"]
    ret = close.pct_change().dropna()
    print(f"GLD daily {len(ret)} returns {ret.index.min().date()}→{ret.index.max().date()}")
    print(f"Buy&hold: Sharpe={sharpe(ret):+.2f}  maxDD={max_dd(ret)*100:.0f}%\n")

    # 1. ARCH effect: autocorr of |ret| and ret
    abs_ac = [ret.abs().autocorr(k) for k in (1, 5, 10, 20)]
    raw_ac = [ret.autocorr(k) for k in (1, 5, 10, 20)]
    print("=== 1. Vol clustering (autocorrelation) ===")
    print(f"  |ret| autocorr  lag1/5/10/20: {abs_ac[0]:+.3f} {abs_ac[1]:+.3f} {abs_ac[2]:+.3f} {abs_ac[3]:+.3f}")
    print(f"   ret  autocorr  lag1/5/10/20: {raw_ac[0]:+.3f} {raw_ac[1]:+.3f} {raw_ac[2]:+.3f} {raw_ac[3]:+.3f}")
    print("  (|ret| strongly autocorrelated + ret ~0 ⇒ vol forecastable, direction not)")

    # 2. Forecast skill: EWMA & GARCH variance vs next-day realized var (ret^2)
    realized_next = (ret.shift(-1) ** 2)
    ewma = ewma_var(ret)
    garch, (a, b) = garch11(ret)
    cut = int(len(ret) * 0.7)
    print(f"\n=== 2. Variance forecast skill (corr with next-day ret²) ===")
    for name, f in [("EWMA(0.94)", ewma), ("GARCH(1,1)", garch)]:
        d = pd.DataFrame({"f": f, "y": realized_next}).dropna()
        c_all = d["f"].corr(d["y"])
        c_oos = d["f"].iloc[cut:].corr(d["y"].iloc[cut:])
        print(f"  {name:<12} corr ALL={c_all:+.3f}  OOS={c_oos:+.3f}")
    print(f"  (GARCH fit: alpha={a}, beta={b}, persistence={a+b:.2f})")

    # 3. Vol-targeting overlay
    print(f"\n=== 3. Vol-targeting vs static (target 10% annual vol, lev cap 3x) ===")
    fvol = np.sqrt(garch * ANNUAL)             # annualized forecast vol, causal
    target = 0.10
    scale = (target / fvol).clip(upper=3.0).shift(1)  # size set yesterday → no lookahead

    # signals: always-long, and 50/200 TSMOM (long if above 200d MA)
    long_sig = pd.Series(1.0, index=ret.index)
    tsmom = (close > close.rolling(200).mean()).astype(float).reindex(ret.index).fillna(0)

    cost = 4.0 / 1e4  # round-trip per unit turnover
    def run(sig, scl, label):
        pos = (sig * scl) if scl is not None else sig
        pos = pos.fillna(0)
        turn = pos.diff().abs().fillna(0)
        r = pos.shift(0) * ret - turn * cost
        # pos already shifted via scale.shift(1); for static, shift sig
        return r
    # static uses yesterday's signal
    for sig, name in [(long_sig, "Always-long"), (tsmom, "TSMOM 200d")]:
        stat_pos = sig.shift(1).fillna(0)
        stat = stat_pos * ret - stat_pos.diff().abs().fillna(0) * cost
        targ_pos = (sig.shift(1) * scale).fillna(0)
        targ = targ_pos * ret - targ_pos.diff().abs().fillna(0) * cost
        print(f"  {name}:")
        print(f"    static   Sharpe={sharpe(stat):+.2f}  maxDD={max_dd(stat)*100:5.0f}%  "
              f"annVol={stat.std()*np.sqrt(ANNUAL)*100:4.1f}%  OOS_Sharpe={sharpe(stat.iloc[cut:]):+.2f}")
        print(f"    vol-tgt  Sharpe={sharpe(targ):+.2f}  maxDD={max_dd(targ)*100:5.0f}%  "
              f"annVol={targ.std()*np.sqrt(ANNUAL)*100:4.1f}%  OOS_Sharpe={sharpe(targ.iloc[cut:]):+.2f}")


if __name__ == "__main__":
    main()
